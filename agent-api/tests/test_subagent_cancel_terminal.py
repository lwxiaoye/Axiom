"""Harness 对用户明确取消意图的识别。"""
from app.services.agent_harness.orchestrator import _is_explicit_cancel_choice


def test_cancel_choice_classifier_is_exact():
    for value in ("取消", "取消本次申请", "终止当前流程", "cancel", "STOP"):
        assert _is_explicit_cancel_choice(value)
    for value in ("取消后重新发起", "不要取消", "我想了解取消流程", "病假"):
        assert not _is_explicit_cancel_choice(value)
