from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth import UserContext
from app.routers.campus_assistant import router


def test_non_admin_cannot_read_campus_config(monkeypatch):
    app = FastAPI()
    app.include_router(router)

    async def fake_user():
        return UserContext(user_id="u1", username="student", tenant_id="1", role_ids=[])

    from app.routers import campus_assistant
    app.dependency_overrides[campus_assistant.current_user] = fake_user
    client = TestClient(app)
    response = client.get("/campus-assistant/admin/config")
    assert response.status_code == 403


def test_non_admin_cannot_import_or_list_main_chat_skins():
    app = FastAPI()
    app.include_router(router)

    async def fake_user():
        return UserContext(user_id="u1", username="student", tenant_id="1", role_ids=[])

    from app.routers import campus_assistant
    app.dependency_overrides[campus_assistant.current_user] = fake_user
    client = TestClient(app)

    assert client.get("/campus-assistant/admin/skins").status_code == 403
    response = client.post(
        "/campus-assistant/admin/skins/import",
        files={"file": ("skin.axiomskin", b"not-a-package", "application/zip")},
    )
    assert response.status_code == 403
    assert client.get("/campus-assistant/admin/skins/mcs_1").status_code == 403
    assert client.patch(
        "/campus-assistant/admin/skins/mcs_1",
        json={"name": "新名称", "description": "备注"},
    ).status_code == 403
    assert client.delete("/campus-assistant/admin/skins/mcs_1").status_code == 403
