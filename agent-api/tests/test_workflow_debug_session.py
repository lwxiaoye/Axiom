import pytest

from app.services.workflows.debug_session_service import (
    DebugSessionForbiddenError,
    DebugSessionUnsupportedGraphError,
    WorkflowDebugSession,
    WorkflowDebugSessionStore,
)
from app.services.workflows.workflow_engine import RunContext


def _graph():
    return {
        "nodes": [
            {"nodeId": "start", "name": "开始", "flowNodeType": "workflowStart", "inputs": [], "outputs": []},
            {
                "nodeId": "text",
                "name": "拼接文本",
                "flowNodeType": "textEditor",
                "inputs": [{"key": "system_textareaInput", "value": "你好，{{name}}"}],
                "outputs": [{"key": "system_text", "required": True}],
            },
        ],
        "edges": [{"source": "start", "sourceHandle": "start-source-right", "target": "text"}],
        "chatConfig": {},
    }


@pytest.mark.asyncio
async def test_single_step_runs_one_node_and_keeps_context_between_requests():
    session = WorkflowDebugSession.create(
        _graph(),
        RunContext(input_text="测试", variables={"name": "初始值"}, app_id="app-1", run_id="run-1"),
        owner_user_id="user-1",
        app_id="app-1",
    )

    first = await session.step()
    assert first["status"] == "paused"
    assert [run["nodeId"] for run in first["nodeRuns"]] == ["start"]
    assert first["edges"][0]["status"] == "active"

    second = await session.step({"name": "小青"})
    assert second["status"] == "completed"
    assert [run["nodeId"] for run in second["nodeRuns"]] == ["start", "text"]
    assert second["outputs"]["text"]["system_text"] == "你好，小青"

    assert second["nextNodeId"] is None


def test_single_step_rejects_interactive_nodes_until_their_checkpoint_can_be_preserved():
    graph = _graph()
    graph["nodes"].append({"nodeId": "form", "flowNodeType": "formInput"})

    with pytest.raises(DebugSessionUnsupportedGraphError, match="formInput"):
        WorkflowDebugSession.create(
            graph,
            RunContext(input_text="测试", app_id="app-1"),
            owner_user_id="user-1",
            app_id="app-1",
        )


@pytest.mark.asyncio
async def test_debug_session_store_enforces_creator_ownership():
    store = WorkflowDebugSessionStore()
    session = WorkflowDebugSession.create(
        _graph(),
        RunContext(input_text="测试", app_id="app-1"),
        owner_user_id="user-1",
        app_id="app-1",
    )
    await store.create(session)

    with pytest.raises(DebugSessionForbiddenError):
        await store.get(session.session_id, "user-2")
