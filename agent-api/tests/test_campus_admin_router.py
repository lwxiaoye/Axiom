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
async def test_non_admin_cannot_import_or_list_main_chat_skins():
    app = FastAPI()
    app.include_router(router)

    async def fake_user():
        return UserContext(user_id="u1", username="student", tenant_id="1", role_ids=[])

    from app.routers import campus_assistant
    app.dependency_overrides[campus_assistant.current_user] = fake_user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/campus-assistant/admin/skins")).status_code == 403
        response = await client.post(
            "/campus-assistant/admin/skins/import",
            files={"file": ("skin.axiomskin", b"not-a-package", "application/zip")},
        )
        assert response.status_code == 403
        assert (await client.get("/campus-assistant/admin/skins/mcs_1")).status_code == 403
        assert (
            await client.patch(
                "/campus-assistant/admin/skins/mcs_1",
                json={"name": "新名称", "description": "备注"},
            )
        ).status_code == 403
        assert (await client.delete("/campus-assistant/admin/skins/mcs_1")).status_code == 403
