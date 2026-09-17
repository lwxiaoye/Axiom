from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI


@pytest.fixture
def app():
    from app.routers import embed

    app = FastAPI()
    app.include_router(embed.router)
    return app


def test_ticket_endpoints_are_not_exposed():
    """Only the static qze iframe flow is public; ticket issuance was removed."""
    from app.routers import embed

    paths = {route.path for route in embed.router.routes}

    assert "/embed/v1/agents/{app_id}/tickets" not in paths
    assert "/embed/v1/tickets/exchange" not in paths


@pytest.mark.asyncio
async def test_embed_run_only_accepts_short_embed_token_and_marks_embed_source(app, monkeypatch):
    from app.routers import embed

    session = SimpleNamespace(
        app_id="app-a", version_id="version-a", key_id="key-a", owner_user_id="publisher-a",
        origin="https://portal.example.com", expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    monkeypatch.setattr(embed.embed_ticket_service, "authenticate_session", AsyncMock(return_value=session))
    handle = SimpleNamespace(attribution="attribution")
    monkeypatch.setattr(embed, "begin_invocation", AsyncMock(return_value=handle))
    monkeypatch.setattr(embed, "get_or_create_external_session", AsyncMock(return_value=SimpleNamespace(
        id="exts_a", app_id="app-a", api_key_id="key-a", owner_user_id="publisher-a",
        session_id="chat-1", workspace_ref="external/exts_a",
    )))
    execute = AsyncMock(return_value={"status": "success", "output": "hello"})
    monkeypatch.setattr(embed, "execute_published_api_workflow", execute)
    monkeypatch.setattr(embed, "finish_invocation", AsyncMock())

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post(
            "/embed/v1/runs", headers={"Authorization": "Bearer qza_example"}, json={"input": "hello"},
        )
        accepted = await client.post(
            "/embed/v1/runs", headers={"Authorization": "Embed emb_short_token"}, json={"input": "hello", "sessionId": "chat-1"},
        )

    assert denied.status_code == 401
    assert accepted.status_code == 200
    request = execute.await_args.args[1]
    assert request.source == "embed"
    assert request.session_id == "chat-1"
    assert request.attribution == "attribution"
    assert request.external_session.id == "exts_a"
    assert accepted.headers["cache-control"] == "no-store"
    assert accepted.headers["referrer-policy"] == "no-referrer"


@pytest.mark.asyncio
async def test_embed_run_rejects_model_controls_and_revoked_embed_token(app, monkeypatch):
    from app.routers import embed

    monkeypatch.setattr(
        embed.embed_ticket_service,
        "authenticate_session",
        AsyncMock(side_effect=embed.EmbedTicketError("embed_key_revoked")),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        controlled = await client.post(
            "/embed/v1/runs", headers={"Authorization": "Embed emb_short_token"}, json={"input": "hello", "model": "x"},
        )
        revoked = await client.post(
            "/embed/v1/runs", headers={"Authorization": "Embed emb_short_token"}, json={"input": "hello"},
        )

    assert controlled.status_code == 400
    assert controlled.json()["error"]["code"] == "unsupported_parameter"
    assert revoked.status_code == 403
    assert revoked.json()["error"]["code"] == "embed_key_revoked"


@pytest.mark.asyncio
async def test_direct_embed_session_accepts_only_a_scoped_qze_key(app, monkeypatch):
    from app.routers import embed

    principal = SimpleNamespace(
        key_id="embed-key-a", app_id="app-a", owner_user_id="publisher-a", origin="https://portal.example.com",
    )
    monkeypatch.setattr(embed, "authenticate_embed_key", AsyncMock(return_value=principal))
    monkeypatch.setattr(
        embed.embed_ticket_service,
        "create_direct_session",
        AsyncMock(return_value=SimpleNamespace(token="emb_short", expires_at=datetime.now(timezone.utc))),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post("/embed/v1/agents/app-a/direct-sessions", headers={"Authorization": "Bearer qza_example"})
        accepted = await client.post("/embed/v1/agents/app-a/direct-sessions", headers={"Authorization": "Embed-Key qze_example"})

    assert denied.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["token"] == "emb_short"
