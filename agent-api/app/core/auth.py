import base64
import hashlib
import hmac
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
    """Java 响应没有角色时，从同一业务库只读补齐；失败严格回退空角色。"""
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


def _verify_gateway_signature(
    user_id: str,
    username: str,
    role_ids: str,
    dept_ids: str,
    timestamp: str,
    nonce: str,
    signature: str,
) -> None:
    if not settings.GATEWAY_IDENTITY_SECRET:
        if settings.GATEWAY_IDENTITY_SIGNATURE_REQUIRED:
            raise HTTPException(503, "Gateway identity secret is not configured")
        return
    if not timestamp or not nonce or not signature:
        raise HTTPException(401, "Missing trusted gateway identity signature")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise HTTPException(401, "Invalid gateway identity timestamp") from exc
    if abs(time.time() - ts) > settings.GATEWAY_IDENTITY_MAX_AGE_SECONDS:
        raise HTTPException(401, "Gateway identity has expired")
    canonical = "\n".join([user_id, username, role_ids, dept_ids, timestamp, nonce])
    digest = hmac.new(
        settings.GATEWAY_IDENTITY_SECRET.encode(),
        canonical.encode(),
        hashlib.sha256,
    ).digest()
    expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "Invalid gateway identity signature")


def _resolve_tenant_id(info: dict, result: dict) -> str:
    """从**可信 Java 回源结果**读取租户（语义发现升级 §十一）。

    只信 getUserInfo 响应本体，不信任何未签名请求头（如 X-Tenant-Id——它不在
    网关 HMAC canonical 里，伪造即越租户）。取不到即 tenant 0（单租户环境语义）。
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


async def _verify_token_with_java(token: str) -> UserContext:
    url = f"{settings.AUTH_API_BASE}/sys/user/getUserInfo"
    logger.info("回源 Java 校验 token: %s -> %s", _mask_token(token), url)
    try:
        # 内部身份校验直连配置的 Java 服务，避免继承桌面代理并转发登录凭据。
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            resp = await client.get(url, headers={"X-Access-Token": token})
            logger.info("Java getUserInfo 返回状态码: %s", resp.status_code)
            data = resp.json()
    except Exception:
        logger.warning("回源 Java 鉴权失败 (token=%s)", _mask_token(token), exc_info=True)
        raise HTTPException(503, "鉴权服务不可用")

    if not data.get("success"):
        logger.warning(
            "Java 返回鉴权失败 (token=%s): code=%s message=%s",
            _mask_token(token), data.get("code"), data.get("message"),
        )
        raise HTTPException(401, "令牌无效或已过期")

    result = data.get("result") or {}
    info = result.get("userInfo") or result
    user_id = str(info.get("id") or info.get("userId") or "").strip()
    username = str(info.get("username") or "").strip()
    if not user_id or not username:
        logger.warning(
            "Java 用户信息缺少 user_id/username (token=%s): result_keys=%s",
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
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_username: Optional[str] = Header(None, alias="X-Username"),
    x_real_name: Optional[str] = Header("", alias="X-Real-Name"),
    x_role_ids: Optional[str] = Header("", alias="X-Role-Ids"),
    x_dept_ids: Optional[str] = Header("", alias="X-Dept-Ids"),
    x_auth_timestamp: Optional[str] = Header("", alias="X-Auth-Timestamp"),
    x_auth_nonce: Optional[str] = Header("", alias="X-Auth-Nonce"),
    x_auth_signature: Optional[str] = Header("", alias="X-Auth-Signature"),
    x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> UserContext:
    token = _clean_access_token(x_access_token, authorization)
    def _header_text(value) -> str:
        # FastAPI Header defaults are FieldInfo objects when this dependency is called directly
        # in tests or internal code.  Only actual header strings are trusted as request data.
        return value.strip() if isinstance(value, str) else ""

    user_id = _header_text(x_user_id)
    username = _header_text(x_username)
    timestamp = _header_text(x_auth_timestamp)
    nonce = _header_text(x_auth_nonce)
    signature = _header_text(x_auth_signature)
    has_signed_identity = bool(
        user_id and username and settings.GATEWAY_IDENTITY_SECRET
        and timestamp and nonce and signature
    )
    if has_signed_identity:
        roles = _header_text(x_role_ids)
        depts = _header_text(x_dept_ids)
        _verify_gateway_signature(
            user_id, username, roles, depts,
            timestamp, nonce, signature,
        )
        if token:
            # 网关签名保障了代理传入的主体，但签名协议没有 tenant 字段。浏览器请求
            # 同时携带 token 时，以 Java 回源的已验证租户、角色和部门为准，并校验
            # 两个可信身份来源的 user_id 一致，避免原本固定 tenant=0 误拒绝应用权限。
            verified = _get_cached(token) or await _verify_token_with_java(token)
            if verified.user_id != user_id:
                raise HTTPException(401, "Gateway identity does not match access token")
            verified.access_token = token
            _cache_verified(token, verified)
            return verified

        # 网关身份路径的租户保持 "0"：tenant 不在 HMAC canonical（user_id/username/
        # roles/depts/timestamp/nonce）里，未签名的租户头不可信（§十一.2/3）。要传租户
        # 必须先把它纳入网关签名协议并同步 Java 侧；协议未同步前明确保持单租户限制。
        return UserContext(
            user_id=user_id,
            username=username,
            real_name=_header_text(x_real_name),
            tenant_id="0",
            role_ids=_normalize_ids(roles),
            dept_ids=_normalize_ids(depts),
            access_token=token,
        )

    if settings.GATEWAY_IDENTITY_SIGNATURE_REQUIRED:
        raise HTTPException(401, "Missing APISIX authenticated identity")

    if not token:
        raise HTTPException(401, "缺少访问令牌")
    cached = _get_cached(token)
    if cached:
        cached.access_token = token
        return cached
    user = await _verify_token_with_java(token)
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
        user = await _verify_token_with_java(cleaned)
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
