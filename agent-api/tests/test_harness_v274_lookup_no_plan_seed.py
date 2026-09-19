# -*- coding: utf-8 -*-
"""v2.74: simple fact lookup must not seed task plans or mechanical next-action."""
from pathlib import Path

from app.services.agent_harness.orchestrator import _goal_next_action_text
from app.services.agent_harness.model_driver import _goal_is_simple_fact_lookup


def _src(rel: str) -> str:
    return Path(__file__).resolve().parents[1].joinpath(rel).read_text(encoding="utf-8")


def test_simple_fact_lookup_detects_weather():
    assert _goal_is_simple_fact_lookup("查一下漳州今天天气，要具体气温，直接回答")
    assert not _goal_is_simple_fact_lookup("用Word写一份很短的三要点简报，直接交付")


def test_weather_next_action_is_empty_model_driven():
    text = _goal_next_action_text("查一下漳州今天天气，要具体气温，直接回答", pure_qa=False)
    assert text == ""


def test_accept_does_not_seed_lookup_plan():
    src = _src("app/services/agent_harness/orchestrator.py")
    accept = src[src.find("async def accept_harness_run"): src.find("async def start_resume_chat_run")]
    # v2.89+：受理阶段不 seed 任务板；也无 lookup 硬塞
    assert "task_plan_updated" not in accept
    assert "or _lookup" not in accept


def test_loop_lookup_no_plan_seed():
    # 主循环里 product / lookup 两条关键词分支（含「短事实检索只 nudge search_web」）整体
    # 退役：不再按领域关键词 seed 任务板或 nudge 工具，计划投影只来自模型的 update_plan。
    src = _src("app/services/agent_harness/model_driver.py")
    assert "短事实检索只 nudge search_web，不投影任务协作计划" not in src
    assert "_needs_product" not in src
    assert "_needs_lookup" not in src
