"""WS2 发布审核鉴权 + 能力注册事件验签 安全回归（2026-07-24 安全修复）。

覆盖四条高危 / 一条中危：
- /review/approve、/review/reject、/review/page 必须审核员——非审核员一律 403
  （原缺 _require_reviewer：自审自过发布 / 越权驳回他人 / 全平台待审队列泄露）。
- /registry/events 验签在 secret 未配置（空或占位 change-me）时**失败关闭**（拒绝而非放行）——
  原先放行使该无用户鉴权接口在默认部署下可被任意写入污染能力注册表。

审核守卫在任何 DB 访问之前触发，故 approve/reject/page 用例无需数据库。
"""
import asyncio
import base64
import hashlib
import hmac
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException

from app.core.auth import UserContext
from app.routers import workflow as wf
from app.routers.workflow import ReviewActionRequest
from app.services.agents import app_capability_registry as reg


def _run(coro):
    return asyncio.run(coro)


# 普通登录用户：非内置 admin、无任何角色 —— 不满足 is_reviewer / is_platform_admin
NON_REVIEWER = UserContext(user_id="u-attacker", username="lisi", role_ids=[], dept_ids=[])


class ReviewEndpointAuthzTest(unittest.TestCase):
    def test_review_page_permission_grants_reviewer_gate(self):
        user = UserContext(user_id="u-reviewer", username="wangwu", access_token="token-review")

        with mock.patch.object(wf, "_java_user_has_menu_path", new=mock.AsyncMock(return_value=True)):
            _run(wf._require_review_permission(user))

    def test_review_page_permission_gate_rejects_user_without_menu(self):
        user = UserContext(user_id="u-reviewer", username="wangwu", access_token="token-review")

        with mock.patch.object(wf, "_java_user_has_menu_path", new=mock.AsyncMock(return_value=False)):
            with self.assertRaises(HTTPException) as ctx:
                _run(wf._require_review_permission(user))

        self.assertEqual(ctx.exception.status_code, 403)

    def test_role_reviewer_does_not_call_java_permission_service(self):
        user = UserContext(user_id="u-reviewer", username="reviewer", role_ids=["reviewer-role"])

        java_check = mock.AsyncMock(return_value=False)
        with mock.patch.object(wf.settings, "AGENT_REVIEWER_ROLE_IDS", "reviewer-role"):
            with mock.patch.object(wf, "_java_user_has_menu_path", new=java_check):
                _run(wf._require_review_permission(user))

        java_check.assert_not_awaited()

    def test_approve_rejects_non_reviewer(self):
        with self.assertRaises(HTTPException) as ctx:
            _run(wf.review_approve(ReviewActionRequest(versionId="v1"), NON_REVIEWER))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_reject_rejects_non_reviewer(self):
        # 守卫先于「必须填写意见」校验，故即便带 comment 也应 403
        with self.assertRaises(HTTPException) as ctx:
            _run(wf.review_reject(ReviewActionRequest(versionId="v1", comment="x"), NON_REVIEWER))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_review_page_rejects_non_reviewer(self):
        with self.assertRaises(HTTPException) as ctx:
            _run(wf.review_page(user=NON_REVIEWER))
        self.assertEqual(ctx.exception.status_code, 403)


class LegacyPublishEndpointTest(unittest.TestCase):
    def test_legacy_app_publish_endpoint_is_retired(self):
        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        owner = UserContext(user_id="u-owner", username="owner")
        permission = mock.AsyncMock(return_value=(SimpleNamespace(id="app-1"), "OWNER"))
        with mock.patch.object(wf, "async_session", return_value=FakeSession()):
            with mock.patch.object(wf, "_require_permission", new=permission):
                with self.assertRaises(HTTPException) as ctx:
                    _run(wf.publish_app("app-1", owner))

        self.assertEqual(ctx.exception.status_code, 410)
        permission.assert_awaited_once()


class RegistryEventSignatureFailClosedTest(unittest.TestCase):
    """verify_event_signature：未配 secret 拒绝；配置后正确签名放行、错误签名拒绝。"""

    def _patch_secret(self, secret: str):
        stub = SimpleNamespace(
            INTERNAL_SYNC_SECRET=secret,
            GATEWAY_IDENTITY_MAX_AGE_SECONDS=300,
        )
        return mock.patch.object(reg, "settings", stub)

    def test_empty_secret_rejects(self):
        with self._patch_secret(""):
            with self.assertRaises(ValueError):
                reg.verify_event_signature("upsert:0:cap", "0", "sig")

    def test_placeholder_secret_rejects(self):
        with self._patch_secret("change-me"):
            with self.assertRaises(ValueError):
                reg.verify_event_signature("upsert:0:cap", str(int(time.time())), "sig")

    def test_valid_signature_passes(self):
        secret = "s3cret-xyz"
        canonical = "upsert:0:cap"
        ts = str(int(time.time()))
        digest = hmac.new(secret.encode(), f"{canonical}\n{ts}".encode(), hashlib.sha256).digest()
        sig = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        with self._patch_secret(secret):
            reg.verify_event_signature(canonical, ts, sig)  # 不抛即通过

    def test_bad_signature_rejects(self):
        with self._patch_secret("s3cret-xyz"):
            with self.assertRaises(ValueError):
                reg.verify_event_signature("upsert:0:cap", str(int(time.time())), "wrong-sig")


if __name__ == "__main__":
    unittest.main()
