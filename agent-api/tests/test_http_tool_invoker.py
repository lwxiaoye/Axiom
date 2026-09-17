import httpx
import pytest

from app.services.gateway import tool_invoker


class _FakeClient:
    captured = None

    def __init__(self, *args, **kwargs):
        self.options = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def request(self, method, url, **kwargs):
        type(self).captured = {"method": method, "url": url, **kwargs}
        return httpx.Response(201, headers={"Content-Type": "application/json"}, text='{"ok":true}')


@pytest.fixture(autouse=True)
def fake_http(monkeypatch):
    _FakeClient.captured = None
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr("app.services.gateway.mcp_client.assert_public_http_url", lambda _url: None)


def _config(tool):
    return {
        "baseUrl": "https://api.example.com",
        "headers": [{"key": "Authorization", "value": "Bearer {{token}}"}, {"key": "X-Locale", "value": "global"}],
        "toolList": [tool],
    }


@pytest.mark.asyncio
async def test_routes_values_to_path_query_headers_and_json_body():
    tool = {
        "name": "updateUser",
        "method": "PATCH",
        "path": "/users/{userId}",
        "pathParams": [{"key": "userId", "type": "string", "required": True}],
        "queryParams": [{"key": "notify", "type": "boolean", "required": False}],
        "headerParams": [{"key": "X-Locale", "type": "string", "required": False}],
        "bodyParams": [{"key": "name", "type": "string", "required": True}],
        "inputSchema": {"type": "object", "properties": {key: {"type": "string"} for key in ("userId", "notify", "X-Locale", "name", "token")}},
    }

    result = await tool_invoker.execute_http_toolset_request(
        _config(tool),
        "updateUser",
        {"userId": "a/b", "notify": True, "X-Locale": "zh-CN", "name": "Ada", "token": "secret"},
    )

    assert result.status_code == 201
    assert _FakeClient.captured["url"] == "https://api.example.com/users/a%2Fb"
    assert _FakeClient.captured["params"] == {"notify": True}
    assert _FakeClient.captured["json"] == {"name": "Ada"}
    assert _FakeClient.captured["headers"] == {"Authorization": "Bearer secret", "X-Locale": "zh-CN"}


@pytest.mark.asyncio
async def test_keeps_legacy_get_payload_as_query():
    tool = {"name": "listUsers", "method": "GET", "path": "/users"}
    await tool_invoker.execute_http_toolset_request(_config(tool), "listUsers", {"page": 2})
    assert _FakeClient.captured["params"] == {"page": 2}
    assert _FakeClient.captured["json"] is None


@pytest.mark.asyncio
async def test_builds_multipart_request_from_uploaded_file():
    tool = {
        "name": "upload",
        "method": "POST",
        "path": "/files",
        "bodyParams": [
            {"key": "caption", "type": "string", "required": False},
            {"key": "avatar", "type": "file", "required": True},
        ],
        "pathParams": [],
        "queryParams": [],
        "headerParams": [],
    }
    upload = tool_invoker.HttpUpload(filename="../avatar.png", content_type="image/png", content=b"png")

    await tool_invoker.execute_http_toolset_request(
        _config(tool), "upload", {"caption": "profile"}, uploaded_files={"avatar": upload}
    )

    assert _FakeClient.captured["data"] == {"caption": "profile"}
    assert _FakeClient.captured["files"]["avatar"] == ("avatar.png", b"png", "image/png")
    assert _FakeClient.captured["json"] is None


@pytest.mark.asyncio
async def test_rejects_local_file_paths():
    tool = {
        "name": "upload",
        "method": "POST",
        "path": "/files",
        "bodyParams": [{"key": "avatar", "type": "file", "required": True}],
        "pathParams": [],
        "queryParams": [],
        "headerParams": [],
    }
    with pytest.raises(tool_invoker.ToolInvokeError, match="文件引用"):
        await tool_invoker.execute_http_toolset_request(_config(tool), "upload", {"avatar": "C:/secret.txt"})
