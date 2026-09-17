"""首字延迟预算：准备/预检超时不得把对话拖成空白十几秒。"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.services.chat import turn_prepare


def test_latency_budget_defaults_are_tight():
    """默认配置必须把同步等待压在数秒内，而不是 20s 级空白。"""
    assert float(settings.TURN_PREPARE_BUDGET_SECONDS) <= 3.0
    assert float(settings.MAIN_TOOL_PREFLIGHT_BUDGET_SECONDS) <= 4.0
    assert int(settings.CONTEXT_COMPACT_TIMEOUT_SECONDS) <= 6
    assert int(settings.KNOWLEDGE_PRE_RETRIEVE_TIMEOUT_SECONDS) <= 8


@pytest.mark.asyncio
async def test_prepare_turn_respects_budget_and_returns():
    """catalog/记忆再慢，prepare_turn 也必须在预算附近返回，不能卡死。"""

    async def slow_catalog(*_a, **_k):
        await asyncio.sleep(5)
        return []

    with (
        patch.object(turn_prepare, "_get_catalog_records", AsyncMock(side_effect=slow_catalog)),
        patch.object(turn_prepare, "_retrieve_agents", AsyncMock(return_value=[])),
        patch.object(turn_prepare.memory_service, "recall", AsyncMock(return_value=[])),
        patch.object(turn_prepare.personalization_service, "prompt_block", AsyncMock(return_value="")),
        patch.object(turn_prepare, "_fetch_trusted_skills", AsyncMock(return_value=[])),
        patch.object(turn_prepare.settings, "TURN_PREPARE_BUDGET_SECONDS", 0.6),
        patch.object(turn_prepare.settings, "AUTO_ROUTE_ENABLED", False),
    ):
        t0 = asyncio.get_event_loop().time()
        ctx = await turn_prepare.prepare_turn(
            message="帮我做一份 200 元人体工学椅市场调研",
            user_context=None,
            subagent_id=None,
            knowledge_ids=None,
            selected_knowledge=None,
            web_search=False,
            image_urls=[],
            resolved_model="m",
            newapi_key="k",
            skill_ids=None,
            token="t",
            user_id="u",
            thread_id="th",
        )
        elapsed = asyncio.get_event_loop().time() - t0

    assert ctx is not None
    # 预算 0.6 + 超时后 0.4 目录补救 ≈ 1s 级；绝不能接近 5s
    assert elapsed < 2.0, f"prepare_turn 超时未生效: {elapsed:.2f}s"
