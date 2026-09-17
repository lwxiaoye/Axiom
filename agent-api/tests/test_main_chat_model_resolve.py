from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.schemas.schemas import ModelItem
from app.services.agent_harness.orchestrator import harness_orchestrator
from app.services.agents.agent_service import agent_service


def _mock_models(monkeypatch, models: list[ModelItem]) -> None:
    monkeypatch.setattr(agent_service, "get_models", AsyncMock(return_value=models))


@pytest.mark.asyncio
async def test_resolve_model_honors_requested_model(monkeypatch):
    _mock_models(
        monkeypatch,
        [
            ModelItem(id="deepseek-v4-flash", name="deepseek-v4-flash", is_default=True),
            ModelItem(id="gpt-5.5", name="gpt-5.5", is_default=False),
        ],
    )

    resolved = await harness_orchestrator._resolve_model("gpt-5.5", user_key="test-key")

    assert resolved == "gpt-5.5"


@pytest.mark.asyncio
async def test_resolve_model_falls_back_to_default(monkeypatch):
    _mock_models(
        monkeypatch,
        [
            ModelItem(id="gpt-5.5", name="gpt-5.5", is_default=False),
            ModelItem(id="deepseek-v4-flash", name="deepseek-v4-flash", is_default=True),
        ],
    )

    resolved = await harness_orchestrator._resolve_model(None, user_key="test-key")

    assert resolved == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_resolve_model_rejects_unavailable_model(monkeypatch):
    _mock_models(
        monkeypatch,
        [ModelItem(id="deepseek-v4-flash", name="deepseek-v4-flash", is_default=True)],
    )

    with pytest.raises(HTTPException) as exc_info:
        await harness_orchestrator._resolve_model("gpt-5.5", user_key="test-key")

    assert exc_info.value.status_code == 400
