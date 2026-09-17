from types import SimpleNamespace

import pytest

from app.routers import workflow


@pytest.mark.asyncio
async def test_update_publish_inherits_only_omitted_visibility_fields(monkeypatch):
    """更新发布默认保留当前线上可见范围，显式传空数组仍可取消授权。"""

    async def live_version(*_args, **_kwargs):
        return SimpleNamespace(
            visible_role_ids='["role-student"]',
            visible_dept_ids='["dept-academic"]',
        )

    monkeypatch.setattr(workflow, "load_published_visibility_version", live_version)

    omitted = workflow.DefinitionSaveRequest(appId="app-1", workflowJson="{}")
    assert "visibleRoleIds" not in omitted.model_fields_set
    assert "visibleDeptIds" not in omitted.model_fields_set
    roles, departments = await workflow._resolve_publish_visibility(
        object(),
        "app-1",
        omitted.model_fields_set,
        omitted.visibleRoleIds,
        omitted.visibleDeptIds,
    )
    assert roles == ["role-student"]
    assert departments == ["dept-academic"]

    explicit_clear = workflow.DefinitionSaveRequest(
        appId="app-1",
        workflowJson="{}",
        visibleRoleIds=[],
        visibleDeptIds=[],
    )
    roles, departments = await workflow._resolve_publish_visibility(
        object(),
        "app-1",
        explicit_clear.model_fields_set,
        explicit_clear.visibleRoleIds,
        explicit_clear.visibleDeptIds,
    )
    assert roles == []
    assert departments == []
