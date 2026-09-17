from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import workflow


@pytest.mark.asyncio
async def test_metrics_route_requires_application_owner(monkeypatch):
    @asynccontextmanager
    async def fake_session_context():
        yield object()

    async def deny_owner(*_args, **_kwargs):
        raise HTTPException(403, "仅应用拥有者可执行该操作")

    monkeypatch.setattr(workflow, "async_session", fake_session_context)
    monkeypatch.setattr(workflow, "_require_permission", deny_owner)

    with pytest.raises(HTTPException) as exc:
        await workflow.app_metrics("app-1", "today", user=SimpleNamespace(user_id="viewer"))

    assert exc.value.status_code == 403
