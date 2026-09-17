# -*- coding: utf-8 -*-
"""Harness convergence rules: plan control is not a file mutation."""
from pathlib import Path


def _src(rel: str) -> str:
    return Path(__file__).resolve().parents[1].joinpath(rel).read_text(encoding="utf-8")


def test_standard_loop_has_no_forced_convergence_path():
    src = _src("app/services/agent_harness/model_driver.py")
    assert 'payload["tool_choice"] = "none"' not in src
    assert "st.disabled_tools.setdefault" not in src
    assert "net_plan_first_nudge" not in src
    assert "net_plan_early_nudge" not in src
    assert "CompletionVerifier" in src
    assert "_try_continue_unverified_completion" in src
    assert "completion_gap_loop_continue" not in src


def test_default_next_action_not_confirm_goal():
    src = _src("app/services/agent_harness/orchestrator.py")
    assert "接下来会先确认目标，再动手推进" not in src
    # 脚本化开场整段废弃：不再塞「我先按这个目标往下推进」
    assert "脚本化开场已废弃" in src or 'return ""' in src


def test_context_builder_prefers_colleague_tone():
    src = _src("app/services/chat/turn_context_builder.py")
    assert "像跟同事同步" in src
    assert "一两句自然结论：文件名+要点，像人在回话" in src


def test_lookup_allows_second_search_without_units():
    from app.services.agent_harness.model_driver import _trace_search_has_concrete_units

    assert _trace_search_has_concrete_units([
        {"name": "search_web", "status": "completed", "preview": "仅有入口页"},
    ]) is False
    assert _trace_search_has_concrete_units([
        {"name": "search_web", "status": "completed", "preview": "气温 26℃"},
    ]) is True


def test_concrete_unit_regex_rejects_dates():
    from app.services.chat.turn_finalizer import _CONCRETE_UNIT_RE
    assert _CONCRETE_UNIT_RE.search("26℃")
    assert _CONCRETE_UNIT_RE.search("35.8℃")
    assert not _CONCRETE_UNIT_RE.search("2026-07-30T12:00:00")
    assert not _CONCRETE_UNIT_RE.search("2026年5月26日")
