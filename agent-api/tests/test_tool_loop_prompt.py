# -*- coding: utf-8 -*-
"""工具循环 system prompt 的组装（2026-07-27 Skill 目录事故回归）。

事故：`skill_catalog_block` 只被拼进 plain_turn / fallback 直答的 prompt，工具循环这条
**无条件进入**的默认路径上没有它。于是模型手握 use_skill、工具描述写着「id 取自系统提示
词的 Skill 目录」，却从没见过任何 skill_id —— 真机问「平台上有哪些技能」，模型编出了
「PPT 演示专家 id=ppt」「Deep Research id=research」两个不存在的技能。上线 12 天零生效。

之所以没被拦住：既有测试（test_skill_catalog.py）只断言 `_fetch_skill_catalog_block`
自己的产物、以及 `prep.skill_catalog_block` 的内容，**没有一条断言它进了给模型的 prompt**。
本文件补的就是这一条。
"""
import inspect

from app.services.chat.main_tool_turn import (
    compose_tool_loop_system_prompt,
    recovery_projection_world_state,
    run_agent_turn,
    tool_loop_projection_world_state,
)


def test_skill_catalog_is_in_the_prompt():
    """正面：目录必须出现在组装结果里。少了它模型就只能编 skill_id。"""
    out = compose_tool_loop_system_prompt(
        "你是助手", summary_block="", skill_catalog_block="[demo] 演示技能：做演示",
        turn_guard_prompt="",
    )
    assert "[demo] 演示技能" in out


def test_turn_guard_stays_last():
    """顺序即优先级：本轮约束恒为最后一段，不被目录挤到前面去。"""
    out = compose_tool_loop_system_prompt(
        "BASE", summary_block="SUMMARY", skill_catalog_block="CATALOG",
        turn_guard_prompt="GUARD",
    )
    assert out.split("\n\n") == ["BASE", "SUMMARY", "CATALOG", "GUARD"]


def test_blank_sections_are_dropped_not_rendered_as_gaps():
    """空段不留空行——否则模型会看到一串莫名其妙的空白分隔。"""
    assert compose_tool_loop_system_prompt("BASE") == "BASE"
    assert compose_tool_loop_system_prompt(
        "BASE", skill_catalog_block="CATALOG") == "BASE\n\nCATALOG"


def test_dynamic_tool_sections_have_typed_projection_fields():
    assert tool_loop_projection_world_state(
        summary_block="SUMMARY",
        skill_catalog_block="CATALOG",
        knowledge_context="KNOWLEDGE",
        turn_guard="GUARD",
    ) == {
        "conversation_summary": "SUMMARY",
        "skill_catalog": "CATALOG",
        "knowledge_context": "KNOWLEDGE",
        "turn_guard": "GUARD",
    }


def test_recovery_projection_world_state_uses_the_atomic_checkpoint():
    checkpoint = {"current_date": "2026-08-30", "turn_guard": "original"}
    assert recovery_projection_world_state(
        {"current_date": "2026-08-31", "turn_guard": "recomputed"},
        initial_messages=[{"role": "user", "content": "resume"}],
        checkpoint_world_state=checkpoint,
    ) == checkpoint
    assert recovery_projection_world_state(
        {"current_date": "2026-08-31"},
        initial_messages=None,
        checkpoint_world_state=checkpoint,
    ) == {"current_date": "2026-08-31"}


def test_tool_loop_actually_feeds_the_catalog_in():
    """组装函数正确还不够——工具循环必须真的把目录传进来（事故正是断在这一步）。

    纯函数测不到「调用方传没传」，这里对调用点下结构断言：run_agent_turn 里
    既要从 env.prep 取出 skill_catalog_block，也要把它交给组装函数。
    """
    src = inspect.getsource(run_agent_turn)
    assert "env.prep.skill_catalog_block" in src, (
        "run_agent_turn 没有从 env.prep 取 Skill 目录 —— 模型将看不到任何 skill_id")
    assert "skill_catalog_block=skill_catalog_block" in src, (
        "取到了却没传给 compose_tool_loop_system_prompt —— 等于没取")


def test_tool_terminal_projection_uses_the_authoritative_persisted_row():
    """Provider-only history must bind to the exact assistant row just committed."""

    src = inspect.getsource(run_agent_turn)
    assert 'getattr(tool_row, "content", full_response)' in src
    assert 'getattr(tool_row, "attachments_json", None)' in src
    assert 'getattr(assistant_row, "content", full_response)' not in src
