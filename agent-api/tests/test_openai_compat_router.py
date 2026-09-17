from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI


@pytest.fixture
def principal():
    from app.services.agent_api.access_service import AgentApiPrincipal

    return AgentApiPrincipal("key-a", "app-a", "publisher-a", "axa_example")


@pytest.fixture
def app(monkeypatch, principal):
    from app.routers import openai_compat
    from app.services.agent_api.external_session_service import ExternalSessionHandle

    monkeypatch.setattr(openai_compat, "authenticate_openai_bearer", AsyncMock(return_value=principal))
    monkeypatch.setattr(openai_compat, "get_or_create_external_session", AsyncMock(return_value=ExternalSessionHandle(
        id="exts_a", app_id="app-a", api_key_id="key-a", owner_user_id="publisher-a",
        session_id="chat_test", workspace_ref="external/exts_a",
    )))
    app = FastAPI()
    app.include_router(openai_compat.router)
    return app


@pytest.mark.asyncio
async def test_models_exposes_only_the_authenticated_agents_virtual_model(app):
    """A regression that listed provider models would expose owner configuration."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/openai/v1/models", headers={"Authorization": "Bearer axa_example"})

    assert response.status_code == 200
    assert response.json()["object"] == "list"
    assert response.json()["data"] == [{
        "id": "agent_app-a", "object": "model", "created": 0, "owned_by": "publisher-agent-api",
    }]


@pytest.mark.asyncio
async def test_chat_completion_maps_text_history_and_returns_virtual_model(app, monkeypatch):
    """A non-streaming caller must receive OpenAI's response envelope, never engine internals."""
    from app.routers import openai_compat

    execute = AsyncMock(return_value={"runId": "api_run_1", "status": "success", "output": "你好"})
    monkeypatch.setattr(openai_compat, "execute_published_api_workflow", execute)
    monkeypatch.setattr(openai_compat, "begin_openai_invocation", AsyncMock(return_value=None))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/openai/v1/chat/completions", headers={"Authorization": "Bearer axa_example"}, json={
            "model": "agent_app-a", "n": 1, "user": "request-42",
            "messages": [
                {"role": "assistant", "content": "上一轮"},
                {"role": "user", "content": "现在的问题"},
            ],
        })

    body = response.json()
    assert response.status_code == 200
    assert body["object"] == "chat.completion"
    assert body["model"] == "agent_app-a"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "你好"}
    execution = execute.await_args.args[1]
    assert execution.input_text == "现在的问题"
    assert execution.histories == [{"role": "assistant", "content": "上一轮"}]
    assert execution.source == "openai_api"


@pytest.mark.asyncio
async def test_unsupported_tools_returns_an_openai_parameter_error(app):
    """Silently accepting tool controls could give callers a false security expectation."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/openai/v1/chat/completions", headers={"Authorization": "Bearer axa_example"}, json={
            "model": "agent_app-a", "messages": [{"role": "user", "content": "x"}], "tools": [],
        })

    assert response.status_code == 400
    assert response.json()["error"] == {
        "message": "Parameter 'tools' is not supported by publisher agent API.",
        "type": "invalid_request_error", "param": "tools", "code": "unsupported_parameter",
    }


@pytest.mark.asyncio
async def test_final_message_must_be_a_text_user_message(app):
    """Treating an assistant turn as input would invert the caller's conversation semantics."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/openai/v1/chat/completions", headers={"Authorization": "Bearer axa_example"}, json={
            "model": "agent_app-a", "messages": [{"role": "assistant", "content": "x"}],
        })

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_messages"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "param"),
    [
        ({"role": "assistant", "content": "x", "tool_calls": []}, "messages[0].tool_calls"),
        ({"role": "user", "content": "x", "function_call": {"name": "unsafe"}}, "messages[0].function_call"),
    ],
)
async def test_message_tool_and_function_fields_are_explicitly_rejected(app, message, param):
    """Ignoring per-message controls could let an OpenAI client assume tools are available."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/openai/v1/chat/completions", headers={"Authorization": "Bearer axa_example"}, json={
            "model": "agent_app-a", "messages": [message],
        })

    assert response.status_code == 400
    assert response.json()["error"] == {
        "message": f"Parameter '{param}' is not supported by publisher agent API.",
        "type": "invalid_request_error", "param": param, "code": "unsupported_parameter",
    }
