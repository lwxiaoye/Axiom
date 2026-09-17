"""Tool registry and resource-aware dispatcher driven only by ToolSpec metadata."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Awaitable, Callable

from .contracts import (
    ObservationStatus,
    RunSnapshot,
    ToolCallContext,
    ToolObservation,
    ToolRequest,
    ToolSpec,
)
from .policy import HarnessPolicy


ToolExecutor = Callable[[dict, ToolCallContext], Awaitable[ToolObservation]]


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    execute: ToolExecutor


class ToolRegistry:
    def __init__(self, tools: tuple[RegisteredTool, ...] = ()) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: RegisteredTool) -> None:
        if tool.spec.name in self._tools:
            raise ValueError(f"duplicate Harness tool: {tool.spec.name}")
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(item.spec for item in self._tools.values())


class ToolDispatcher:
    def __init__(self, registry: ToolRegistry, policy: HarnessPolicy | None = None) -> None:
        self._registry = registry
        self._policy = policy or HarnessPolicy()

    def visible_specs(self, run: RunSnapshot, *, export_authorized: bool = False) -> tuple[ToolSpec, ...]:
        return self._policy.visible_specs(
            self._registry.specs(), run, export_authorized=export_authorized,
        )

    def authorize(
        self,
        spec: ToolSpec,
        run: RunSnapshot,
        *,
        export_authorized: bool = False,
    ):
        """Expose the same policy decision to production gateway adapters."""
        return self._policy.evaluate(spec, run, export_authorized=export_authorized)

    @staticmethod
    def idempotency_key(run: RunSnapshot, call: ToolRequest) -> str:
        payload = json.dumps(call.arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        raw = f"{run.run_id}:{run.goal_revision}:{run.plan_version}:{call.call_id}:{call.name}:{payload}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _rejected(call: ToolRequest, code: str, summary: str) -> ToolObservation:
        return ToolObservation(
            call_id=call.call_id,
            tool_name=call.name,
            status=ObservationStatus.REJECTED,
            summary=summary,
            error_code=code,
        )

    async def dispatch(
        self,
        run: RunSnapshot,
        calls: tuple[ToolRequest, ...],
        *,
        export_authorized: bool = False,
    ) -> tuple[ToolObservation, ...]:
        results: list[ToolObservation] = []
        parallel_batch: list[tuple[RegisteredTool, ToolRequest]] = []
        batch_locks: set[str] = set()

        async def execute_one(tool: RegisteredTool, call: ToolRequest) -> ToolObservation:
            context = ToolCallContext(
                call_id=call.call_id,
                run_id=run.run_id,
                user_id=run.user_id,
                thread_id=run.thread_id,
                goal_revision=run.goal_revision,
                plan_version=run.plan_version,
                idempotency_key=self.idempotency_key(run, call),
            )
            try:
                return await asyncio.wait_for(
                    tool.execute(dict(call.arguments), context),
                    timeout=tool.spec.timeout_seconds,
                )
            except asyncio.TimeoutError:
                return ToolObservation(
                    call_id=call.call_id,
                    tool_name=call.name,
                    status=ObservationStatus.FAILED,
                    summary="tool timed out",
                    retryable=True,
                    error_code="tool_timeout",
                )
            except asyncio.CancelledError:
                return ToolObservation(
                    call_id=call.call_id,
                    tool_name=call.name,
                    status=ObservationStatus.CANCELLED,
                    summary="tool cancelled",
                    error_code="tool_cancelled",
                )
            except Exception as exc:  # noqa: BLE001
                return ToolObservation(
                    call_id=call.call_id,
                    tool_name=call.name,
                    status=ObservationStatus.FAILED,
                    summary=str(exc)[:500],
                    retryable=False,
                    error_code="tool_execution_failed",
                )

        async def flush_parallel() -> None:
            nonlocal parallel_batch, batch_locks
            if parallel_batch:
                results.extend(await asyncio.gather(*(
                    execute_one(tool, call) for tool, call in parallel_batch
                )))
            parallel_batch = []
            batch_locks = set()

        for call in calls:
            tool = self._registry.get(call.name)
            if tool is None:
                await flush_parallel()
                results.append(self._rejected(call, "unknown_tool", "tool is not registered"))
                continue
            decision = self._policy.evaluate(
                tool.spec, run, export_authorized=export_authorized,
            )
            if not decision.allowed:
                await flush_parallel()
                results.append(self._rejected(call, decision.reason_code, "tool is not allowed in this run"))
                continue
            locks = set(tool.spec.resource_locks)
            can_join = tool.spec.parallel_safe and not (locks & batch_locks)
            if can_join:
                parallel_batch.append((tool, call))
                batch_locks.update(locks)
                continue
            await flush_parallel()
            results.append(await execute_one(tool, call))

        await flush_parallel()
        return tuple(results)
