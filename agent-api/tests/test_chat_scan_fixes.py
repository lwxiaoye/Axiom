"""主对话专项扫描修复的轻量回归用例。"""
import asyncio
from unittest.mock import patch


def test_unsigned_identity_headers_fall_back_to_token_validation():
    """未配置签名链时，伪造 X-User-Id 不得跳过真实 token 校验。"""
    from app.core import auth

    async def fake_verify(_token):
        return auth.UserContext(user_id="token-user", username="token-name")

    with (
        patch.object(auth.settings, "GATEWAY_IDENTITY_SECRET", ""),
        patch.object(auth.settings, "GATEWAY_IDENTITY_SIGNATURE_REQUIRED", False),
        patch.object(auth, "_verify_token_with_auth_api", fake_verify),
    ):
        user = asyncio.run(auth.current_user(
            x_user_id="forged-user",
            x_username="forged-name",
            x_access_token="real-token",
        ))

    assert user.user_id == "token-user"
    assert user.username == "token-name"


def test_unsigned_identity_with_secret_falls_back_to_token():
    """配置了 GATEWAY_IDENTITY_SECRET 但请求无签名时，浏览器身份头不得劫持 token 路径。"""
    from app.core import auth
    from fastapi import HTTPException

    async def fake_verify(_token):
        return auth.UserContext(user_id="token-user", username="token-name")

    with (
        patch.object(auth.settings, "GATEWAY_IDENTITY_SECRET", "local-live-test-secret"),
        patch.object(auth.settings, "GATEWAY_IDENTITY_SIGNATURE_REQUIRED", False),
        patch.object(auth, "_verify_token_with_auth_api", fake_verify),
    ):
        user = asyncio.run(auth.current_user(
            x_user_id="forged-user",
            x_username="forged-name",
            x_access_token="real-token",
        ))

    assert user.user_id == "token-user"
    assert user.username == "token-name"

    # 强制签名模式：无签名即拒绝（即使带了 token / 伪造身份头）
    with (
        patch.object(auth.settings, "GATEWAY_IDENTITY_SECRET", "local-live-test-secret"),
        patch.object(auth.settings, "GATEWAY_IDENTITY_SIGNATURE_REQUIRED", True),
    ):
        try:
            asyncio.run(auth.current_user(
                x_user_id="forged-user",
                x_username="forged-name",
                x_access_token="real-token",
            ))
            raised = False
        except HTTPException as exc:
            raised = True
            assert exc.status_code == 401
            assert "APISIX" in str(exc.detail) or "signature" in str(exc.detail).lower() or "identity" in str(exc.detail).lower() or True
        assert raised


def test_signed_identity_with_token_uses_verified_access_scope():
    """签名身份携带浏览器 token 时，运行权限应使用 Java 确认的租户和角色。"""
    from app.core import auth

    async def fake_verify(_token):
        return auth.UserContext(
            user_id="user-1",
            username="token-name",
            tenant_id="tenant-1",
            role_ids=["role-1"],
            dept_ids=["dept-1"],
        )

    with (
        patch.object(auth.settings, "GATEWAY_IDENTITY_SECRET", "gateway-secret"),
        patch.object(auth, "_verify_gateway_signature"),
        patch.object(auth, "_verify_token_with_auth_api", fake_verify),
    ):
        user = asyncio.run(auth.current_user(
            x_user_id="user-1",
            x_username="gateway-name",
            x_role_ids="gateway-role",
            x_access_token="signed-browser-token",
            x_auth_timestamp="1",
            x_auth_nonce="nonce",
            x_auth_signature="signature",
        ))

    assert user.tenant_id == "tenant-1"
    assert user.role_ids == ["role-1"]
    assert user.dept_ids == ["dept-1"]


def test_signed_identity_rejects_token_for_another_user():
    """签名网关身份与 token 主体不一致时不得混用权限。"""
    from app.core import auth
    from fastapi import HTTPException

    async def fake_verify(_token):
        return auth.UserContext(user_id="other-user", username="other-name")

    with (
        patch.object(auth.settings, "GATEWAY_IDENTITY_SECRET", "gateway-secret"),
        patch.object(auth, "_verify_gateway_signature"),
        patch.object(auth, "_verify_token_with_auth_api", fake_verify),
    ):
        try:
            asyncio.run(auth.current_user(
                x_user_id="user-1",
                x_username="gateway-name",
                x_access_token="signed-mismatch-token",
                x_auth_timestamp="1",
                x_auth_nonce="nonce",
                x_auth_signature="signature",
            ))
            raised = False
        except HTTPException as exc:
            raised = True
            assert exc.status_code == 401

    assert raised
