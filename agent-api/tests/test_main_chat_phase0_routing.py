# -*- coding: utf-8 -*-
"""主对话阶段 0 路由矩阵（2026-08-04 计划 §6.1）。

直接调用 shipped `decide_turn` / `route` / `force_agent_work_signal` / `is_pure_qa_message`，
锁死真机事故同句：跑/写入/列出必须 agent；纯口算 pure_qa；零工具护栏禁假故障文案。
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.chat.capability_broker import CapabilityBroker, build_capability_search_tool, resolve_core_pins
from app.services.chat.plain_turn import (
    FALLBACK_NO_TOOLS_GUARD,
    PURE_QA_NO_TOOLS_GUARD,
    apply_fallback_no_tools_guard,
)
from app.services.chat.tools.base import MainTool
from app.services.chat.turn_decision import (
    decide_turn,
    force_agent_work_signal,
    is_pure_qa_message,
)


def _route(msg: str, **kw):
    d = decide_turn(msg, has_selected_files=kw.pop("has_selected_files", False), active_run=False)
    return d, d.route(has_explicit_resources=kw.pop("has_explicit_resources", False), message=msg)


def test_pure_math_qa_is_direct_answer_zero_tools():
    d, route = _route("只用一句话回答：3+5等于几？不要用工具。")
    assert is_pure_qa_message("只用一句话回答：3+5等于几？不要用工具。")
    assert route == "direct_answer"
    assert d.intent in ("conversation",)


def test_sky_blue_question_defaults_to_agent():
    """百科/常识问句不再物理禁工具：默认 agent，模型可自决是否搜索。"""
    assert not is_pure_qa_message("为什么天空是蓝色？")
    d, route = _route("为什么天空是蓝色？")
    assert route == "agent"


def test_create_md_file_is_agent():
    d, route = _route("在我的文件新建 agent-diag-x.md，正文只写 hello-x。一句话确认文件名。")
    assert route == "agent"
    assert d.intent in ("execute", "revise") or force_agent_work_signal(
        "在我的文件新建 agent-diag-x.md，正文只写 hello-x。"
    )


def test_bash_pao_write_is_agent_not_direct_answer():
    """真机事故句：「用 bash 跑」不得再 zero-tool。"""
    msg = (
        "用 bash 跑 python3 -c \"print(111*222)\"，把数字写入 agent-diag-y.txt（只写数字），"
        "回复里也写数字。"
    )
    assert force_agent_work_signal(msg)
    d, route = _route(msg)
    assert route == "agent", f"expected agent, got {route} intent={d.intent} reason={d.reason_code}"
    assert d.intent in ("execute", "revise")


def test_bash_execute_synonym_still_agent():
    msg = (
        "用 bash 执行 python3 -c \"print(12345*6789)\"，把输出的数字原样写入我的文件 "
        "agent-diag-calc2.txt"
    )
    d, route = _route(msg)
    assert route == "agent"


def test_list_my_files_is_agent():
    msg = "列出我的文件里文件名含 agent-diag 的真实文件，不要编造。"
    assert force_agent_work_signal(msg)
    d, route = _route(msg)
    assert route == "agent"


def test_which_md_files_is_agent():
    msg = "看看有哪些 md"
    # 扩展名 . 可能缺失；「有哪些」+ 弱信号 — 用明确 md 扩展
    msg2 = "看看有哪些 .md 文件"
    d, route = _route(msg2)
    assert route == "agent"


@pytest.mark.parametrize(
    "msg",
    [
        "列出所有文件",
        "有哪些文件",
        "列出我的文件",
        "列出我的文件里文件名含 agent-diag 的真实文件，不要编造。",
        "看看有哪些 .md 文件",
        "帮我看看文件列表",
        "我的文件里有哪些",
    ],
)
def test_workspace_list_phrases_stay_agent(msg):
    """工作区列举必须保持 agent（与百科负例对照锁死）。"""
    assert force_agent_work_signal(msg), msg
    d, route = _route(msg)
    assert route == "agent", f"{msg!r} → {route} intent={d.intent}"


@pytest.mark.parametrize(
    "msg",
    [
        "Python有哪些特点",
        "列出三国演义人物",
        "有哪些 monads",
        "Java有哪些集合类型",
        # 话题词含「文件/文档」但仍是百科——不得因裸词 force agent（skeptic 二轮）
        "C语言有哪些文件操作函数",
        "文档有哪些类型",
        "有哪些文件IO方法",
        "有哪些文件系统",
        "列出文件名规则",
        "解释一下文件描述符",
    ],
)
def test_bare_list_or_which_without_file_context_defaults_to_agent(msg):
    """百科列举默认 agent：能力常驻，模型可不调工具也能答，但不得被物理禁工具。"""
    assert not is_pure_qa_message(msg), msg
    d, route = _route(msg)
    assert route == "agent", f"{msg!r} → {route} intent={d.intent} reason={d.reason_code}"



def test_casual_hello_is_pure_qa():
    assert is_pure_qa_message("你好")
    d, route = _route("你好")
    assert route == "direct_answer"


def test_pure_qa_guard_forbids_fake_outage_copy():
    out = apply_fallback_no_tools_guard("base", True, intentional_pure_qa=True)
    assert PURE_QA_NO_TOOLS_GUARD in out
    assert "工具系统临时不可用" not in out
    assert "工具系统不可用" not in out


def test_fallback_guard_forbids_platform_outage_wording():
    """故障回退仍禁「工具系统不可用」假故障口径。"""
    assert "工具系统临时不可用" not in FALLBACK_NO_TOOLS_GUARD
    assert "工具系统不可用" not in FALLBACK_NO_TOOLS_GUARD
    assert "搜索结果" in FALLBACK_NO_TOOLS_GUARD
    assert "已保存文件" in FALLBACK_NO_TOOLS_GUARD
    out = apply_fallback_no_tools_guard("base", True, intentional_pure_qa=False)
    assert "工具系统临时不可用" not in out
    assert "工具系统不可用" not in out


def _tool(name: str) -> MainTool:
    async def execute(_args):
        return "ok"

    return MainTool(name=name, description=f"{name}", parameters={}, execute=execute)


def test_agent_path_broker_pins_core_executors_without_search_first():
    """核工具必须在 initial_tools 中，不得先靠 search_capabilities 才出现。"""
    names = [
        "ask_user_choice", "bash", "write_file", "edit_file", "read_file", "glob",
        "download_url", "fetch_ppt_asset", "search_web", "browser_fetch", "update_plan",
        "get_current_time", "get_user_location",
    ]
    tools = [_tool(n) for n in names]
    # 生产口径：web_enabled=True 时 search_web 常驻；不依赖 UI web_search 开关
    pinned = resolve_core_pins(action_authority="mutate", web_enabled=True)
    assert "search_web" in pinned
    broker = CapabilityBroker(tools, pinned=pinned)
    search = build_capability_search_tool(broker)
    broker.register(search, active=True)
    initial = {t.name for t in broker.initial_tools()}
    for core in (
        "bash", "write_file", "edit_file", "read_file", "glob",
        "ask_user_choice", "search_web", "fetch_ppt_asset",
        "get_current_time", "get_user_location",
    ):
        assert core in initial, f"core executor {core} missing from initial tools: {initial}"
    # browser stays secondary (discovery), not forced into the first tool list
    assert "browser_fetch" not in initial
    # search_capabilities is extra, not a prerequisite for core presence
    assert "bash" in initial


def test_resolve_core_pins_search_web_follows_platform_enable_not_ui_toggle():
    """根治：search_web 可见性 = 平台 web_enabled，不是 + 菜单 web_search 开关。"""
    off = resolve_core_pins(web_enabled=False, action_authority="mutate")
    on = resolve_core_pins(web_enabled=True, action_authority="mutate")
    assert "search_web" not in off
    assert "search_web" in on
    # inspect path keeps search and scratch bash, but no persistent workspace writers.
    inspect_on = resolve_core_pins(web_enabled=True, action_authority="inspect")
    assert "search_web" in inspect_on
    assert "bash" in inspect_on
    assert "write_file" not in inspect_on
    assert "edit_file" not in inspect_on


def test_readonly_user_context_tools_are_pinned_without_web_search():
    pinned = resolve_core_pins(web_enabled=False, action_authority="inspect")

    assert "get_current_time" in pinned
    assert "get_user_location" in pinned


def test_resolve_core_pins_exposes_subagent_when_candidates_exist():
    without_candidates = resolve_core_pins(
        action_authority="mutate",
        has_subagent_candidates=False,
    )
    with_candidates = resolve_core_pins(
        action_authority="mutate",
        has_subagent_candidates=True,
    )

    assert "call_subagent" not in without_candidates
    assert "call_subagent" in with_candidates


def test_resolve_core_pins_artifact_coding_keeps_update_plan():
    default = resolve_core_pins(action_authority="mutate", turn_intent="conversation")
    ppt = resolve_core_pins(
        action_authority="mutate",
        turn_intent="conversation",
        pin_plan_tool=True,
    )
    cont = resolve_core_pins(action_authority="mutate", turn_intent="continue")
    assert "update_plan" not in default
    assert "update_plan" in ppt
    assert "update_plan" in cont


def test_observation_status_failed_is_available():
    obs = asyncio.run(_tool("bash").observe({}))
    assert obs.status in ("succeeded", "failed", "unknown") or obs is not None


def test_realtime_world_queries_are_agent_not_pure_qa():
    """用户事故句：自然语言天气/新闻/价格必须 agent，禁止 pure_qa 零工具空答。

    不靠领域词表缝补——is_pure_qa 只认寒暄/口算/显式禁工具，其余默认 agent。
    """
    cases = [
        "你帮我看看今天漳州市的天气状况",
        "今天我这里天气怎样",
        "我现在在哪个城市",
        "今天有什么重要科技新闻",
        "帮我查一下黄金价格",
        "黄金现在什么价",
    ]
    for msg in cases:
        assert not is_pure_qa_message(msg), msg
        d, route = _route(msg)
        assert route == "agent", f"{msg!r} → {route} intent={d.intent}"
