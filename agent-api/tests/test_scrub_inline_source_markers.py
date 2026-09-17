# -*- coding: utf-8 -*-
"""终答硬剥 [1]/[资料N] 来源编号（不依赖提示词）。"""
from app.services.chat.turn_finalizer import scrub_inline_source_markers


def test_strips_numeric_source_markers_keeps_prose():
    raw = (
        "结论先说：英雄联盟值得尝试[3]，尤其 2026 赛季[1]。"
        "核心优点[7][8]。主要门槛"
    )
    out = scrub_inline_source_markers(raw)
    assert "[3]" not in out and "[1]" not in out
    assert "[7]" not in out and "[8]" not in out
    assert "值得尝试" in out
    assert "2026 赛季" in out
    assert "核心优点" in out


def test_keeps_image_markers():
    raw = "天气晴朗[1]。\n\n[图1]\n\n配图如上[2]。"
    out = scrub_inline_source_markers(raw)
    assert "[图1]" in out
    assert "[1]" not in out and "[2]" not in out


def test_keeps_fenced_code_brackets():
    raw = "说明如下[1]。\n```\narr = [1, 2, 12]\n```\n结束[3]。"
    out = scrub_inline_source_markers(raw)
    assert "arr = [1, 2, 12]" in out
    assert "说明如下" in out
    assert "[3]" not in out


def test_strips_knowledge_style_markers():
    out = scrub_inline_source_markers("依据资料[资料1]与网页[2]可得结论。")
    assert "[资料1]" not in out
    assert "[2]" not in out
    assert "依据资料" in out


def test_pipeline_hook_present_in_main_paths():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    mt = (root / "app/services/chat/main_tool_turn.py").read_text(encoding="utf-8")
    pt = (root / "app/services/chat/plain_turn.py").read_text(encoding="utf-8")
    ma = (root / "app/services/agent_harness/model_driver.py").read_text(encoding="utf-8")
    assert "scrub_inline_source_markers" in mt
    assert "scrub_inline_source_markers" in pt
    assert "scrub_inline_source_markers" in ma
