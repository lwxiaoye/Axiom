"""Mid-run steer: plan rewrite hint is a prompt, not a tool gate."""

from app.services.agent_harness.goal_contract import steer_plan_revision_notice
from app.services.agent_harness.model_driver import _format_run_input


def test_steer_plan_revision_notice_lists_steps_and_keeps_ids():
    text = steer_plan_revision_notice(
        [
            {"key": "s1", "title": "调研", "status": "completed", "reason": "两份来源"},
            {"key": "s2", "title": "写页", "status": "in_progress"},
            {"key": "s3", "title": "发布", "status": "pending"},
        ],
        new_goal="改成只狼主题",
    )
    assert "s1 调研" in text
    assert "两份来源" in text
    assert "写页" in text
    assert "发布" in text
    assert "只狼" in text
    assert "不是强制计划或工具顺序" in text
    assert "可按需要调整计划" in text


def test_steer_plan_revision_notice_skips_execution_mode_and_empty_plan():
    steps = [{"key": "s1", "title": "调研", "status": "completed"}]
    assert steer_plan_revision_notice(steps, execution_mode=True) == ""
    assert steer_plan_revision_notice([]) == ""
    assert steer_plan_revision_notice(None) == ""


def test_steer_wrapper_still_forbids_rebuild():
    formatted = _format_run_input("改成只要三页", [])
    assert "不要推翻重来" in formatted
    assert "改成只要三页" in formatted
