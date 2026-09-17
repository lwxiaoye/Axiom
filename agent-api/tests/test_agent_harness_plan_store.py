from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from app.core.runtime_db import runtime_session
from app.runtime_models import AgentPlan, AgentPlanStep, AgentRun
from app.services.agent_harness import (
    PlanConflict,
    PlanStepSnapshot,
    PlanStepStatus,
    revise_goal,
    update_plan,
)
from app.services.agent_harness import run_store
from app.services.tasks import plan_service


@pytest.mark.asyncio
async def test_plan_snapshot_cas_and_goal_revision_are_one_authoritative_state():
    factory = runtime_session()
    if factory is None:
        pytest.skip("runtime database is unavailable")
    run_id = f"harness-plan-{uuid.uuid4().hex}"
    async with factory() as session:
        session.add(AgentRun(
            id=run_id,
            thread_id=f"thread-{uuid.uuid4().hex}",
            user_id="harness-test-user",
            kind="chat",
            status="created",
            state_version=0,
        ))
        await session.commit()

    try:
        assert await run_store.initialize_run_state(run_id, agent_mode="plan") is True
        snapshot = await update_plan(
            run_id,
            expected_plan_version=0,
            expected_goal_revision=0,
            goal="ship the Harness",
            steps=(
                PlanStepSnapshot(
                    step_id="inspect",
                    order=0,
                    title="Inspect",
                    status=PlanStepStatus.IN_PROGRESS,
                    acceptance_criteria=["evidence captured"],
                ),
                PlanStepSnapshot(step_id="deliver", order=1, title="Deliver"),
            ),
        )
        assert snapshot.plan_version == 1
        state = await run_store.get_run_state(run_id)
        assert state["state"]["plan_version"] == 1

        with pytest.raises(PlanConflict, match="plan_version_conflict"):
            await update_plan(
                run_id,
                expected_plan_version=0,
                expected_goal_revision=0,
                goal="stale update",
                steps=(),
            )

        revision = await revise_goal(
            run_id,
            expected_state_version=state["version"],
            goal="ship with revised scope",
        )
        assert revision == 1
        after = await run_store.get_run_state(run_id)
        assert after["state"]["pending_input"]["kind"] == "steer"

        async with factory() as session:
            plan = await session.scalar(select(AgentPlan).where(AgentPlan.run_id == run_id))
            rows = (await session.execute(
                select(AgentPlanStep).where(AgentPlanStep.plan_id == plan.id)
            )).scalars().all()
            assert plan.goal_revision == 0 and plan.plan_version == 1
            assert [row.step_key for row in rows] == ["inspect", "deliver"]
    finally:
        async with factory() as session:
            plans = (await session.execute(
                select(AgentPlan.id).where(AgentPlan.run_id == run_id)
            )).scalars().all()
            if plans:
                await session.execute(delete(AgentPlanStep).where(AgentPlanStep.plan_id.in_(plans)))
            await session.execute(delete(AgentPlan).where(AgentPlan.run_id == run_id))
            await session.execute(delete(AgentRun).where(AgentRun.id == run_id))
            await session.commit()


@pytest.mark.asyncio
async def test_plan_revision_keeps_invalidated_steps_with_unique_order():
    factory = runtime_session()
    if factory is None:
        pytest.skip("runtime database is unavailable")
    run_id = f"harness-plan-revision-{uuid.uuid4().hex}"
    async with factory() as session:
        session.add(AgentRun(
            id=run_id,
            thread_id=f"thread-{uuid.uuid4().hex}",
            user_id="harness-test-user",
            kind="chat",
            status="created",
            state_version=0,
        ))
        await session.commit()

    try:
        assert await run_store.initialize_run_state(run_id, agent_mode="standard") is True
        first = await update_plan(
            run_id,
            expected_plan_version=0,
            expected_goal_revision=0,
            goal="revise the plan",
            steps=(
                PlanStepSnapshot(step_id="old", order=0, title="Old step"),
                PlanStepSnapshot(step_id="keep", order=1, title="Keep step"),
            ),
        )
        revised = await update_plan(
            run_id,
            expected_plan_version=first.plan_version,
            expected_goal_revision=0,
            goal="revise the plan",
            steps=(
                PlanStepSnapshot(step_id="new", order=0, title="New step"),
                PlanStepSnapshot(step_id="keep", order=1, title="Keep step"),
            ),
        )
        assert revised.plan_version == 2
        assert len({step.order for step in revised.steps}) == len(revised.steps)
        invalidated = next(step for step in revised.steps if step.step_id == "old")
        assert invalidated.status is PlanStepStatus.INVALIDATED
        assert invalidated.reason == "removed_by_new_plan_version"
    finally:
        async with factory() as session:
            plans = (await session.execute(
                select(AgentPlan.id).where(AgentPlan.run_id == run_id)
            )).scalars().all()
            if plans:
                await session.execute(delete(AgentPlanStep).where(AgentPlanStep.plan_id.in_(plans)))
            await session.execute(delete(AgentPlan).where(AgentPlan.run_id == run_id))
            await session.commit()


@pytest.mark.asyncio
async def test_model_plan_revision_preserves_existing_evidence():
    factory = runtime_session()
    if factory is None:
        pytest.skip("runtime database is unavailable")
    run_id = f"harness-plan-evidence-{uuid.uuid4().hex}"
    async with factory() as session:
        session.add(AgentRun(
            id=run_id,
            thread_id=f"thread-{uuid.uuid4().hex}",
            user_id="harness-test-user",
            kind="chat",
            status="created",
            state_version=0,
        ))
        await session.commit()

    try:
        assert await run_store.initialize_run_state(run_id, agent_mode="standard") is True
        await update_plan(
            run_id,
            expected_plan_version=0,
            expected_goal_revision=0,
            goal="preserve evidence",
            steps=(
                PlanStepSnapshot(
                    step_id="inspect",
                    order=0,
                    title="Inspect",
                    evidence_refs=[{
                        "kind": "tool_observation",
                        "call_id": "inspect-1",
                        "tool_name": "inspect_workspace",
                        "status": "succeeded",
                    }],
                ),
                PlanStepSnapshot(step_id="deliver", order=1, title="Deliver"),
            ),
        )
        revised = await plan_service.upsert_plan(run_id, [
            {"key": "inspect", "title": "Inspect", "status": "completed"},
            {"key": "deliver", "title": "Deliver", "status": "in_progress"},
        ])
        assert revised[0]["evidence"][0]["call_id"] == "inspect-1"
    finally:
        async with factory() as session:
            plans = (await session.execute(
                select(AgentPlan.id).where(AgentPlan.run_id == run_id)
            )).scalars().all()
            if plans:
                await session.execute(delete(AgentPlanStep).where(AgentPlanStep.plan_id.in_(plans)))
            await session.execute(delete(AgentPlan).where(AgentPlan.run_id == run_id))
            await session.execute(delete(AgentRun).where(AgentRun.id == run_id))
            await session.commit()
