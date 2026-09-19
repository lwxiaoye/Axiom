from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.routers import workflow


@pytest.mark.asyncio
async def test_admin_detail_requires_manage_permission(monkeypatch):
    denied = AsyncMock(side_effect=HTTPException(403, "无智能体管理权限"))
    monkeypatch.setattr(workflow, "_require_agent_manage_permission", denied)

    with pytest.raises(HTTPException) as exc:
        await workflow.admin_app_detail("app-1", user=SimpleNamespace(user_id="viewer"))

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_version_diff_rejects_a_version_from_another_application(monkeypatch):
    @asynccontextmanager
    async def fake_session_context():
        yield SimpleNamespace()

    monkeypatch.setattr(workflow, "async_session", fake_session_context)
    monkeypatch.setattr(workflow, "_require_agent_manage_permission", AsyncMock())
    monkeypatch.setattr(workflow, "_get_app", AsyncMock(return_value=SimpleNamespace(id="app-1")))
    monkeypatch.setattr(
        workflow,
        "_load_version_pair",
        AsyncMock(side_effect=HTTPException(400, "版本不属于该应用")),
        raising=False,
    )

    with pytest.raises(HTTPException, match="版本不属于该应用"):
        await workflow.admin_app_version_diff("app-1", 1, 2, user=SimpleNamespace(user_id="admin"))


@pytest.mark.asyncio
async def test_restore_requires_an_unpublished_application(monkeypatch):
    @asynccontextmanager
    async def fake_session_context():
        yield SimpleNamespace()

    monkeypatch.setattr(workflow, "async_session", fake_session_context)
    monkeypatch.setattr(workflow, "_require_agent_manage_permission", AsyncMock())
    monkeypatch.setattr(workflow, "_get_app", AsyncMock(return_value=SimpleNamespace(id="app-1", status="published")))

    with pytest.raises(HTTPException, match="已下架"):
        await workflow.admin_restore(
            workflow.AdminRestoreRequest(appId="app-1"),
            user=SimpleNamespace(user_id="admin"),
        )


def test_admin_detail_removes_the_raw_config_json_from_its_response():
    source = Path(workflow.__file__).read_text(encoding="utf-8")
    detail_handler = source[source.index("async def admin_app_detail"):source.index("async def owner_app_evaluation_runs")]

    assert 'app_data.pop("configJson", None)' in detail_handler
    assert "load_user_display_names(" in detail_handler
    assert '"ownerUsername": display_names.get(app.owner_user_id, app.owner_username or "")' in detail_handler


def test_application_operation_audit_covers_owner_editor_reviewer_and_admin_paths():
    source = Path(workflow.__file__).read_text(encoding="utf-8")

    assert "record_app_audit" in source
    for action in (
        'action="update_app"',
        'action="save_draft"',
        'action="submit_review"',
        'action="publish"',
        'action="approve_review"',
        'action="reject_review"',
        'action="cancel_review"',
        'action="unpublish"',
        'action="restore"',
        'action="rollback"',
        'action="delete"',
    ):
        assert action in source
