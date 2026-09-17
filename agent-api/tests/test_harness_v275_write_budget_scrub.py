# -*- coding: utf-8 -*-
"""v2.75: product write reserve + scrub 工具轮次耗尽/确认后再写."""
from pathlib import Path
import re

from app.services.chat.turn_finalizer import scrub_false_tool_outage_claim, scrub_false_search_hedge
from app.services.agent_harness.model_driver import _answer_claims_write_blocked, _product_tool_choice_for_model


def _src(rel: str) -> str:
    return Path(__file__).resolve().parents[1].joinpath(rel).read_text(encoding="utf-8")


def test_write_blocked_detects_round_exhaust_and_confirm():
    assert _answer_claims_write_blocked(
        "本轮回合工具轮次已耗尽，无法立即写入 Word 文件；内容已整理就绪，确认需求后我将立即为您生成 .docx 文档。"
    )
    assert _answer_claims_write_blocked("你确认后我立即生成文档")
    assert not _answer_claims_write_blocked("已交付 Agent市场简报.docx，三要点已写好。")


def test_scrub_removes_round_exhaust_claim():
    raw = (
        "网页端 Agent 核心亮点很多。"
        "本轮回合工具轮次已耗尽，无法立即写入 Word 文件；内容已整理就绪，确认需求后我将立即为您生成 .docx 文档。"
    )
    out = scrub_false_tool_outage_claim(raw, tools_succeeded=True, tools_delivered=False)
    assert "工具轮次" not in out
    assert "确认需求后" not in out
    assert "无法立即写入" not in out


def test_scrub_search_hedge_realtime_empty():
    raw = "本次检索未返回实时结果，上述信息基于既有知识库，如需最新动态可补充搜索验证。漳州今天 28℃。"
    out = scrub_false_search_hedge(
        raw,
        trace=[{"name": "search_web", "status": "completed", "preview": "漳州 28℃ 晴"}],
    )
    # hedge should not leave pure refusal when concrete exists; concrete can remain
    assert "28℃" in out or "℃" in out


def test_product_tool_choice_avoids_required_for_deepseek_thinking_mode():
    assert _product_tool_choice_for_model("deepseek-v4-flash") == "auto"
    assert _product_tool_choice_for_model("deepseek-reasoner") == "auto"
    assert _product_tool_choice_for_model("gpt-5.2") == "required"


def test_budget_exhaust_prompt_no_user_facing_rounds_leak_instruction():
    src = _src("app/services/agent_harness/model_driver.py")
    # Budget metrics may be logged, but they cannot inject a forced delivery
    # prompt or become a user-visible terminal reason.
    # Historical helper text may remain import-compatible, but it is not called
    # from the active loop and cannot alter the provider payload or terminal state.
    assert src.count("_budget_notice(") == 1  # definition only; active loop does not call it
    assert 'payload["tool_choice"] = "none"' not in src
    assert "budget_observation" in src
