from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.core.auth import UserContext


OWNER = UserContext(user_id="publisher-a", username="publisher")


@pytest.fixture
def app(monkeypatch):
    from app.routers import agent_api_management as management

    app = FastAPI()
    app.include_router(management.router)
    app.dependency_overrides[management.current_user] = lambda: OWNER
    monkeypatch.setattr(management, "_require_owner", AsyncMock())
    return app


@pytest.mark.asyncio
async def test_owner_key_create_and_list_return_recoverable_secret_without_leaking_storage_fields(app, monkeypatch):
    from app.routers import agent_api_management as management

    monkeypatch.setattr(
        management, "create_key",
        AsyncMock(return_value=SimpleNamespace(
            id="key-a", name="prod", prefix="qza_example", secret="qza_secret_once", expires_at=None,
        )),
    )
    monkeypatch.setattr(
        management, "list_keys",
        AsyncMock(return_value=[SimpleNamespace(
            id="key-a", name="prod", key_prefix="qza_example", status="active", expires_at=None,
            last_used_at=None, revoked_at=None, created_at=datetime.now(timezone.utc),
            secret_hash="must-not-leak", secret_ciphertext="ciphertext-must-not-leak",
        )]),
    )
    monkeypatch.setattr(management, "recover_key_secret", lambda _row: "qza_recovered_secret")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/workflow/apps/app-a/api-keys", json={"name": "prod"})
        listed = await client.get("/workflow/apps/app-a/api-keys")

    assert created.status_code == 201
    assert created.json()["secret"].startswith("qza_")
    assert listed.json()["items"][0]["secret"] == "qza_recovered_secret"
    assert "secret_hash" not in listed.text
    assert "secret_ciphertext" not in listed.text


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["patch", "put"])
async def test_key_enablement_is_scoped_to_the_addressed_application(app, monkeypatch, method):
    from app.routers import agent_api_management as management

    update_status = AsyncMock(return_value=True)
    monkeypatch.setattr(management, "set_key_enabled_for_app", update_status)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await getattr(client, method)(
            "/workflow/apps/app-a/api-keys/key-a/status", json={"enabled": False}
        )

    assert response.status_code == 204
    update_status.assert_awaited_once_with("key-a", "app-a", "publisher-a", "api", enabled=False)


@pytest.mark.asyncio
async def test_key_delete_is_scoped_to_the_addressed_application(app, monkeypatch):
    from app.routers import agent_api_management as management

    delete = AsyncMock(return_value=True)
    monkeypatch.setattr(management, "delete_key_for_app", delete)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/workflow/apps/app-a/api-keys/key-a")

    assert response.status_code == 204
    delete.assert_awaited_once_with("key-a", "app-a", "publisher-a", "api")


@pytest.mark.asyncio
async def test_owner_can_create_an_origin_bound_embed_key(app, monkeypatch):
    from app.routers import agent_api_management as management

    monkeypatch.setattr(management, "create_embed_key", AsyncMock(return_value=SimpleNamespace(
        id="embed-a", name="portal", prefix="qze_example", secret="qze_secret_once", expires_at=None,
    )))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/workflow/apps/app-a/embed-keys",
            json={"name": "portal", "origin": "https://portal.example.com"},
        )

    assert response.status_code == 201
    assert response.json()["secret"].startswith("qze_")

@pytest.mark.asyncio
async def test_owner_can_read_and_update_public_api_configuration(app, monkeypatch):
    """API and static iframe exposure are app-level switches, not publish channels."""
    from app.routers import agent_api_management as management

    monkeypatch.setattr(
        management,
        "get_public_configuration",
        AsyncMock(return_value={"apiEnabled": False, "iframeEnabled": False}),
    )
    update = AsyncMock(return_value={"apiEnabled": True, "iframeEnabled": True})
    monkeypatch.setattr(management, "update_public_configuration", update)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        current = await client.get("/workflow/apps/app-a/public-config")
        updated = await client.put(
            "/workflow/apps/app-a/public-config",
            json={"apiEnabled": True, "iframeEnabled": True},
        )

    assert current.status_code == 200
    assert current.json() == {"apiEnabled": False, "iframeEnabled": False}
    assert updated.status_code == 200
    update.assert_awaited_once_with("app-a", "publisher-a", True, True)
