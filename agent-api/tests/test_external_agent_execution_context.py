from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.agent_api.access_service import AgentApiPrincipal
from app.services.agent_api.external_session_service import ExternalSessionHandle


PRINCIPAL = AgentApiPrincipal("key-a", "app-a", "publisher-a", "axa_test")
SESSION = ExternalSessionHandle(
    id="exts_a",
    app_id="app-a",
    api_key_id="key-a",
    owner_user_id="publisher-a",
    session_id="s1",
    workspace_ref="external/exts_a",
)


@pytest.mark.asyncio
async def test_api_execution_passes_external_workspace_to_root_and_child_context(monkeypatch):
    from app.services.agent_api import execution_service
    from app.services.gateway import tool_invoker
    from app.services.workflows import workflow_engine

    captured = {}

    class _Engine:
        def __init__(self, _graph, ctx):
            captured["child"] = ctx

        async def run(self):
            return None

    monkeypatch.setattr(tool_invoker, "load_tool_definition", AsyncMock(
        return_value='{"fastgpt":{"nodes":[{"nodeId":"start","flowNodeType":"workflowStart"}]}}'
    ))
    monkeypatch.setattr(workflow_engine, "WorkflowEngine", _Engine)

    root = execution_service._context_for_api_execution(
        PRINCIPAL,
        execution_service.ApiExecutionRequest(
            input_text="read attachment",
            session_id="s1",
            external_session=SESSION,
            file_ids=["extfile_a"],
        ),
        "publisher-key",
        "model-a",
    )
    await tool_invoker.run_sub_workflow("child-a", "child", "read attachment", parent_ctx=root)

    assert root.external_execution is True
    assert root.user_id == ""
    assert root.external_workspace_ref == SESSION.workspace_ref
    assert root.external_file_ids == ["extfile_a"]
    assert captured["child"].external_session_id == SESSION.id
    assert captured["child"].external_workspace_ref == SESSION.workspace_ref
