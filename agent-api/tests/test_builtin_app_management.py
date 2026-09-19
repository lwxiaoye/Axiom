from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import WorkflowApp
from app.routers import workflow
from app.services.chat import builtin_app_access as catalog
from app.services.workflows.builtin_app_admin_service import (
    build_builtin_admin_detail,
    get_managed_builtin,
    list_managed_builtins,
    managed_builtin_origin,
)


class AsyncSessionAdapter:
    def __init__(self, session):
        self.session = session

    async def execute(self, statement, params=None):
        return self.session.execute(statement, params or {})


@pytest.fixture
def catalogue_rows(monkeypatch):
    rows = [
        {"id": "campus", "app_name": "校内问答", "app_type": "custom", "pc_url": "/center/chat/campus/", "status": "1", "del_flag": "0", "create_by": "owner"},
        {"id": "ppt", "app_name": "演示文稿助手", "app_type": "external", "pc_url": "/center/chat/ppt", "status": "0", "del_flag": "0", "create_by": "owner"},
        {"id": "interview", "app_type": "custom", "h5_url": "/center/chat/interview", "status": "1", "del_flag": "0", "create_by": "owner"},
        {"id": "deleted", "app_type": "custom", "pc_url": "/center/chat/campus", "status": "1", "del_flag": "1"},
        {"id": "ordinary", "app_type": "external", "pc_url": "/somewhere", "status": "1"},
        {"id": "wrong-type", "app_type": "agent", "pc_url": "/center/chat/campus", "status": "1"},
        {"id": "ambiguous", "app_type": "custom", "pc_url": "/center/chat/campus", "h5_url": "/center/chat/ppt", "status": "1"},
    ]
    monkeypatch.setattr(catalog, "_load_catalog_rows", AsyncMock(return_value=rows))
    monkeypatch.setattr(catalog, "_load_creator_profiles", AsyncMock(return_value={"owner": ("管理员姓名", "")}))
    return rows


@pytest.mark.asyncio
async def test_catalogue_projection_preserves_disabled_records_and_filters(catalogue_rows):
    records = await list_managed_builtins(object())
    assert [item["catalogAppId"] for item in records] == ["campus", "ppt", "interview"]
    assert records[0]["name"] == "校内问答"
    assert records[1]["status"] == "unpublished"
    assert len(await list_managed_builtins(object(), keyword="管理员姓名")) == 3
    assert len(await list_managed_builtins(object(), status="published")) == 2
    assert await list_managed_builtins(object(), owner_user_id="other") == []
    assert len(await list_managed_builtins(object(), keyword="演示")) == 1
    assert managed_builtin_origin(records[0]) == "campus_services"
    detail = build_builtin_admin_detail(records[0])
    assert detail["versions"] == []
    assert detail["summary"]["publishedVersion"] == 0
    assert "configJson" not in detail["app"] and "formOptions" not in detail["app"]
    with pytest.raises(HTTPException) as error:
        await get_managed_builtin(object(), "builtin-catalog:deleted")
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_management_combines_sources_before_paging_and_type_filters(monkeypatch, catalogue_rows):
    engine = create_engine("sqlite:///:memory:")
    WorkflowApp.__table__.create(engine)
    with Session(engine) as session:
        session.add_all([
            WorkflowApp(id=f"app-{i}", ai_app_type="workflow", name=f"工作流 {i}", owner_user_id="owner", status="published", update_time=datetime(2026, 9, 11) - timedelta(days=i))
            for i in range(4)
        ])
        session.commit()

        @asynccontextmanager
        async def session_context():
            yield AsyncSessionAdapter(session)

        monkeypatch.setattr(workflow, "async_session", session_context)
        monkeypatch.setattr(workflow, "_require_agent_manage_permission", AsyncMock())
        monkeypatch.setattr(workflow, "load_user_display_names", AsyncMock(return_value={"owner": "管理员姓名"}))
        user = SimpleNamespace(user_id="admin")
        pages = [await workflow.admin_app_page(pageNo=n, pageSize=2, aiAppTypes="builtin,chatAgent,workflow", user=user) for n in range(1, 5)]
        assert [item["id"] for page in pages for item in page["records"]] == [
            "builtin-catalog:campus", "builtin-catalog:ppt", "builtin-catalog:interview", "app-0", "app-1", "app-2", "app-3",
        ]
        assert all(page["total"] == 7 for page in pages)
        builtin = await workflow.admin_app_page(aiAppType="builtin", user=user)
        assert builtin["total"] == 3
        ordinary = await workflow.admin_app_page(aiAppType="workflow", user=user)
        assert ordinary["total"] == 4
        published = await workflow.admin_app_page(status="published", user=user)
        assert published["total"] == 6
        for kwargs in ({"pageNo": 0}, {"pageSize": 101}):
            with pytest.raises(HTTPException):
                await workflow.admin_app_page(user=user, **kwargs)
    engine.dispose()


@pytest.mark.asyncio
async def test_builtin_management_requires_existing_admin_permission(monkeypatch):
    monkeypatch.setattr(workflow, "_require_agent_manage_permission", AsyncMock(side_effect=HTTPException(403, "forbidden")))
    with pytest.raises(HTTPException) as error:
        await workflow.admin_app_detail("builtin-catalog:campus", user=SimpleNamespace(user_id="viewer"))
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_detail_resolves_the_catalogue_record(monkeypatch, catalogue_rows):
    @asynccontextmanager
    async def session_context():
        yield object()

    monkeypatch.setattr(workflow, "async_session", session_context)
    monkeypatch.setattr(workflow, "_require_agent_manage_permission", AsyncMock())
    user = SimpleNamespace(user_id="admin")
    detail = await workflow.admin_app_detail("builtin-catalog:ppt", user=user)
    assert detail["app"]["catalogAppId"] == "ppt"
    assert detail["app"]["status"] == "unpublished"
    with pytest.raises(HTTPException) as error:
        await workflow.admin_app_detail("builtin-catalog:deleted", user=user)
    assert error.value.status_code == 404
