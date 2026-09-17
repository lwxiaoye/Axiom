"""v2.86: 「不要保存到我的文件」不得误判为产物/落盘任务。"""

from app.services.agent_harness.model_driver import (
    _chat_inline_image_only_goal,
    _goal_wants_workspace_product,
    _user_requires_file_deliverable,
    _goal_is_simple_fact_lookup,
    _goal_is_chat_lookup,
)
from app.services.agent_harness.orchestrator import _goal_next_action_text

WX = "你帮我看看今天漳州市的天气状况，并附上几张当地照片，只要对话里看就行，不要保存到我的文件。"
WX2 = "查一下漳州天气，配几张照片"
WORD = "用Word写一份简报，直接交付docx"


def test_weather_photos_not_product():
    assert _user_requires_file_deliverable(WX) is False
    assert _goal_wants_workspace_product(WX) is False
    assert _chat_inline_image_only_goal(WX) is True
    assert _goal_is_chat_lookup(WX) is True
    assert _goal_is_simple_fact_lookup(WX) is True


def test_weather_photos_simple_also():
    assert _chat_inline_image_only_goal(WX2) is True
    assert _goal_wants_workspace_product(WX2) is False
    assert _user_requires_file_deliverable(WX2) is False


def test_word_still_file_deliverable():
    assert _user_requires_file_deliverable(WORD) is True
    assert _goal_wants_workspace_product(WORD) is True
    assert _chat_inline_image_only_goal(WORD) is False


def test_next_action_for_weather_photos():
    t = _goal_next_action_text(WX)
    assert t == ""
