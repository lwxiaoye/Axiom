from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.services.agent_api.access_service import AgentApiPrincipal
from app.services.agent_api.external_session_service import ExternalFileHandle, ExternalSessionHandle


PRINCIPAL = AgentApiPrincipal("key-a", "app-a", "publisher-a", "qza_test")
SESSION = ExternalSessionHandle(
    id="exts_a", app_id="app-a", api_key_id="key-a", owner_user_id="publisher-a",
    session_id="s1", workspace_ref="external/exts_a",
)


@pytest.mark.asyncio
async def test_openai_file_is_bound_to_key_and_can_be_referenced_in_chat(monkeypatch):
    from app.routers import openai_compat

    monkeypatch.setattr(openai_compat, "authenticate_openai_bearer", AsyncMock(return_value=PRINCIPAL))
    monkeypatch.setattr(openai_compat, "get_or_create_external_session", AsyncMock(return_value=SESSION))
    saved = ExternalFileHandle(
        id="extfile_a", external_session_id="exts_a", storage_ref="external/exts_a/extfile_a.txt",
        original_name="a.txt", content_type="text/plain", size_bytes=5,
    )
    monkeypatch.setattr(openai_compat, "save_external_file", AsyncMock(return_value=saved))
    monkeypatch.setattr(openai_compat, "resolve_external_file", AsyncMock(return_value=saved))
    monkeypatch.setattr(openai_compat, "begin_openai_invocation", AsyncMock(return_value=None))
    execute = AsyncMock(return_value={"runId": "api_run_1", "status": "success", "output": "done"})
    monkeypatch.setattr(openai_compat, "execute_published_api_workflow", execute)

    app = FastAPI()
    app.include_router(openai_compat.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        uploaded = await client.post(
            "/openai/v1/files?session_id=s1",
            headers={"Authorization": "Bearer qza_test"},
            files={"file": ("a.txt", b"hello", "text/plain")},
        )
        completion = await client.post("/openai/v1/chat/completions?session_id=s1", headers={"Authorization": "Bearer qza_test"}, json={
            "model": "agent_app-a",
            "messages": [{"role": "user", "content": [
                {"type": "input_text", "text": "summarize"},
                {"type": "input_file", "file_id": uploaded.json()["id"]},
            ]}],
        })

    assert uploaded.status_code == 200
    assert uploaded.json()["id"] == "extfile_a"
    assert completion.status_code == 200
    execution = execute.await_args.args[1]
    assert execution.external_session == SESSION
    assert execution.file_ids == ["extfile_a"]
    assert execution.input_text == "summarize"
