# -*- coding: utf-8 -*-
"""「开始执行」之后不得再被计划轮快照/调研游标拉回去。"""
from app.services.agent_harness.contracts import (
    PlanStepSnapshot,
    PlanStepStatus,
    RunSnapshot,
)
from app.services.agent_harness.plan_execution import (
    exit_plan_mode_tool_env,
    plan_execution_already_unlocked,
    release_investigation_cursor,
)


def _snapshot(**overrides) -> RunSnapshot:
    payload = dict(
        run_id="r1",
        thread_id="th1",
        user_id="u1",
        agent_mode="plan",
        phase="waiting_confirmation",
        capability_scope="planning",
        state_version=0,
        goal_revision=0,
        plan_version=1,
        event_cursor=0,
        execution_profile={"id": "interactive"},
    )
    payload.update(overrides)
    return RunSnapshot(**payload)


def test_exit_plan_mode_tool_env_clears_inspect_snapshot():
    out = exit_plan_mode_tool_env({
        "plan_mode": True,
        "action_authority": "inspect",
        "allow_create": False,
        "turn_intent": "execute",
    })
    assert out["plan_mode"] is False
    assert out["action_authority"] == "mutate"
    assert out["allow_create"] is True
    assert out["turn_intent"] == "execute"


def test_already_unlocked_when_facts_left_plan_but_snapshot_still_inspect():
    tool_env = {"plan_mode": True, "action_authority": "inspect"}
    assert plan_execution_already_unlocked(
        _snapshot(agent_mode="standard", phase="executing", capability_scope="default"),
        tool_env,
    ) is True
    assert plan_execution_already_unlocked(
        _snapshot(),
        tool_env,
    ) is False
    assert plan_execution_already_unlocked(
        _snapshot(agent_mode="standard"),
        {"plan_mode": False},
    ) is False


def test_already_unlocked_by_approved_version_while_executing():
    assert plan_execution_already_unlocked(
        _snapshot(
            agent_mode="plan",
            phase="executing",
            capability_scope="default",
            approved_plan_version=2,
        ),
        {"plan_mode": True},
    ) is True


def test_release_investigation_cursor_skips_stale_research_step():
    steps = (
        PlanStepSnapshot(
            step_id="research",
            order=0,
            title="调研资料",
            status=PlanStepStatus.IN_PROGRESS,
            requires=("investigate",),
        ),
        PlanStepSnapshot(
            step_id="write",
            order=1,
            title="生成Word并保存到我的文件",
            requires=("productive",),
        ),
    )
    out = release_investigation_cursor(steps)
    assert out[0].status is PlanStepStatus.SKIPPED
    assert out[0].reason == "execution_started"
    assert out[1].status is PlanStepStatus.IN_PROGRESS


def test_release_investigation_cursor_completes_when_receipts_exist():
    steps = (
        PlanStepSnapshot(
            step_id="research",
            order=0,
            title="调研",
            status=PlanStepStatus.IN_PROGRESS,
            requires=("investigate",),
            evidence_refs=[{"status": "succeeded", "tool_name": "search_web"}],
        ),
        PlanStepSnapshot(
            step_id="write",
            order=1,
            title="写报告",
            requires=("productive",),
        ),
    )
    out = release_investigation_cursor(steps)
    assert out[0].status is PlanStepStatus.COMPLETED
    assert out[1].status is PlanStepStatus.IN_PROGRESS


def test_release_investigation_cursor_leaves_productive_cursor():
    steps = (
        PlanStepSnapshot(
            step_id="write",
            order=0,
            title="生成Word",
            status=PlanStepStatus.IN_PROGRESS,
            requires=("productive",),
        ),
        PlanStepSnapshot(
            step_id="deliver",
            order=1,
            title="交付",
            requires=("productive",),
        ),
    )
    assert release_investigation_cursor(steps) == steps


def test_release_investigation_cursor_noop_without_later_work():
    steps = (
        PlanStepSnapshot(
            step_id="research",
            order=0,
            title="调研",
            status=PlanStepStatus.IN_PROGRESS,
            requires=("investigate",),
        ),
    )
    assert release_investigation_cursor(steps) == steps


def test_resume_and_router_call_the_same_unlock():
    from pathlib import Path

    orch = Path(__file__).resolve().parents[1].joinpath(
        "app/services/agent_harness/orchestrator.py"
    ).read_text(encoding="utf-8")
    router = Path(__file__).resolve().parents[1].joinpath(
        "app/api/router.py"
    ).read_text(encoding="utf-8")
    assert "persist_plan_execution_unlock" in orch
    assert "_unlock_plan_execution_before_resume" in orch
    assert "executing_now" in orch
    assert "exit_plan_mode_tool_env" in orch
    assert "persist_plan_execution_unlock" in router
