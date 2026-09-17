"""Exact clock access must be on-demand and schema-stable."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.core.config import settings
from app.services.chat.tools.time_context import build_time_tools, configured_business_time


def test_configured_business_time_uses_configured_timezone(monkeypatch) -> None:
    monkeypatch.setattr(settings, "AGENT_TIMEZONE", "Asia/Shanghai")

    snapshot = configured_business_time()
    parsed = datetime.fromisoformat(snapshot["iso8601"])

    assert snapshot["timezone"] == "Asia/Shanghai"
    assert snapshot["current_date"] == parsed.date().isoformat()
    assert parsed.utcoffset() is not None


def test_invalid_timezone_uses_existing_business_default(monkeypatch) -> None:
    monkeypatch.setattr(settings, "AGENT_TIMEZONE", "Mars/Olympus")

    snapshot = configured_business_time()

    assert snapshot["timezone"] == "Asia/Shanghai"
    assert datetime.fromisoformat(snapshot["iso8601"]).utcoffset() is not None


@pytest.mark.asyncio
async def test_get_current_time_tool_is_always_stable_and_read_only() -> None:
    first = build_time_tools()[0]
    second = build_time_tools()[0]

    assert first.name == second.name == "get_current_time"
    assert first.to_openai() == second.to_openai()
    assert first.readonly is True
    assert first.spec.effect_scope.value == "none"
    result = await first.execute({})
    assert "当前时间：" in result.model_content
    assert "时区：" in result.model_content
