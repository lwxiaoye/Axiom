from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.core.auth import UserContext
from app.routers.campus_assistant import router


def _app(user_factory=None):
    app = FastAPI()
    app.include_router(router)
    if user_factory is not None:
        from app.routers import campus_assistant
        app.dependency_overrides[campus_assistant.current_user] = user_factory
    return app


async def _get(app, path):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_unauthenticated_cannot_read_campus_config():
    response = await _get(_app(), "/campus-assistant/admin/config")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_non_admin_cannot_read_campus_config():
    async def fake_user():
        return UserContext(user_id="u1", username="student", tenant_id="1", role_ids=[])

    response = await _get(_app(fake_user), "/campus-assistant/admin/config")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_read_campus_config(monkeypatch):
    from app.routers import campus_assistant

    async def fake_admin():
        return UserContext(user_id="1", username="admin", tenant_id="1", role_ids=["admin"])

    monkeypatch.setattr(
        campus_assistant.config_service,
        "get_or_create_config",
        AsyncMock(return_value={"id": "campus-config"}),
    )
    response = await _get(_app(fake_admin), "/campus-assistant/admin/config")
    assert response.status_code == 200
    assert response.json()["id"] == "campus-config"


@pytest.mark.asyncio
async def test_main_chat_skin_routes_are_gone():
    """皮肤系统已删除：运行时 / 管理端的皮肤接口一律 404，而不是被别的路由兜住。"""
    from app.routers import campus_assistant

    async def fake_admin():
        return UserContext(user_id="1", username="admin", tenant_id="1", role_ids=["admin"])

    app = _app(fake_admin)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/campus-assistant/runtime/skin")).status_code == 404
        assert (await client.get("/campus-assistant/skins/mcs_1/assets/background")).status_code == 404
        assert (await client.get("/campus-assistant/admin/skins")).status_code == 404
        assert (await client.get("/campus-assistant/admin/skins/mcs_1")).status_code == 404
        assert (await client.delete("/campus-assistant/admin/skins/mcs_1")).status_code == 404
        assert (
            await client.post(
                "/campus-assistant/admin/skins/import",
                files={"file": ("skin.axiomskin", b"not-a-package", "application/zip")},
            )
        ).status_code == 404
    assert not hasattr(campus_assistant, "skin_service")


@pytest.mark.asyncio
async def test_draft_requests_ignore_legacy_skin_field_instead_of_422(monkeypatch):
    """接口契约：旧前端 / 旧草稿仍可能带 main_chat_skin_id，后端静默忽略而不是 422。"""
    from app.routers import campus_assistant

    async def fake_admin():
        return UserContext(user_id="1", username="admin", tenant_id="1", role_ids=["admin"])

    seen = {}

    async def fake_save_draft(_user, body):
        seen["save"] = body
        return {"ok": True}

    async def fake_validate_draft(_user, body):
        seen["validate"] = body
        return {"errors": [], "warnings": []}

    monkeypatch.setattr(campus_assistant.config_service, "save_draft", fake_save_draft)
    monkeypatch.setattr(campus_assistant.config_service, "validate_draft", fake_validate_draft)
    app = _app(fake_admin)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/campus-assistant/admin/draft",
            json={
                "expected_revision": 1,
                "model_id": "m1",
                "main_chat_skin_id": "mcs_1",
                "knowledge_bindings": [{"knowledge_id": "kb1"}],
            },
        )
        assert response.status_code == 200, response.text
        response = await client.post(
            "/campus-assistant/admin/draft/validate",
            json={"model_id": "m1", "main_chat_skin_id": "mcs_1"},
        )
        assert response.status_code == 200, response.text

    assert not hasattr(seen["save"], "main_chat_skin_id")
    assert "main_chat_skin_id" not in seen["save"].model_dump()
    assert "main_chat_skin_id" not in seen["validate"].model_dump()
