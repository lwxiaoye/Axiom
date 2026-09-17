from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI


@pytest.fixture
def app(monkeypatch):
    from app.routers import openai_compat
    from app.services.agent_api.access_service import AgentApiPrincipal
    from app.services.agent_api.external_session_service import ExternalSessionHandle

    principal = AgentApiPrincipal("key-a", "app-a", "publisher-a", "qza_example")
    monkeypatch.setattr(openai_compat, "authenticate_openai_bearer", AsyncMock(return_value=principal))
    monkeypatch.setattr(openai_compat, "get_or_create_external_session", AsyncMock(return_value=ExternalSessionHandle(
        id="exts_a", app_id="app-a", api_key_id="key-a", owner_user_id="publisher-a",
        session_id="chat_test", workspace_ref="external/exts_a",
    )))
    app = FastAPI()
    app.include_router(openai_compat.router)
    return app


@pytest.mark.asyncio
async def test_stream_emits_openai_chunks_trusted_usage_and_done(app, monkeypatch):
    """Removing the terminal marker or sending usage without trust breaks standard SSE clients."""
    from app.routers import openai_compat
    from app.services.agent_api.execution_service import ApiExecutionStreamEvent

    async def _stream(*_args, **_kwargs):
        yield ApiExecutionStreamEvent(type="delta", delta="你")
        yield ApiExecutionStreamEvent(type="delta", delta="好")
        yield ApiExecutionStreamEvent(type="completed", result={
            "runId": "api_run_2", "status": "success", "output": "你好",
            "usage": {"input_tokens": 3, "output_tokens": 2, "trusted": True},
        })

    monkeypatch.setattr(openai_compat, "stream_published_api_workflow", _stream)
    monkeypatch.setattr(openai_compat, "begin_openai_invocation", AsyncMock(return_value=None))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/openai/v1/chat/completions", headers={"Authorization": "Bearer qza_example"}, json={
            "model": "agent_app-a", "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": "你好"}],
        })

    assert response.status_code == 200
    frames = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    assert frames[-1] == "[DONE]"
    chunks = [json.loads(frame) for frame in frames[:-1]]
    assert all(chunk["object"] == "chat.completion.chunk" for chunk in chunks)
    assert "".join(
        chunk["choices"][0]["delta"].get("content", "")
        for chunk in chunks if chunk["choices"]
    ) == "你好"
    assert chunks[-1]["choices"] == []
    assert chunks[-1]["usage"] == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}


@pytest.mark.asyncio
async def test_stream_omits_usage_chunk_when_provider_usage_is_not_trusted(app, monkeypatch):
    """Unknown provider usage must stay unknown rather than being presented as zero."""
    from app.routers import openai_compat
    from app.services.agent_api.execution_service import ApiExecutionStreamEvent

    async def _stream(*_args, **_kwargs):
        yield ApiExecutionStreamEvent(type="completed", result={
            "runId": "api_run_3", "status": "success", "output": "done",
            "usage": {"input_tokens": 3, "output_tokens": 2, "trusted": False},
        })

    monkeypatch.setattr(openai_compat, "stream_published_api_workflow", _stream)
    monkeypatch.setattr(openai_compat, "begin_openai_invocation", AsyncMock(return_value=None))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/openai/v1/chat/completions", headers={"Authorization": "Bearer qza_example"}, json={
            "model": "agent_app-a", "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": "你好"}],
        })

    chunks = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    assert all(chunk["choices"] for chunk in chunks)
