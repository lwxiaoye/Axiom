"""CAS-backed authoritative plan storage and goal steering."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select

from app.core.runtime_db import runtime_session

from .contracts import PlanSnapshot, PlanStepSnapshot, PlanStepStatus
from .run_store import is_harness_state


class PlanConflict(RuntimeError):
    pass


async def get_plan_snapshot(run_id: str) -> PlanSnapshot | None:
    """Read the one active plan, including its complete ordered step snapshot."""
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentPlan, AgentPlanStep

    async with factory() as session:
        plan = await session.scalar(
            select(AgentPlan)
            .where(AgentPlan.run_id == run_id, AgentPlan.status == "active")
            .order_by(AgentPlan.updated_at.desc())
            .limit(1)
        )
        if plan is None or int(plan.plan_version or 0) < 1:
            return None
        rows = (await session.execute(
            select(AgentPlanStep)
            .where(AgentPlanStep.plan_id == plan.id)
            .order_by(AgentPlanStep.seq.asc(), AgentPlanStep.id.asc())
        )).scalars().all()
        steps = tuple(
            PlanStepSnapshot(
                step_id=str(row.step_key or row.id),
                order=int(row.seq or 0),
                title=str(row.description or ""),
                detail=str(row.detail or ""),
                status=PlanStepStatus(str(row.status or PlanStepStatus.PENDING.value)),
                required=bool(row.required),
                acceptance_criteria=list(row.acceptance_criteria or []),
                evidence_refs=list(row.evidence or []),
                reason=str(row.reason or ""),
                depends_on=tuple(
                    str(item) for item in (row.depends_on or [])
                    if str(item).strip()
                ),
                requires=tuple(
                    str(item) for item in (row.requires or [])
                    if str(item).strip()
                ),
            )
            for row in rows
        )
        return PlanSnapshot(
            run_id=run_id,
            goal_revision=int(plan.goal_revision or 0),
            plan_version=int(plan.plan_version or 0),
            goal=str(plan.goal or plan.summary or ""),
            steps=steps,
            updated_at=plan.updated_at or plan.created_at or datetime.utcnow(),
        )


async def get_latest_prior_plan_snapshot(
    thread_id: str,
    *,
    before_run_id: str,
    require_complete: bool = False,
) -> PlanSnapshot | None:
    """Read the newest authoritative plan from an earlier Run in the same Thread."""
    if not thread_id or not before_run_id:
        return None
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentPlan, AgentRun

    async with factory() as session:
        current_created_at = await session.scalar(
            select(AgentRun.created_at).where(AgentRun.id == before_run_id)
        )
        if current_created_at is None:
            return None
        source_run_ids = (await session.scalars(
            select(AgentPlan.run_id)
            .join(AgentRun, AgentRun.id == AgentPlan.run_id)
            .where(
                AgentRun.thread_id == thread_id,
                AgentRun.created_at < current_created_at,
                AgentPlan.status == "active",
                AgentPlan.plan_version >= 1,
            )
            .order_by(AgentRun.created_at.desc(), AgentPlan.updated_at.desc())
            .limit(20)
        )).all()
    for source_run_id in source_run_ids:
        snapshot = await get_plan_snapshot(str(source_run_id))
        if snapshot is None:
            continue
        if require_complete and any(
            step.required and step.status not in {
                PlanStepStatus.COMPLETED,
                PlanStepStatus.SKIPPED,
                PlanStepStatus.INVALIDATED,
            }
            for step in snapshot.steps
        ):
            continue
        return snapshot
    return None


async def update_plan(
    run_id: str,
    *,
    expected_plan_version: int,
    expected_goal_revision: int,
    goal: str,
    steps: tuple[PlanStepSnapshot, ...],
) -> PlanSnapshot:
    """Replace the full plan snapshot and advance its version in the same Run transaction."""
    candidate = PlanSnapshot(
        run_id=run_id,
        goal_revision=expected_goal_revision,
        plan_version=expected_plan_version + 1,
        goal=goal,
        steps=steps,
        updated_at=datetime.utcnow(),
    )
    factory = runtime_session()
    if factory is None:
        raise RuntimeError("Harness Run Store is unavailable")
    from app.runtime_models import AgentPlan, AgentPlanStep, AgentRun

    async with factory() as session:
        run = await session.get(AgentRun, run_id, with_for_update=True)
        if not run or not is_harness_state(run.state):
            raise RuntimeError("Harness Run does not exist")
        state = dict(run.state or {})
        if int(state.get("goal_revision") or 0) != expected_goal_revision:
            raise PlanConflict("goal_revision_conflict")
        if int(state.get("plan_version") or 0) != expected_plan_version:
            raise PlanConflict("plan_version_conflict")

        plan = await session.scalar(
            select(AgentPlan)
            .where(AgentPlan.run_id == run_id, AgentPlan.status == "active")
            .order_by(AgentPlan.updated_at.desc())
            .with_for_update()
            .limit(1)
        )
        if plan is None:
            plan = AgentPlan(id=uuid.uuid4().hex, run_id=run_id, status="active")
            session.add(plan)
            await session.flush()
        plan.goal = candidate.goal
        plan.summary = candidate.goal
        plan.goal_revision = candidate.goal_revision
        plan.plan_version = candidate.plan_version

        existing = (await session.execute(
            select(AgentPlanStep).where(AgentPlanStep.plan_id == plan.id)
        )).scalars().all()
        by_key = {str(row.step_key): row for row in existing}
        touched: set[str] = set()
        for step in candidate.steps:
            touched.add(step.step_id)
            row = by_key.get(step.step_id)
            if row is None:
                row = AgentPlanStep(id=uuid.uuid4().hex, plan_id=plan.id, step_key=step.step_id)
                session.add(row)
            row.seq = step.order
            row.description = step.title
            row.detail = step.detail or None
            row.status = step.status.value
            row.required = 1 if step.required else 0
            row.acceptance_criteria = list(step.acceptance_criteria)
            row.depends_on = list(step.depends_on)
            row.requires = list(step.requires)
            row.evidence = list(step.evidence_refs)
            row.reason = step.reason or None
            row.verified = 1 if step.status is PlanStepStatus.COMPLETED and step.evidence_refs else 0
        next_invalidated_order = max((step.order for step in candidate.steps), default=-1) + 1
        for key, row in sorted(
            by_key.items(), key=lambda item: (int(item[1].seq or 0), str(item[1].id)),
        ):
            if key not in touched:
                row.status = PlanStepStatus.INVALIDATED.value
                row.reason = row.reason or "removed_by_new_plan_version"
                # 失效步骤仍属于完整快照，但不能与新版活动步骤复用 order；否则
                # get_plan_snapshot 会因 PlanSnapshot 的唯一顺序约束直接校验失败。
                row.seq = next_invalidated_order
                next_invalidated_order += 1

        state["plan_id"] = plan.id
        state["plan_version"] = candidate.plan_version
        run.state = state
        run.state_version = int(run.state_version or 0) + 1
        await session.commit()
    persisted = await get_plan_snapshot(run_id)
    if persisted is None:  # pragma: no cover - commit 后读不到属于存储损坏
        raise RuntimeError("Harness Plan disappeared after update")
    return persisted


async def revise_goal(run_id: str, *, expected_state_version: int, goal: str) -> int:
    """Advance goal_revision at the steer boundary; old tool calls become stale immediately."""
    factory = runtime_session()
    if factory is None:
        raise RuntimeError("Harness Run Store is unavailable")
    from app.runtime_models import AgentRun

    async with factory() as session:
        run = await session.get(AgentRun, run_id, with_for_update=True)
        if not run or not is_harness_state(run.state):
            raise RuntimeError("Harness Run does not exist")
        if int(run.state_version or 0) != expected_state_version:
            raise PlanConflict("state_version_conflict")
        state = dict(run.state or {})
        revision = int(state.get("goal_revision") or 0) + 1
        state["goal_revision"] = revision
        state["pending_input"] = {"kind": "steer", "goal": goal}
        run.goal = goal
        run.state = state
        run.state_version = int(run.state_version or 0) + 1
        await session.commit()
        return revision
