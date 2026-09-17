from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI


@pytest.fixture
def app(monkeypatch):
    from app.routers import embed
    app = FastAPI()
    app.include_router(embed.router)
    return app


@pytest.mark.asyncio
async def test_static_frame_has_origin_specific_csp_and_does_not_expose_qze_secret(app, monkeypatch):
    from app.routers import embed

    monkeypatch.setattr(
        embed,
        "resolve_embed_key_frame",
        AsyncMock(return_value=SimpleNamespace(origin="https://portal.example.com")),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/embed/v1/frame/app-a?embedKeyId=ek_public#embedKey=qze_secret")

    assert response.status_code == 200
    assert "frame-ancestors https://portal.example.com" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "qze_secret" not in response.text
    assert "history.replaceState" in response.text


def test_embed_template_checks_parent_origin_before_theme():
    from app.services.agent_api.embed_page import _TEMPLATE_PATH

    page = _TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "event.origin !== parentOrigin" in page
    assert "event.source !== window.parent" in page
    assert "qz-agent.set-theme" in page
    for event_name in ("qz-agent.ready", "qz-agent.resize", "qz-agent.completed", "qz-agent.error"):
        assert event_name in page


@pytest.mark.asyncio
async def test_frame_hides_invalid_static_key_state(app, monkeypatch):
    from app.routers import embed
    from app.services.agent_api.access_service import ApiReleaseInactiveError

    monkeypatch.setattr(embed, "resolve_embed_key_frame", AsyncMock(side_effect=ApiReleaseInactiveError("api_release_inactive")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/embed/v1/frame/app-a?embedKeyId=ek_revoked")

    assert response.status_code == 404
    assert "revoked" not in response.text


@pytest.mark.asyncio
async def test_direct_embed_key_id_can_render_a_whitelisted_frame(app, monkeypatch):
    """A static embed URL uses a public key id; its qze secret stays in the fragment."""
    from app.routers import embed
    monkeypatch.setattr(
        embed,
        "resolve_embed_key_frame",
        AsyncMock(return_value=SimpleNamespace(origin="https://portal.example.com")),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/embed/v1/frame/app-a?embedKeyId=ek_public")

    assert response.status_code == 200
    assert "frame-ancestors https://portal.example.com" in response.headers["content-security-policy"]
    assert "embedKeyId" not in response.text
