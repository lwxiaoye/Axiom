import io
import json

import pytest
from fastapi import HTTPException, UploadFile

from app.routers import workflow


@pytest.mark.asyncio
async def test_http_tool_test_executes_unsaved_config_with_upload(monkeypatch):
    captured = {}

    async def fake_execute(config, tool_name, values, uploaded_files=None):
        captured.update(config=config, tool_name=tool_name, values=values, files=uploaded_files)
        return workflow.tool_invoker.HttpToolResponse(
            status_code=202,
            headers={"content-type": "application/json", "set-cookie": "secret"},
            body='{"accepted":true}',
            duration_ms=12,
            truncated=False,
        )

    monkeypatch.setattr(workflow.tool_invoker, "execute_http_toolset_request", fake_execute)
    upload = UploadFile(file=io.BytesIO(b"png"), filename="avatar.png", headers={"content-type": "image/png"})

    result = await workflow.http_tool_test(
        config=json.dumps({"baseUrl": "https://api.example.com", "toolList": []}),
        toolName="upload",
        values=json.dumps({"caption": "profile"}),
        fileParams=json.dumps(["avatar"]),
        files=[upload],
        user=object(),
    )

    assert result["statusCode"] == 202
    assert result["ok"] is True
    assert result["headers"] == {"content-type": "application/json"}
    assert captured["files"]["avatar"].content == b"png"


@pytest.mark.asyncio
async def test_http_tool_test_rejects_oversized_upload(monkeypatch):
    monkeypatch.setattr(workflow, "HTTP_TOOL_TEST_FILE_LIMIT", 2)
    upload = UploadFile(file=io.BytesIO(b"big"), filename="large.bin")

    with pytest.raises(HTTPException, match="20MB"):
        await workflow.http_tool_test(
            config='{"baseUrl":"https://api.example.com"}',
            toolName="upload",
            values="{}",
            fileParams='["file"]',
            files=[upload],
            user=object(),
        )


@pytest.mark.asyncio
async def test_http_tool_test_rejects_malformed_json():
    with pytest.raises(HTTPException) as exc:
        await workflow.http_tool_test(
            config="not-json",
            toolName="upload",
            values="{}",
            fileParams="[]",
            files=[],
            user=object(),
        )
    assert exc.value.status_code == 400
