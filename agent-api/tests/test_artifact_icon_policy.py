"""主对话全部可视化产物的图标规范回归。"""

import pytest

from app.services.chat import turn_context_builder
from app.services.chat.tools.shell import build_shell_tools
from app.services.sandbox import output_review, visual_review


def test_system_prompt_forbids_emoji_icons_in_all_visual_artifacts() -> None:
    prompt = turn_context_builder._build_system_prompt()

    assert "生成任何可视化产物" in prompt
    assert "HTML/网页组件、PPT、Word/PDF、表格看板、图表、海报" in prompt
    assert "HTML 使用内联 SVG、项目已有图标组件或 CSS 图形" in prompt
    assert "没有合适图标时宁可使用简洁文字标签" in prompt
    assert "HTML 的按钮、导航、卡片、状态和功能入口" in prompt
    assert "不得使用 Emoji、Unicode 符号、icon font 字符" in prompt


def test_executor_description_applies_icon_policy_to_office_artifacts() -> None:
    """执行器的工具描述必须带图标规范。

    为什么必须挂在执行器描述上、而不是只放系统提示词里：产出 PPT/Word 的那一步就是这个
    工具，描述是模型在**决定怎么写脚本时**最贴近的一份约束。丢了它，产物会退回 Emoji 充当
    图标——那正是 ppt 审美否决清单上的一条。
    """
    tools = build_shell_tools(user_id="test-user")
    executor = next(tool for tool in tools if tool.name == "bash")

    assert "可视化产物图标规范" in executor.description
    assert "PPT、Word/PDF、表格看板、图表和海报" in executor.description
    assert "SVG 矢量图标、原生形状与线条" in executor.description
    assert "透明背景 PNG" in executor.description
    assert "不要退回 Emoji" in executor.description


def test_visual_review_rejects_emoji_used_as_artifact_icons() -> None:
    prompt = visual_review._VISUAL_PROMPT

    assert "PPT、Word/PDF、表格看板、图表、海报等可视化产物" in prompt
    assert "卡片/章节/导航/状态/要点的图标位" in prompt
    assert "应标 error" in prompt


def test_ppt_structure_review_deterministically_rejects_emoji_glyphs() -> None:
    for char in "💧⚠✅⏰✓":
        assert output_review._is_forbidden_ppt_glyph(char)
    for char in "补水ABC123•—%":
        assert not output_review._is_forbidden_ppt_glyph(char)
    assert "web_safe_icons" in output_review._REVIEW_SCRIPT
    assert "no_icon_fonts" in output_review._REVIEW_SCRIPT
