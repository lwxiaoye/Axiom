from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.agent_api.access_service import AgentApiPrincipal
from app.services.agent_harness.model_usage_audit import ExternalAttribution
from app.services.workflows.workflow_engine import NodeRun, RunContext, WorkflowEngine


PRINCIPAL = AgentApiPrincipal("key-a", "app-a", "publisher-a", "axa_test")
VERSION = SimpleNamespace(
    id="version-a",
    app_id="app-a",
    owner_user_id="publisher-a",
    definition_json='{"fastgpt":{"nodes":[{"nodeId":"start","flowNodeType":"workflowStart"}]}}',
)


@pytest.mark.asyncio
async def test_execution_uses_only_the_publisher_key_and_fixed_version(monkeypatch):
    """A caller supplied workflow or credential must never reach the engine."""
    from app.services.agent_api import execution_service

    seen = {}
    monkeypatch.setattr(execution_service, "load_active_api_version", AsyncMock(return_value=VERSION))
    monkeypatch.setattr(execution_service.key_service, "get_user_key", AsyncMock(return_value="publisher-key"))
    monkeypatch.setattr(
        execution_service,
        "_resolve_owner_model_configuration",
        AsyncMock(return_value="publisher-default"),
    )

    async def _run(version, ctx):
        seen["version"] = version
        seen["ctx"] = ctx
        return {"status": "success", "output": "done", "runId": ctx.run_id}

    monkeypatch.setattr(execution_service, "_run_api_version", _run)
    attribution = ExternalAttribution("inv-a", "key-a", "app-a", "publisher-a")
    result = await execution_service.execute_published_api_workflow(
        PRINCIPAL,
        execution_service.ApiExecutionRequest(
            input_text="hello",
            histories=[{"role": "assistant", "content": "earlier"}],
            session_id="chat-1",
            source="openai_api",
            attribution=attribution,
        ),
    )

    assert result["output"] == "done"
    execution_service.key_service.get_user_key.assert_awaited_once_with("publisher-a")
    assert seen["version"] is VERSION
    assert seen["ctx"].llm_api_key == "publisher-key"
    assert seen["ctx"].default_model == "publisher-default"
    assert seen["ctx"].thread_id == "api:key-a:chat-1"
    assert seen["ctx"].token == ""
    assert seen["ctx"].external_attribution == attribution


@pytest.mark.asyncio
async def test_invalid_api_session_is_rejected_before_loading_any_platform_thread(monkeypatch):
    from app.services.agent_api import execution_service

    load_version = AsyncMock()
    monkeypatch.setattr(execution_service, "load_active_api_version", load_version)

    with pytest.raises(execution_service.ApiExecutionError, match="invalid_session"):
        await execution_service.execute_published_api_workflow(
            PRINCIPAL,
            execution_service.ApiExecutionRequest("hello", [], "platform/thread", "openai_api"),
        )

    load_version.assert_not_awaited()


@pytest.mark.asyncio
async def test_api_execution_rejects_a_runtime_interaction_capability(monkeypatch):
    from app.services.agent_api import execution_service

    interactive = SimpleNamespace(**{
        **VERSION.__dict__,
        "definition_json": '{"fastgpt":{"nodes":[{"flowNodeType":"userSelect"}]}}',
    })
    monkeypatch.setattr(execution_service, "load_active_api_version", AsyncMock(return_value=interactive))

    with pytest.raises(execution_service.ApiExecutionError, match="api_runtime_capability_unsupported"):
        await execution_service.execute_published_api_workflow(
            PRINCIPAL,
            execution_service.ApiExecutionRequest("hello", [], "chat-1", "openai_api"),
        )


@pytest.mark.asyncio
async def test_api_execution_fails_closed_for_an_unknown_runtime_capability(monkeypatch):
    from app.services.agent_api import execution_service

    unknown = SimpleNamespace(**{
        **VERSION.__dict__,
        "definition_json": '{"fastgpt":{"nodes":[{"flowNodeType":"futurePrivateNode"}]}}',
    })
    monkeypatch.setattr(execution_service, "load_active_api_version", AsyncMock(return_value=unknown))

    with pytest.raises(execution_service.ApiExecutionError, match="api_runtime_capability_unsupported"):
        await execution_service.execute_published_api_workflow(
            PRINCIPAL,
            execution_service.ApiExecutionRequest("hello", [], "chat-1", "openai_api"),
        )


@pytest.mark.asyncio
async def test_api_execution_rejects_a_restricted_nested_app_before_reading_owner_key(monkeypatch):
    from app.services.agent_api import execution_service

    child_app = SimpleNamespace(id="child-a", status="published", owner_user_id="publisher-a")
    child_version = SimpleNamespace(
        id="version-child",
        app_id="child-a",
        publish_channels='["api"]',
        definition_json='{"fastgpt":{"nodes":[{"flowNodeType":"userSelect"}]}}',
    )
    root = SimpleNamespace(**{
        **VERSION.__dict__,
        "definition_json": (
            '{"fastgpt":{"nodes":['
            '{"nodeId":"start","flowNodeType":"workflowStart"},'
            '{"nodeId":"child","flowNodeType":"appModule","pluginId":"child-a"}'
            ']}}'
        ),
    })

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, model, app_id):
            assert model is execution_service.WorkflowApp
            return child_app if app_id == "child-a" else None

    monkeypatch.setattr(execution_service, "load_active_api_version", AsyncMock(return_value=root))
    monkeypatch.setattr(execution_service, "async_session", lambda: _Session())
    monkeypatch.setattr(
        execution_service,
        "load_published_visibility_version",
        AsyncMock(return_value=child_version),
    )
    monkeypatch.setattr(
        execution_service,
        "load_published_definition",
        AsyncMock(return_value=child_version.definition_json),
    )
    owner_key = AsyncMock(return_value="publisher-key")
    monkeypatch.setattr(execution_service.key_service, "get_user_key", owner_key)

    with pytest.raises(execution_service.ApiExecutionError, match="api_runtime_capability_unsupported"):
        await execution_service.execute_published_api_workflow(
            PRINCIPAL,
            execution_service.ApiExecutionRequest("hello", [], "chat-1", "openai_api"),
        )

    owner_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_api_plugin_child_with_no_published_definition_rejects_before_owner_key(monkeypatch):
    from app.services.agent_api import execution_service

    child_app = SimpleNamespace(id="child-a", status="published", owner_user_id="publisher-a")
    child_version = SimpleNamespace(
        id="version-child", app_id="child-a", publish_channels='["api"]',
        definition_json='{"fastgpt":{"nodes":[{"nodeId":"start","flowNodeType":"workflowStart"}]}}',
    )
    root = SimpleNamespace(**{
        **VERSION.__dict__,
        "definition_json": (
            '{"fastgpt":{"nodes":['
            '{"nodeId":"start","flowNodeType":"workflowStart"},'
            '{"nodeId":"child","flowNodeType":"pluginModule","pluginId":"child-a"}'
            ']}}'
        ),
    })

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _model, app_id):
            return child_app if app_id == "child-a" else None

    monkeypatch.setattr(execution_service, "load_active_api_version", AsyncMock(return_value=root))
    monkeypatch.setattr(execution_service, "async_session", lambda: _Session())
    monkeypatch.setattr(execution_service, "load_published_visibility_version", AsyncMock(return_value=child_version))
    # The child draft is deliberately unsafe.  It must not be consulted when
    # published_json is absent.
    monkeypatch.setattr(execution_service, "load_published_definition", AsyncMock(return_value=None))
    owner_key = AsyncMock(return_value="publisher-key")
    monkeypatch.setattr(execution_service.key_service, "get_user_key", owner_key)

    with pytest.raises(execution_service.ApiExecutionError, match="api_runtime_capability_unsupported"):
        await execution_service.execute_published_api_workflow(
            PRINCIPAL,
            execution_service.ApiExecutionRequest("hello", [], "chat-1", "openai_api"),
        )

    owner_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_api_execution_rejects_blank_nested_plugin_reference_before_owner_key(monkeypatch):
    from app.services.agent_api import execution_service

    root = SimpleNamespace(**{
        **VERSION.__dict__,
        "definition_json": (
            '{"fastgpt":{"nodes":['
            '{"nodeId":"start","flowNodeType":"workflowStart"},'
            '{"nodeId":"child","flowNodeType":"pluginModule","pluginId":"  "}'
            ']}}'
        ),
    })
    monkeypatch.setattr(execution_service, "load_active_api_version", AsyncMock(return_value=root))
    owner_key = AsyncMock(return_value="publisher-key")
    monkeypatch.setattr(execution_service.key_service, "get_user_key", owner_key)

    with pytest.raises(execution_service.ApiExecutionError, match="api_runtime_capability_unsupported"):
        await execution_service.execute_published_api_workflow(
            PRINCIPAL,
            execution_service.ApiExecutionRequest("hello", [], "chat-1", "openai_api"),
        )

    owner_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_api_stream_reports_the_same_final_text_as_non_streaming(monkeypatch):
    from app.services.agent_api import execution_service

    monkeypatch.setattr(execution_service, "load_active_api_version", AsyncMock(return_value=VERSION))
    monkeypatch.setattr(execution_service.key_service, "get_user_key", AsyncMock(return_value="publisher-key"))
    monkeypatch.setattr(
        execution_service,
        "_resolve_owner_model_configuration",
        AsyncMock(return_value="publisher-default"),
    )

    async def _run(_version, ctx):
        if ctx.stream_output:
            await ctx.stream_output(1, "done")
        return {"status": "success", "output": "done", "runId": ctx.run_id}

    monkeypatch.setattr(execution_service, "_run_api_version", _run)
    request = execution_service.ApiExecutionRequest("hello", [], "chat-1", "openai_api")
    normal = await execution_service.execute_published_api_workflow(PRINCIPAL, request)
    events = [event async for event in execution_service.stream_published_api_workflow(PRINCIPAL, request)]

    assert "".join(event.delta for event in events if event.type == "delta") == normal["output"]
    assert events[-1].type == "completed"
    assert events[-1].result["output"] == normal["output"]


@pytest.mark.asyncio
async def test_workflow_model_audit_receives_external_attribution(monkeypatch):
    """Removing the RunContext -> logical audit bridge must fail this test."""
    calls = []

    class _Audit:
        async def begin_logical_call(self, **kwargs):
            calls.append(kwargs)
            return None

        async def begin_attempt(self, *_args, **_kwargs):
            return None

    from app.services.agent_harness import model_usage_audit

    monkeypatch.setattr(model_usage_audit, "begin_logical_call", _Audit().begin_logical_call)
    monkeypatch.setattr(model_usage_audit, "begin_attempt", _Audit().begin_attempt)
    attribution = ExternalAttribution("inv-a", "key-a", "app-a", "publisher-a")
    engine = WorkflowEngine(
        {"nodes": [], "edges": []},
        RunContext(input_text="hello", run_id="run-a", llm_api_key="publisher-key", external_attribution=attribution),
    )

    await engine._begin_workflow_llm_audit(
        model="model-a", node={"nodeId": "n-1", "flowNodeType": "chatNode"}, messages=[], stream=False,
    )

    assert calls[0]["external_attribution"] == attribution


@pytest.mark.asyncio
async def test_tool_invoked_subworkflow_keeps_external_attribution(monkeypatch):
    from app.services.gateway import tool_invoker
    from app.services.workflows import workflow_engine

    seen = {}

    class _Engine:
        def __init__(self, _graph, ctx):
            seen["ctx"] = ctx

        async def run(self):
            return None

    monkeypatch.setattr(tool_invoker, "load_tool_definition", AsyncMock(
        return_value='{"fastgpt":{"nodes":[{"nodeId":"start","flowNodeType":"workflowStart"}]}}'
    ))
    monkeypatch.setattr(workflow_engine, "WorkflowEngine", _Engine)
    attribution = ExternalAttribution("inv-a", "key-a", "app-a", "publisher-a")
    parent = SimpleNamespace(
        depth=0, app_id="app-a", token="", user_id="publisher-a", run_id="run-a", thread_id="api:key-a:chat-1",
        audit_run_id="run-a", audit_root_run_id="run-a", audit_parent_tool_call_id="", audit_parent_logical_call_id="",
        audit_execution_segment="run-a", audit_purpose="workflow_node", preview_only=False,
        llm_api_key="publisher-key", default_model="model-a", external_attribution=attribution,
    )

    await tool_invoker.run_sub_workflow("child-a", "child", "hello", parent_ctx=parent)

    assert seen["ctx"].external_attribution == attribution


@pytest.mark.asyncio
async def test_plugin_module_forces_published_child_for_api_context(monkeypatch):
    from app.services.gateway import tool_invoker

    calls = []

    async def _run_child(*_args, **kwargs):
        calls.append(kwargs["allow_draft"])
        return "child output"

    monkeypatch.setattr(tool_invoker, "run_sub_workflow", _run_child)
    node = {"nodeId": "plugin", "flowNodeType": "pluginModule", "pluginId": "child-a", "inputs": []}
    run = NodeRun("plugin", "pluginModule", "child")
    external = ExternalAttribution("inv-a", "key-a", "app-a", "publisher-a")
    api_engine = WorkflowEngine(
        {"nodes": [], "edges": []},
        RunContext(input_text="x", external_attribution=external, api_runtime=True),
    )
    local_engine = WorkflowEngine({"nodes": [], "edges": []}, RunContext(input_text="x"))

    await api_engine._run_plugin_module(node, run)
    await local_engine._run_plugin_module(node, run)

    assert calls == [False, True]


@pytest.mark.asyncio
async def test_api_context_without_attribution_still_forces_published_plugin_child(monkeypatch):
    from app.services.agent_api import execution_service
    from app.services.gateway import tool_invoker

    calls = []

    async def _run_child(*_args, **kwargs):
        calls.append(kwargs["allow_draft"])
        return "child output"

    monkeypatch.setattr(tool_invoker, "run_sub_workflow", _run_child)
    ctx = execution_service._context_for_api_execution(
        PRINCIPAL,
        execution_service.ApiExecutionRequest("hello", [], "chat-1", "openai_api"),
        "publisher-key",
        "model-a",
    )
    engine = WorkflowEngine({"nodes": [], "edges": []}, ctx)
    node = {"nodeId": "plugin", "flowNodeType": "pluginModule", "pluginId": "child-a", "inputs": []}

    await engine._run_plugin_module(node, NodeRun("plugin", "pluginModule", "child"))

    assert ctx.external_attribution is None
    assert ctx.api_runtime is True
    assert calls == [False]
