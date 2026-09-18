import logging
import time
from typing import Optional

import httpx
from fastapi import Header, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)


def _normalize_ids(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(value, list):
        return [str(value)]
    result = []
    for item in value:
        if isinstance(item, dict):
            item = (
                item.get("id") or item.get("value") or item.get("roleId")
                or item.get("departId") or item.get("roleCode")
                or item.get("role_code") or item.get("code")
            )
        if item is not None and str(item).strip():
            result.append(str(item).strip())
    return result


async def _load_role_ids_from_database(user_id: str) -> list[str]:
    """auth-api 响应没有角色时，从同一业务库只读补齐；失败严格回退空角色。"""
    try:
        from sqlalchemy import text

        from app.core.database import async_session

        async with async_session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT r.id, r.role_code FROM sys_user_role ur "
                        "JOIN sys_role r ON r.id = ur.role_id WHERE ur.user_id = :user_id"
                    ),
                    {"user_id": user_id},
                )
            ).all()
    except Exception:  # noqa: BLE001
        logger.warning("数据库角色补齐失败 user_id=%s", user_id, exc_info=True)
        return []
    result: list[str] = []
    for role_id, role_code in rows:
        for value in (role_id, role_code):
            normalized = str(value or "").strip()
            if normalized and normalized not in result:
                result.append(normalized)
    return result


def _mask_token(token: str) -> str:
    if not token:
        return "<empty>"
    if len(token) <= 8:
        return f"{token[:2]}***"
    return f"{token[:6]}***{token[-4:]}"


class UserContext(BaseModel):
    user_id: str
    username: str
    real_name: str = ""
    tenant_id: str = "0"
    dept_ids: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    access_token: str = Field(default="", exclude=True)


_token_cache: dict[str, tuple[float, UserContext]] = {}


def _clean_access_token(x_access_token: Optional[str], authorization: Optional[str]) -> str:
    token = (x_access_token or authorization or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def _get_cached(token: str) -> Optional[UserContext]:
    if settings.AUTH_TOKEN_CACHE_TTL_SECONDS <= 0:
        return None
    cached = _token_cache.get(token)
    if not cached:
        return None
    expire_at, user = cached
    if expire_at < time.time():
        _token_cache.pop(token, None)
        return None
    return user


def _cache_verified(token: str, user: UserContext) -> None:
    ttl = settings.AUTH_TOKEN_CACHE_TTL_SECONDS
    if ttl > 0:
        now = time.time()
        for key, (expires, _) in list(_token_cache.items()):
            if expires <= now:
                _token_cache.pop(key, None)
        _token_cache[token] = (now + ttl, user)


def _resolve_tenant_id(info: dict, result: dict) -> str:
    """从**可信 auth-api 回源结果**读取租户（语义发现升级 §十一）。

    只信 getUserInfo 响应本体，不信任何请求头（如 X-Tenant-Id——没有可信来源，
    伪造即越租户）。取不到即 tenant 0（单租户环境语义）。
    """
    for source in (info or {}, result or {}):
        for key in ("tenantId", "tenant_id", "loginTenantId"):
            value = source.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    rel = str((info or {}).get("relTenantIds") or "").strip()
    if rel:
        first = rel.split(",")[0].strip()
        if first:
            return first
    return "0"


async def _verify_token_with_auth_api(token: str) -> UserContext:
    url = f"{settings.AUTH_API_BASE}/sys/user/getUserInfo"
    logger.info("回源 auth-api 校验 token: %s -> %s", _mask_token(token), url)
    try:
        # 内部身份校验直连配置的 auth-api 服务，避免继承桌面代理并转发登录凭据。
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            resp = await client.get(url, headers={"X-Access-Token": token})
            logger.info("auth-api getUserInfo 返回状态码: %s", resp.status_code)
            data = resp.json()
    except Exception:
        logger.warning("回源 auth-api 鉴权失败 (token=%s)", _mask_token(token), exc_info=True)
        raise HTTPException(503, "鉴权服务不可用")

    if not data.get("success"):
        logger.warning(
            "auth-api 返回鉴权失败 (token=%s): code=%s message=%s",
            _mask_token(token), data.get("code"), data.get("message"),
        )
        raise HTTPException(401, "令牌无效或已过期")

    result = data.get("result") or {}
    info = result.get("userInfo") or result
    user_id = str(info.get("id") or info.get("userId") or "").strip()
    username = str(info.get("username") or "").strip()
    if not user_id or not username:
        logger.warning(
            "auth-api 用户信息缺少 user_id/username (token=%s): result_keys=%s",
            _mask_token(token), list(result.keys()),
        )
        raise HTTPException(401, "令牌无效或已过期")

    roles = (
        result.get("roles") or result.get("roleList")
        or info.get("roleList") or info.get("roles") or []
    )
    role_ids = _normalize_ids(roles)
    if not role_ids:
        role_ids = await _load_role_ids_from_database(user_id)
    raw_depts = (
        info.get("departIds")
        or info.get("deptIds")
        or info.get("selecteddeparts")
        or result.get("departIds")
        or result.get("deptIds")
        or []
    )
    user = UserContext(
        user_id=user_id,
        username=username,
        real_name=str(info.get("realname") or ""),
        tenant_id=_resolve_tenant_id(info, result),
        dept_ids=_normalize_ids(raw_depts),
        role_ids=role_ids,
    )
    logger.info(
        "鉴权成功: user_id=%s username=%s role_ids=%s dept_ids=%s",
        user.user_id, user.username, user.role_ids, user.dept_ids,
    )
    return user


async def current_user(
    x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> UserContext:
    """请求主体只来自访问令牌回源 auth-api（带缓存）。

    不读取任何 X-User-Id / X-Username 之类的身份头：它们没有签名来源，
    伪造即越权。历史上的「APISIX 网关签名身份」路径随 Java 后端一起下线。
    """
    token = _clean_access_token(x_access_token, authorization)
    if not token:
        raise HTTPException(401, "缺少访问令牌")
    cached = _get_cached(token)
    if cached:
        cached.access_token = token
        return cached
    user = await _verify_token_with_auth_api(token)
    user.access_token = token
    _cache_verified(token, user)
    return user


async def user_from_token(token: str) -> Optional[UserContext]:
    """用访问令牌换用户上下文（带缓存）。供 FastAPI 依赖之外的场景使用，
    例如对话工具里要按当前用户过滤知识库权限。令牌无效时返回 None。"""
    cleaned = (token or "").strip()
    if not cleaned:
        return None
    cached = _get_cached(cleaned)
    if cached:
        return cached
    try:
        user = await _verify_token_with_auth_api(cleaned)
    except Exception:  # noqa: BLE001 - 含 HTTPException(401)
        return None
    _cache_verified(cleaned, user)
    return user


def is_admin(user: UserContext) -> bool:
    return user.username == "admin" or "admin" in user.role_ids


def _split_ids(value: str) -> set[str]:
    return {item.strip() for item in (value or "").split(",") if item.strip()}


def is_platform_admin(user: UserContext) -> bool:
    """跨用户管理权限（WS4）：内置 admin 或角色命中 AGENT_ADMIN_ROLE_IDS 白名单。"""
    if is_admin(user):
        return True
    return bool(_split_ids(settings.AGENT_ADMIN_ROLE_IDS) & set(user.role_ids))


def is_reviewer(user: UserContext) -> bool:
    """发布审核权限（WS2）：平台管理员天然可审，或角色命中 AGENT_REVIEWER_ROLE_IDS。"""
    if is_platform_admin(user):
        return True
    return bool(_split_ids(settings.AGENT_REVIEWER_ROLE_IDS) & set(user.role_ids))
