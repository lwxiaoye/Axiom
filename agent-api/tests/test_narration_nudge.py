"""Narration Checkpoint：静默真实动作后触发模型自述提醒。"""

import inspect

from app.services.agent_harness.model_driver import (
    _NARRATION_NUDGE_MAX,
    _NARRATION_NUDGE_MESSAGE,
    _NARRATION_SILENT_THRESHOLD,
    _should_nudge_narration,
)


def test_nudge_fires_after_silent_threshold():
    assert _should_nudge_narration(0, 0, False) is False
    assert _NARRATION_SILENT_THRESHOLD == 1
    assert _NARRATION_NUDGE_MAX == 6
    assert _should_nudge_narration(_NARRATION_SILENT_THRESHOLD, 0, False) is True
    assert _should_nudge_narration(_NARRATION_SILENT_THRESHOLD + 3, 0, False) is True


def test_nudge_respects_cap_and_forced_final():
    # 额度用尽不再提醒（防提示堆积挤占上下文）
    assert _should_nudge_narration(5, _NARRATION_NUDGE_MAX, False) is False
    assert _should_nudge_narration(5, _NARRATION_NUDGE_MAX - 1, False) is True
    # 强制收敛轮 tool_choice=none，只收敛不打扰
    assert _should_nudge_narration(5, 0, True) is False


def test_nudge_message_states_facts_only_contract():
    """文案契约：只在有实质进展时补一句，不把过程变成机械状态播报。"""
    assert "实质进展" in _NARRATION_NUDGE_MESSAGE
    assert "一句自然的话" in _NARRATION_NUDGE_MESSAGE
    assert "不要为了报状态而补述" in _NARRATION_NUDGE_MESSAGE
    assert "不要预告未执行的结果" in _NARRATION_NUDGE_MESSAGE


def test_drive_model_actually_injects_the_narration_nudge():
    """防止常量与判定函数存在，但主循环根本没有调用的假实现。"""
    from app.services.agent_harness import model_driver

    source = inspect.getsource(model_driver.drive_model)
    assert source.count("_should_nudge_narration(") == 1
    assert '"content": _NARRATION_NUDGE_MESSAGE' in source
    assert '"public_narration_nudged"' in source
