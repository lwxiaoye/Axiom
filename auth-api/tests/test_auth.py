"""Run: agent-api/.venv/Scripts/python.exe -m pytest auth-api/tests -q

All HTTP requests use an in-process transport and all state lives in a temporary DB.
"""
import base64
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

AUTH_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AUTH_ROOT))
spec = importlib.util.spec_from_file_location("axiom_local_auth", AUTH_ROOT / "app.py")
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)

PASSWORD = "local-test-password-2026"


def encrypt(password):
    padder = padding.PKCS7(128).padder()
    padded = padder.update(password.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(auth.AES_KEY), modes.CBC(auth.AES_IV)).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "auth.sqlite3")
        self.env = patch.dict(os.environ, {"AXIOM_ADMIN_PASSWORD": PASSWORD, "AXIOM_AUTH_DB": self.db_path})
        self.env.start()
        self.life = auth.app.router.lifespan_context(auth.app)
        await self.life.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=auth.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.life.__aexit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    async def login(self, password=PASSWORD, encrypted=True, key="test-key"):
        auth.app.state.store.save_captcha(key, "ABCD")
        return await self.client.post("/sys/login", json={
            "username": auth.DEFAULT_USERNAME, "password": encrypt(password) if encrypted else password,
            "captcha": "abcd", "checkKey": key,
        })

    async def headers(self):
        response = await self.login()
        self.assertEqual(response.status_code, 200, response.text)
        return {"X-Access-Token": response.json()["result"]["token"]}

    async def test_permission_menu_parents_use_layout_component(self):
        headers = await self.headers()
        result = await self.client.get("/sys/permission/getUserPermissionByToken", headers=headers)
        self.assertEqual(result.status_code, 200)
        for menu in result.json()["result"]["menu"]:
            self.assertEqual(menu["component"], "LAYOUT")

    async def test_login_identity_permissions_and_logout(self):
        headers = await self.headers()
        result = await self.client.get("/sys/user/getUserInfo", headers=headers)
        self.assertEqual(result.json()["result"]["userInfo"]["id"], "1")
        permissions = await self.client.get("/sys/permission/getPermCode", headers=headers)
        self.assertIn("campus:admin", permissions.json()["result"])
        self.assertEqual((await self.client.post("/sys/logout", headers=headers)).status_code, 200)
        self.assertEqual((await self.client.get("/sys/user/getUserInfo", headers=headers)).status_code, 401)

    async def test_plaintext_password_is_rejected(self):
        result = await self.login(encrypted=False)
        self.assertEqual(result.status_code, 400)
        self.assertFalse(result.json()["success"])

    async def test_wrong_encrypted_password_is_rejected(self):
        self.assertEqual((await self.login(password="wrong-password")).status_code, 401)

    async def test_captcha_can_only_be_used_once(self):
        await self.login()
        result = await self.client.post("/sys/login", json={
            "username": auth.DEFAULT_USERNAME, "password": encrypt(PASSWORD),
            "captcha": "abcd", "checkKey": "test-key",
        })
        self.assertFalse(result.json()["success"])
        self.assertEqual(result.json()["code"], 412)

    async def test_wrong_captcha_consumes_challenge(self):
        auth.app.state.store.save_captcha("one-shot", "ABCD")
        self.assertFalse(auth.app.state.store.consume_captcha("one-shot", "XXXX"))
        self.assertFalse(auth.app.state.store.consume_captcha("one-shot", "ABCD"))

    async def test_expired_captcha_is_rejected(self):
        auth.app.state.store.save_captcha("expired", "ABCD")
        with auth.app.state.store.connect() as db:
            db.execute("UPDATE captchas SET expires=0")
        self.assertFalse(auth.app.state.store.consume_captcha("expired", "ABCD"))

    async def test_missing_auth_cannot_access_knowledge_or_write(self):
        for method, path in [("GET", "/ai/knowledge/acl/list"), ("GET", "/ai/knowledge/base/list"),
                             ("GET", "/app/appInfo/my/all/list"), ("GET", "/ai/skill/list"),
                             ("POST", "/sys/user/add"), ("GET", "/sys/dict/getDictItems/test")]:
            result = await self.client.request(method, path)
            self.assertEqual(result.status_code, 401)
            self.assertFalse(result.json()["success"])

    async def test_unimplemented_mutations_never_report_success(self):
        headers = await self.headers()
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            response = await self.client.request(method, "/sys/user/add", headers=headers)
            self.assertEqual(response.status_code, 404)
            self.assertFalse(response.json()["success"])

    async def test_legacy_knowledge_paths_are_unimplemented_not_empty_success(self):
        """知识库已由 agent-api 自持（/agent-api/knowledge/*），前端不再请求这些 Java 路径；
        专门的 503 桩已删，落到通用 404 fallback，但绝不能变成空的成功响应。"""
        headers = await self.headers()
        for method, path in [("GET", "/ai/knowledge/base/queryById"),
                             ("GET", "/ai/knowledge/base/list"),
                             ("GET", "/ai/knowledge/document/list"),
                             ("GET", "/ai/knowledge/acl/list"),
                             ("POST", "/ai/knowledge/retrieval/test"),
                             ("POST", "/ai/knowledge/retrieval/internal")]:
            response = await self.client.request(method, path, headers=headers)
            self.assertEqual(response.status_code, 404, path)
            self.assertFalse(response.json()["success"])

    async def test_local_catalog_lists_are_empty_not_404(self):
        headers = await self.headers()
        for path in ("/app/appInfo/my/all/list",):
            response = await self.client.get(path, headers=headers)
            self.assertEqual(response.status_code, 200, path)
            self.assertTrue(response.json()["success"])
            self.assertEqual(response.json()["result"], [])

    async def test_skill_catalog_stub_is_404_not_empty_success(self):
        """Skill 目录已迁至 agent-api：这里不能再回 `[]` 假装有数据源（会让目录消费者误判为空目录）。"""
        headers = await self.headers()
        response = await self.client.get("/ai/skill/list", headers=headers)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])
        self.assertIn("agent-api", response.json()["message"])

    async def test_persistent_sessions_and_password_rotation(self):
        headers = await self.headers()
        await self.life.__aexit__(None, None, None)
        self.life = auth.app.router.lifespan_context(auth.app)
        await self.life.__aenter__()
        self.assertEqual((await self.client.get("/sys/user/getUserInfo", headers=headers)).status_code, 200)
        # The stored password and token are both non-plaintext.
        data = Path(self.db_path).read_bytes()
        self.assertNotIn(PASSWORD.encode(), data)
        self.assertNotIn(headers["X-Access-Token"].encode(), data)
        auth.app.state.store = auth.AuthStore(self.db_path, auth.DEFAULT_USERNAME, "rotated-test-password")
        self.assertEqual((await self.client.get("/sys/user/getUserInfo", headers=headers)).status_code, 401)

    async def test_expired_session_is_rejected(self):
        headers = await self.headers()
        with auth.app.state.store.connect() as db:
            db.execute("UPDATE sessions SET expires=0")
        self.assertEqual((await self.client.get("/sys/user/getUserInfo", headers=headers)).status_code, 401)

    async def test_login_rate_limit_ignores_forged_forwarded_ip(self):
        for i in range(11):
            response = await self.client.post("/sys/login", json={}, headers={"X-Forwarded-For": f"test-{i}"})
        self.assertEqual(response.status_code, 429)

    async def test_captcha_rate_limit(self):
        for i in range(31):
            response = await self.client.get(f"/sys/randomImage/{i}")
        self.assertEqual(response.status_code, 429)

    async def test_cors_denies_unknown_origin(self):
        response = await self.client.options("/sys/login", headers={
            "Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access-control-allow-origin", response.headers)

    async def test_cors_allows_local_frontend(self):
        response = await self.client.options("/sys/login", headers={
            "Origin": "http://127.0.0.1:3200", "Access-Control-Request-Method": "POST"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://127.0.0.1:3200")

    async def test_unknown_user_has_no_admin_role(self):
        result = await self.client.get("/sys/user/queryUserRole?userid=other", headers=await self.headers())
        self.assertEqual(result.status_code, 404)

    async def test_startup_rejects_missing_or_placeholder_password(self):
        for password in ("", "admin123", "CHANGE_ME_123456"):
            with patch.dict(os.environ, {"AXIOM_ADMIN_PASSWORD": password}):
                with self.assertRaises(RuntimeError):
                    async with auth.app.router.lifespan_context(auth.app):
                        pass

    async def test_local_demo_password_login_requires_explicit_opt_in(self):
        with patch.dict(os.environ, {"AXIOM_LOCAL_DEMO_LOGIN": "true", "AXIOM_ADMIN_PASSWORD": "admin123"}):
            async with auth.app.router.lifespan_context(auth.app):
                response = await self.login(password="admin123")
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["success"])
                self.assertEqual((await self.login(password="wrong-password")).status_code, 401)

    async def test_startup_rejects_invalid_aes(self):
        with patch.object(auth, "AES_KEY", b"invalid-key"):
            with self.assertRaises(RuntimeError):
                async with auth.app.router.lifespan_context(auth.app):
                        pass
