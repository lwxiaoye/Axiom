"""Single-step workflow debugging without re-running completed nodes.

The normal workflow runner deliberately executes each ready batch concurrently.  A
debugger needs a different contract: execute exactly one ready node, retain the
runtime context, then pause before the next node.  This module owns that small
state machine and reuses :class:`WorkflowEngine` for the actual node execution,
so debug and production runs have identical node semantics.

Sessions are intentionally process-local.  Replaying a partially completed
workflow after an API restart could duplicate HTTP, tool, or code side effects;
callers receive a clear expiration error instead.  Durable cross-process resume
requires a separate runtime-schema migration and an idempotency contract for
side-effecting nodes.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.workflows.workflow_engine import (
    EXECUTOR_METHODS,
    MAX_NODE_RUN_TIMES,
    MAX_STEPS,
    NODE_WORKFLOW_START,
    RunContext,
    WorkflowEngine,
    WorkflowExecutionError,
)


DEBUG_SESSION_TTL_SECONDS = 15 * 60


class DebugSessionNotFoundError(LookupError):
    """The session was stopped, expired, or belongs to another API worker."""


class DebugSessionForbiddenError(PermissionError):
    """Debug sessions are private to the editor that created them."""


class DebugSessionExpiredError(DebugSessionNotFoundError):
    """The session reached its inactivity limit and was discarded."""


class DebugSessionUnsupportedGraphError(ValueError):
    """The graph needs an execution kernel that cannot pause one node at a time."""


@dataclass
class WorkflowDebugSession:
    """One paused workflow execution.

    ``active_queue`` and ``skip_queue`` mirror ``WorkflowEngine.run``.  The
    only intentional semantic difference is serialising ready nodes for human
    inspection; normal production execution remains concurrent.
    """

    engine: WorkflowEngine
    owner_user_id: str
    app_id: str
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "paused"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    active_queue: list[str] = field(default_factory=list)
    skip_queue: list[str] = field(default_factory=list)
    skip_seen: set[str] = field(default_factory=set)
    run_counts: dict[str, int] = field(default_factory=dict)
    total_runs: int = 0
    skip_budget: int = MAX_STEPS * 10
    current_node_id: Optional[str] = None
    next_node_id: Optional[str] = None
    error_message: str = ""
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @classmethod
    def create(cls, graph: dict, ctx: RunContext, *, owner_user_id: str, app_id: str) -> "WorkflowDebugSession":
        unsupported = sorted({
            str(node.get("flowNodeType") or "")
            for node in graph.get("nodes") or []
            if str(node.get("flowNodeType") or "") not in EXECUTOR_METHODS
        })
        if unsupported:
            names = "、".join(unsupported)
            raise DebugSessionUnsupportedGraphError(
                f"单步调试暂不支持这些节点：{names}。请使用全流程调试。"
            )

        engine = WorkflowEngine(graph, ctx)
        starts = [node for node in engine.nodes.values() if node.get("flowNodeType") == NODE_WORKFLOW_START]
        if not starts:
            raise WorkflowExecutionError("工作流缺少「流程开始」节点")

        by_source: dict[str, list[dict]] = {}
        by_target: dict[str, list[dict]] = {}
        for edge in engine.edges:
            # selectedTools is a tool attachment, not a scheduling edge.
            if edge.get("sourceHandle") == "selectedTools":
                continue
            by_source.setdefault(edge["source"], []).append(edge)
            by_target.setdefault(edge["target"], []).append(edge)
        engine._by_target = by_target
        engine._back_edge_ids = engine._compute_back_edges(starts[0]["nodeId"], by_source)

        session = cls(engine=engine, owner_user_id=owner_user_id, app_id=app_id)
        session._by_source = by_source
        session._by_target = by_target
        session.active_queue = [starts[0]["nodeId"]]
        session.next_node_id = starts[0]["nodeId"]
        return session

    def is_expired(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) - self.updated_at > DEBUG_SESSION_TTL_SECONDS

    async def step(self, variables: Optional[dict[str, Any]] = None) -> dict:
        """Optionally patch variables, execute one ready node, and pause again."""
        async with self._lock:
            if self.status != "paused":
                return self.snapshot()
            if variables:
                self.engine.ctx.variables.update(variables)

            try:
                node_id = self._take_next_runnable_node()
                if node_id is None:
                    self.status = "completed"
                    self.updated_at = time.time()
                    return self.snapshot()

                self.current_node_id = node_id
                self.run_counts[node_id] = self.run_counts.get(node_id, 0) + 1
                if self.run_counts[node_id] > MAX_NODE_RUN_TIMES:
                    raise WorkflowExecutionError(
                        f"节点 {self.engine.nodes[node_id].get('name') or node_id} "
                        f"执行次数超过上限 {MAX_NODE_RUN_TIMES}"
                    )
                self.total_runs += 1
                if self.total_runs > MAX_STEPS:
                    raise WorkflowExecutionError(f"执行步数超过上限 {MAX_STEPS}，请检查是否存在循环连线")

                self.skip_seen.discard(node_id)
                for edge in self._by_target.get(node_id, []):
                    edge["status"] = "waiting"

                await self.engine._execute_node(node_id)
                for edge in self._by_source.get(node_id, []):
                    if edge["status"] == "active":
                        self.active_queue.append(edge["target"])
                    else:
                        self.skip_queue.append(edge["target"])
                self._pause_or_complete()
            except Exception as exc:  # A node's business exception is already captured by _execute_node.
                self.status = "failed"
                self.error_message = str(exc)
            self.updated_at = time.time()
            return self.snapshot()

    def _pause_or_complete(self) -> None:
        """Resolve skip-only paths now so the final node completes immediately."""
        next_node_id = self._take_next_runnable_node()
        if next_node_id is not None:
            # _take_next_runnable_node consumes the ready item.  Put it back so
            # the next request is still the only request that executes it.
            self.active_queue.insert(0, next_node_id)
            self.next_node_id = next_node_id
            return
        self.next_node_id = None
        output = "".join(text for _seq, text in self.engine.ctx.output_parts if text)
        if self.engine.ctx.uncaught_errors and not output:
            self.status = "failed"
            self.error_message = "；".join(self.engine.ctx.uncaught_errors[:3])
        else:
            self.status = "completed"

    async def stop(self) -> dict:
        async with self._lock:
            self.status = "stopped"
            self.updated_at = time.time()
            return self.snapshot()

    def _take_next_runnable_node(self) -> Optional[str]:
        """Advance scheduling bookkeeping until exactly one node is ready."""
        while self.active_queue or self.skip_queue:
            if self.active_queue:
                node_id = self.active_queue.pop(0)
                state = self.engine._node_state(node_id)
                if state == "run":
                    return node_id
                if state == "skip":
                    self.skip_queue.append(node_id)
                continue

            self.skip_budget -= 1
            if self.skip_budget <= 0:
                raise WorkflowExecutionError("跳过传播超出调度预算，请检查连线是否成环")
            node_id = self.skip_queue.pop(0)
            if node_id in self.skip_seen:
                continue
            state = self.engine._node_state(node_id)
            if state == "skip":
                self.skip_seen.add(node_id)
                self.engine._mark_out_edges(node_id, {"__all__": "skipped"})
                self.skip_queue.extend(edge["target"] for edge in self._by_source.get(node_id, []))
            elif state == "run":
                self.active_queue.append(node_id)
        return None

    def snapshot(self) -> dict:
        ctx = self.engine.ctx
        output = "".join(text for _seq, text in sorted(ctx.output_parts, key=lambda part: part[0]) if text)
        last_run = ctx.node_runs[-1].to_dict() if ctx.node_runs else None
        return {
            "sessionId": self.session_id,
            "runId": ctx.run_id,
            "status": self.status,
            "currentNodeId": self.current_node_id,
            "nextNodeId": self.next_node_id,
            "output": output,
            "errorMessage": self.error_message or None,
            "nodeRuns": [run.to_dict() for run in ctx.node_runs],
            "lastNodeRun": last_run,
            "outputs": ctx.outputs,
            "variables": ctx.variables,
            "edges": self.engine.edges_snapshot(),
            "uncaughtErrors": list(ctx.uncaught_errors),
            "createdAt": int(self.created_at * 1000),
            "updatedAt": int(self.updated_at * 1000),
            "expiresAt": int((self.updated_at + DEBUG_SESSION_TTL_SECONDS) * 1000),
        }


class WorkflowDebugSessionStore:
    """Small, ownership-aware registry for live debugger sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, WorkflowDebugSession] = {}
        self._lock = asyncio.Lock()

    async def create(self, session: WorkflowDebugSession) -> WorkflowDebugSession:
        async with self._lock:
            self._purge_expired_locked()
            self._sessions[session.session_id] = session
        return session

    async def get(self, session_id: str, owner_user_id: str) -> WorkflowDebugSession:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise DebugSessionNotFoundError()
            if session.owner_user_id != owner_user_id:
                raise DebugSessionForbiddenError()
            if session.is_expired():
                self._sessions.pop(session_id, None)
                raise DebugSessionExpiredError()
            return session

    async def stop(self, session_id: str, owner_user_id: str) -> dict:
        session = await self.get(session_id, owner_user_id)
        snapshot = await session.stop()
        async with self._lock:
            self._sessions.pop(session_id, None)
        return snapshot

    def _purge_expired_locked(self) -> None:
        now = time.time()
        expired = [key for key, value in self._sessions.items() if value.is_expired(now)]
        for key in expired:
            self._sessions.pop(key, None)


workflow_debug_sessions = WorkflowDebugSessionStore()
