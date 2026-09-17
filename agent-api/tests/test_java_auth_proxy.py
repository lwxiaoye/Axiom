"""内部鉴权不应被桌面 HTTP 代理劫持。"""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.core import auth


def test_java_auth_bypasses_environment_proxy(monkeypatch):
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
        monkeypatch.setattr(auth.settings, "JAVA_INTERNAL_BASE", f"http://127.0.0.1:{server.server_port}")
        user = asyncio.run(auth._verify_token_with_java("test-token"))
        assert user.user_id == "test-user"
        assert received == [("/sys/user/getUserInfo", "test-token")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
