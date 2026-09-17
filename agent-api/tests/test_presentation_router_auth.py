from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth import UserContext
from app.routers.workflow import current_user, router


def test_non_platform_admin_cannot_manage_global_sub_agent_skin_catalog():
    app = FastAPI()
    app.include_router(router)

    async def fake_user():
        return UserContext(user_id="u1", username="customer-editor", tenant_id="1", role_ids=[])

    app.dependency_overrides[current_user] = fake_user
    client = TestClient(app)
    assert client.get("/workflow/presentation/admin/presets").status_code == 403
    assert client.get("/workflow/presentation/admin/skins/sas_1").status_code == 403
    assert client.patch(
        "/workflow/presentation/admin/skins/sas_1",
        json={"name": "新名称", "description": "备注"},
    ).status_code == 403
    assert client.delete("/workflow/presentation/admin/skins/sas_1").status_code == 403


def test_non_platform_admin_cannot_import_sub_agent_skin_package():
    app = FastAPI()
    app.include_router(router)

    async def fake_user():
        return UserContext(user_id="u1", username="customer-editor", tenant_id="1", role_ids=[])

    app.dependency_overrides[current_user] = fake_user
    client = TestClient(app)
    response = client.post(
        "/workflow/presentation/admin/skins/import",
        files={"file": ("skin.qzskin", b"not-a-package", "application/zip")},
    )

    assert response.status_code == 403
