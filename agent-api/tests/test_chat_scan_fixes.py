"""主对话专项扫描修复的轻量回归用例。"""
import asyncio
import inspect
from unittest.mock import patch

import pytest
from fastapi import HTTPException


def test_current_user_reads_no_identity_headers():
    """身份只来自 token 回源 auth-api：current_user 不再声明任何身份头参数。

    历史上的「APISIX 网关签名身份」路径（X-User-Id/X-Username + X-Auth-Signature）
    随 Java 后端一起下线；这里钉住依赖签名，防止有人把未签名的身份头再加回来。
    """
    from app.core import auth

    params = set(inspect.signature(auth.current_user).parameters)
    assert params == {"x_access_token", "authorization"}
    assert not hasattr(auth, "_verify_gateway_signature")
    assert not hasattr(auth.settings, "GATEWAY_IDENTITY_SECRET")


def test_token_path_uses_auth_api_identity():
    """带 token 的请求以 auth-api 回源结果为准，并把 token 回填到上下文。"""
    from app.core import auth

    async def fake_verify(_token):
        return auth.UserContext(user_id="token-user", username="token-name")

    with (
        patch.object(auth.settings, "AUTH_TOKEN_CACHE_TTL_SECONDS", 0),
        patch.object(auth, "_token_cache", {}),
        patch.object(auth, "_verify_token_with_auth_api", fake_verify),
    ):
        user = asyncio.run(auth.current_user(x_access_token="real-token", authorization=None))

    assert user.user_id == "token-user"
    assert user.username == "token-name"
    assert user.access_token == "real-token"


def test_missing_token_is_rejected():
    """没有 token 就没有身份：无论请求带什么头都必须 401。"""
    from app.core import auth

    with pytest.raises(HTTPException) as error:
        asyncio.run(auth.current_user(x_access_token=None, authorization=None))
    assert error.value.status_code == 401
