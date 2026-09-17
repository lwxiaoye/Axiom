from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_static_embed_session_rechecks_its_qze_key_and_public_switches(monkeypatch):
    """A session from a static iframe stops when its qze key or iframe switch is disabled."""
    from app.services.agent_api import embed_ticket_service as service_module

    service = service_module.EmbedTicketService(secret=b"test-secret", now=lambda: 1_000)
    principal = SimpleNamespace(
        key_id="embed-key-a", app_id="app-a", owner_user_id="publisher-a", origin="https://portal.example.com",
    )
    monkeypatch.setattr(service_module, "load_active_api_version", AsyncMock(return_value=SimpleNamespace(id="version-a")))
    session = await service.create_direct_session(principal)

    class _Db:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, model, identity):
            if model is service_module.AgentApiAccessKey:
                return SimpleNamespace(
                    status="active", app_id="app-a", owner_user_id="publisher-a", key_kind="embed", expires_at=None,
                )
            return SimpleNamespace(api_enabled=True, iframe_embed_enabled=True)

    monkeypatch.setattr(service_module, "async_session", lambda: _Db())
    assert (await service.authenticate_session(session.token)).app_id == "app-a"

    class _DisabledDb(_Db):
        async def get(self, model, identity):
            value = await super().get(model, identity)
            if model is not service_module.AgentApiAccessKey:
                value.iframe_embed_enabled = False
            return value

    monkeypatch.setattr(service_module, "async_session", lambda: _DisabledDb())
    with pytest.raises(service_module.EmbedTicketError, match="embed_key_revoked"):
        await service.authenticate_session(session.token)
