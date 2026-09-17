"""Leave Plan mode after the user approves execution.

v1.46 flipped ``agent_mode`` and rewrote the prompt on 「开始执行」, but three
follow-on paths still restored inspect-only tools or kept the plan cursor on an
investigate step. This module is the single unlock used by the HTTP resume API
and by ``_resume_orchestration`` / ``resume_chat``.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from .contracts import AgentMode, PlanStepSnapshot, PlanStepStatus, RunPhase, RunSnapshot
from .plan_binding import infer_requires

logger = logging.getLogger(__name__)


def exit_plan_mode_tool_env(tool_env: Optional[dict]) -> dict:
    """Rewrite the HITL tool snapshot so a later hang cannot restore inspect."""
    env = dict(tool_env or {})
    env["plan_mode"] = False
    env["action_authority"] = "mutate"
    env["allow_create"] = True
    return env


def plan_execution_already_unlocked(
    snapshot: Optional[RunSnapshot],
    tool_env: Optional[dict] = None,
) -> bool:
    """True when this hang started as Plan mode but the Run already left it.

    The original tool_env snapshot stays ``plan_mode=True`` until rewritten.
    Router unlock, a previous resume, or a worker restart must still see mutate.
    """
    env = tool_env or {}
    if not env.get("plan_mode"):
        return False
    if snapshot is None:
        return False
    if snapshot.agent_mode == AgentMode.STANDARD:
        return True
    if snapshot.phase == RunPhase.EXECUTING and snapshot.capability_scope not in {
        "inspect",
        "planning",
        "revision_pending",
    }:
        return True
    if snapshot.approved_plan_version is not None and snapshot.phase in {
        RunPhase.EXECUTING,
        RunPhase.VERIFYING,
    }:
        return True
    return False


def _investigate_only(step: PlanStepSnapshot) -> bool:
    tags = set(step.requires or ())
    if not tags:
        tags = set(infer_requires(step.title, step.detail, step.acceptance_criteria))
    return "investigate" in tags and not tags.intersection(
        {"productive", "artifact_producer", "mutate"}
    )


def _has_success_evidence(step: PlanStepSnapshot) -> bool:
    return any(
        isinstance(ref, dict) and str(ref.get("status") or "") == "succeeded"
        for ref in (step.evidence_refs or ())
    )


def release_investigation_cursor(
    steps: Sequence[PlanStepSnapshot],
) -> tuple[PlanStepSnapshot, ...]:
    """Move the unique cursor off a leftover investigate step so write tools can bind.

    Linear ready-checks treat an earlier ``in_progress`` investigate row as a blocker.
    After the user approved execution that cursor is stale: skip it (or complete it
    when it already has receipts) and nominate the next unfinished step.
    """
    rows = list(steps)
    in_progress = [step for step in rows if step.status is PlanStepStatus.IN_PROGRESS]
    if len(in_progress) != 1:
        return tuple(rows)
    cursor = in_progress[0]
    if not _investigate_only(cursor):
        return tuple(rows)
    later_open = [
        step for step in rows
        if step.order > cursor.order
        and step.status not in {
            PlanStepStatus.COMPLETED,
            PlanStepStatus.SKIPPED,
            PlanStepStatus.INVALIDATED,
        }
    ]
    if not later_open:
        return tuple(rows)
    done_status = (
        PlanStepStatus.COMPLETED if _has_success_evidence(cursor) else PlanStepStatus.SKIPPED
    )
    nominated = False
    out: list[PlanStepSnapshot] = []
    for step in rows:
        if step.step_id == cursor.step_id:
            out.append(step.model_copy(update={
                "status": done_status,
                "reason": "execution_started",
            }))
            continue
        if (
            not nominated
            and step.status is PlanStepStatus.PENDING
            and step.order > cursor.order
        ):
            out.append(step.model_copy(update={"status": PlanStepStatus.IN_PROGRESS, "reason": ""}))
            nominated = True
            continue
        out.append(step)
    return tuple(out)


async def persist_plan_execution_unlock(run_id: str) -> bool:
    """Flip Run facts to executing/standard and free the investigate cursor.

    Do **not** rewrite ``orchestration.tool_env`` here. ``_resume_orchestration``
    still needs the original ``plan_mode=True`` bit to escalate mutate and rewrite
    the plan-round system prompt; it persists the inspect→mutate snapshot itself.
    """
    from app.services.agent_harness import run_store
    from app.services.tasks import task_run_service

    column_ok = await task_run_service.set_agent_mode(run_id, "standard")
    patched = await run_store.patch_run_state(
        run_id,
        {
            "agent_mode": "standard",
            "capability_scope": "default",
            "pending_input": None,
        },
        phase="executing",
    )
    try:
        await _persist_investigation_cursor_release(run_id)
    except Exception:  # noqa: BLE001
        logger.debug("execution unlock cursor release skipped run=%s", run_id, exc_info=True)
    return bool(patched or column_ok)


async def _persist_investigation_cursor_release(run_id: str) -> None:
    from app.services.agent_harness import plan_store, run_store

    snapshot = await plan_store.get_plan_snapshot(run_id)
    if snapshot is None or not snapshot.steps:
        return
    next_steps = release_investigation_cursor(snapshot.steps)
    if next_steps == snapshot.steps:
        return
    current = await run_store.get_run_state(run_id)
    if current is None:
        return
    state = current.get("state") or {}
    await plan_store.update_plan(
        run_id,
        expected_plan_version=int(state.get("plan_version") or snapshot.plan_version or 0),
        expected_goal_revision=int(state.get("goal_revision") or snapshot.goal_revision or 0),
        goal=snapshot.goal,
        steps=next_steps,
    )
