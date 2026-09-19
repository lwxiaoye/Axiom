"""内部鉴权不应被桌面 HTTP 代理劫持。"""
import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.core import auth
from fastapi import HTTPException
import pytest


def test_auth_api_bypasses_environment_proxy(monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.path, self.headers.get("X-Access-Token")))
            body = json.dumps({"success": True, "result": {
                "userInfo": {"id": "test-user", "username": "test-name"},
                "roles": ["test-role"],
            }}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.setenv(key, "http://127.0.0.1:1")
        for key in ("NO_PROXY", "no_proxy"):
            monkeypatch.setenv(key, "")
        monkeypatch.setattr(auth.settings, "AUTH_API_BASE", f"http://127.0.0.1:{server.server_port}")
        # 鉴权成功后会把 userInfo upsert 进 sys_user；本测试只关心代理，且不该往任何真实库写测试行
        from app.services import sys_user_sync

        async def _no_sync(_info):
            return False

        monkeypatch.setattr(sys_user_sync, "sync_sys_user_profile", _no_sync)
        user = asyncio.run(auth._verify_token_with_auth_api("test-token"))
        assert user.user_id == "test-user"
        assert received == [("/sys/user/getUserInfo", "test-token")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_revoked_token_is_revalidated_instead_of_using_stale_cache(monkeypatch):
    user = auth.UserContext(user_id="test-user", username="test-name")
    monkeypatch.setattr(auth.settings, "AUTH_TOKEN_CACHE_TTL_SECONDS", 0)
    monkeypatch.setattr(auth, "_token_cache", {"revoked-token": (time.time() + 300, user)})

    async def rejected(_token):
        raise HTTPException(401, "revoked")

    monkeypatch.setattr(auth, "_verify_token_with_auth_api", rejected)
    with pytest.raises(HTTPException) as error:
        asyncio.run(auth.current_user(x_access_token="revoked-token", authorization=None))
    assert error.value.status_code == 401


def test_explicit_cache_ttl_and_expiration(monkeypatch):
    user = auth.UserContext(user_id="test-user", username="test-name")
    monkeypatch.setattr(auth.settings, "AUTH_TOKEN_CACHE_TTL_SECONDS", 5)
    monkeypatch.setattr(auth, "_token_cache", {})
    auth._cache_verified("test-token", user)
    assert auth._get_cached("test-token") == user
    expires, _ = auth._token_cache["test-token"]
    monkeypatch.setattr(auth.time, "time", lambda: expires + 1)
    assert auth._get_cached("test-token") is None
