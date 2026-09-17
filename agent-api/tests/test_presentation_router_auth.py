import httpx
import pytest
from fastapi import FastAPI

from app.core.auth import UserContext
from app.routers.workflow import current_user, router


def _app(user_factory=None):
    app = FastAPI()
    app.include_router(router)
    if user_factory is not None:
        app.dependency_overrides[current_user] = user_factory
    return app


@pytest.mark.asyncio
async def test_unauthenticated_cannot_manage_global_sub_agent_skin_catalog():
    app = _app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/workflow/presentation/admin/presets")).status_code == 401


@pytest.mark.asyncio
async def test_non_platform_admin_cannot_manage_global_sub_agent_skin_catalog():
    async def fake_user():
        return UserContext(user_id="u1", username="customer-editor", tenant_id="1", role_ids=[])

    app = _app(fake_user)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/workflow/presentation/admin/presets")).status_code == 403
        assert (await client.get("/workflow/presentation/admin/skins/sas_1")).status_code == 403
        assert (
            await client.patch(
                "/workflow/presentation/admin/skins/sas_1",
                json={"name": "新名称", "description": "备注"},
            )
        ).status_code == 403
        assert (await client.delete("/workflow/presentation/admin/skins/sas_1")).status_code == 403


@pytest.mark.asyncio
async def test_non_platform_admin_cannot_import_sub_agent_skin_package():
    async def fake_user():
        return UserContext(user_id="u1", username="customer-editor", tenant_id="1", role_ids=[])

    app = _app(fake_user)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/workflow/presentation/admin/skins/import",
            files={"file": ("skin.axiomskin", b"not-a-package", "application/zip")},
        )
    assert response.status_code == 403
