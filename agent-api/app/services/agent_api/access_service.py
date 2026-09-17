"""Publisher-owned Agent API key creation and bearer authentication."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hmac
import os
import secrets
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update

from app.core.config import settings
from app.core.database import async_session
from app.models import AgentApiAccessKey, WorkflowApp
from app.services.agent_api.external_session_service import (
    revoke_external_sessions,
    revoke_external_sessions_for_key,
)
from app.services.agent_api.publish_policy import normalize_embed_origins
from app.services.connectors.crypto import ConnectorCryptoError, decrypt_secret, encrypt_secret


FIXED_WINDOW_SECONDS = 60
FIXED_WINDOW_REQUESTS = 60
MAX_CONCURRENT_REQUESTS_PER_KEY = 4


class AgentApiAccessError(Exception):
    code = "agent_api_access_error"


class ApiAuthError(AgentApiAccessError):
    code = "invalid_api_key"


class ApiReleaseInactiveError(AgentApiAccessError):
    code = "api_release_inactive"


class ApiKeyLimitError(AgentApiAccessError):
    code = "active_key_limit"


class ApiRateLimitError(AgentApiAccessError):
    code = "rate_limit_exceeded"


class ApiConcurrencyLimitError(AgentApiAccessError):
    code = "concurrency_limit_exceeded"


class ApiKeyConfigurationError(AgentApiAccessError):
    code = "agent_api_key_pepper_missing"


@dataclass(frozen=True)
class AgentApiPrincipal:
    key_id: str
    app_id: str
    owner_user_id: str
    key_prefix: str


@dataclass(frozen=True)
class EmbedKeyPrincipal:
    key_id: str
    app_id: str
    owner_user_id: str
    origin: str


@dataclass(frozen=True)
class CreatedAgentApiKey:
    id: str
    prefix: str
    secret: str
    name: str
    expires_at: datetime | None


_window_lock = asyncio.Lock()
_window_requests: dict[str, deque[float]] = defaultdict(deque)
_concurrency_lock = asyncio.Lock()
_inflight_requests: dict[str, int] = defaultdict(int)


def _pepper() -> str:
    value = os.getenv("AGENT_API_KEY_PEPPER") or str(settings.INTERNAL_SYNC_SECRET or "")
    if not value:
        raise ApiKeyConfigurationError("agent_api_key_pepper_missing")
    return value


def _hash_secret(secret: str) -> str:
    return hmac.new(_pepper().encode("utf-8"), str(secret).encode("utf-8"), "sha256").hexdigest()


def _encrypt_key_secret(secret: str) -> str:
    """Keep a recoverable copy only for the authenticated publisher management view."""
    try:
        return encrypt_secret(secret)
    except ConnectorCryptoError as exc:
        raise ApiKeyConfigurationError("agent_api_key_encryption_unavailable") from exc


def recover_key_secret(row: AgentApiAccessKey) -> str | None:
    """Return a verified key only to the owner-only management router.

    Older rows deliberately have no ciphertext: their original one-time secret
    cannot be reconstructed from the HMAC and must stay unavailable.
    """
    ciphertext = str(getattr(row, "secret_ciphertext", "") or "")
    if not ciphertext:
        return None
    try:
        secret = decrypt_secret(ciphertext)
        expected_hash = _hash_secret(secret)
    except (ConnectorCryptoError, ApiKeyConfigurationError):
        return None
    if not hmac.compare_digest(str(getattr(row, "secret_hash", "")), expected_hash):
        return None
    return secret


def _is_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        return expires_at.replace(tzinfo=timezone.utc) <= now
    return expires_at <= now


async def _insert_key(row: AgentApiAccessKey) -> None:
    async with async_session() as session:
        session.add(row)
        await session.commit()


async def create_key(app_id: str, owner_user_id: str, name: str, expires_at: datetime | None) -> CreatedAgentApiKey:
    app_id, owner_user_id = str(app_id or "")[:64], str(owner_user_id or "")[:64]
    if not app_id or not owner_user_id:
        raise ApiAuthError("invalid_api_key")
    if _is_expired(expires_at):
        raise ApiKeyLimitError("invalid_expiry")
    secret = f"axa_{secrets.token_urlsafe(32)}"
    prefix = secret[:16]
    row = AgentApiAccessKey(
        id=f"ak_{uuid.uuid4().hex}", app_id=app_id, owner_user_id=owner_user_id,
        name=str(name or "")[:128], key_prefix=prefix, secret_hash=_hash_secret(secret),
        secret_ciphertext=_encrypt_key_secret(secret), key_kind="api",
        status="active", expires_at=expires_at, created_by=owner_user_id,
    )
    await _insert_key(row)
    return CreatedAgentApiKey(row.id, prefix, secret, row.name, expires_at)


async def create_embed_key(app_id: str, owner_user_id: str, name: str, origin: object) -> CreatedAgentApiKey:
    """Create an embed key that can only create an iframe session for one origin."""
    app_id, owner_user_id = str(app_id or "")[:64], str(owner_user_id or "")[:64]
    try:
        normalized = normalize_embed_origins([origin])
    except Exception as exc:
        raise ApiKeyLimitError("embed_origin_invalid") from exc
    if not app_id or not owner_user_id or len(normalized) != 1:
        raise ApiKeyLimitError("embed_origin_invalid")
    secret = f"axe_{secrets.token_urlsafe(32)}"
    prefix = secret[:16]
    row = AgentApiAccessKey(
        id=f"ek_{uuid.uuid4().hex}", app_id=app_id, owner_user_id=owner_user_id,
        name=str(name or "")[:128], key_prefix=prefix, secret_hash=_hash_secret(secret),
        secret_ciphertext=_encrypt_key_secret(secret), key_kind="embed", embed_origin=normalized[0],
        status="active", created_by=owner_user_id,
    )
    await _insert_key(row)
    return CreatedAgentApiKey(row.id, prefix, secret, row.name, None)


async def _load_key_by_hash(secret_hash: str) -> AgentApiAccessKey | None:
    async with async_session() as session:
        return await session.scalar(select(AgentApiAccessKey).where(AgentApiAccessKey.secret_hash == secret_hash).limit(1))


async def _is_api_release_active(app_id: str, owner_user_id: str) -> bool:
    async with async_session() as session:
        app = await session.get(WorkflowApp, app_id)
        if app is None or app.status != "published" or str(app.owner_user_id or "") != str(owner_user_id or ""):
            return False
        return bool(getattr(app, "api_enabled", False))


async def _mark_key_used(key_id: str) -> None:
    async with async_session() as session:
        await session.execute(update(AgentApiAccessKey).where(
            AgentApiAccessKey.id == key_id, AgentApiAccessKey.status == "active"
        ).values(last_used_at=func.now()))
        await session.commit()


async def _enforce_fixed_window_limit(key_id: str) -> None:
    loop, cutoff = asyncio.get_running_loop(), asyncio.get_running_loop().time() - FIXED_WINDOW_SECONDS
    async with _window_lock:
        entries = _window_requests[key_id]
        while entries and entries[0] <= cutoff:
            entries.popleft()
        if len(entries) >= FIXED_WINDOW_REQUESTS:
            raise ApiRateLimitError("rate_limit_exceeded")
        entries.append(loop.time())


async def enforce_key_rate_limit(key_id: str) -> None:
    """Apply the same per-key limit to API and browser-derived Embed sessions."""
    await _enforce_fixed_window_limit(str(key_id or "")[:64])


@asynccontextmanager
async def execution_slot(key_id: str):
    """Bound in-flight model work to protect the publisher's quota."""
    key_id = str(key_id or "")[:64]
    async with _concurrency_lock:
        if _inflight_requests[key_id] >= MAX_CONCURRENT_REQUESTS_PER_KEY:
            raise ApiConcurrencyLimitError("concurrency_limit_exceeded")
        _inflight_requests[key_id] += 1
    try:
        yield
    finally:
        async with _concurrency_lock:
            remaining = _inflight_requests.get(key_id, 1) - 1
            if remaining > 0:
                _inflight_requests[key_id] = remaining
            else:
                _inflight_requests.pop(key_id, None)


async def authenticate_bearer(raw_token: str) -> AgentApiPrincipal:
    token = str(raw_token or "")
    if not token.startswith("axa_") or len(token) < 40:
        raise ApiAuthError("invalid_api_key")
    try:
        digest = _hash_secret(token)
    except ApiKeyConfigurationError as exc:
        raise ApiAuthError("invalid_api_key") from exc
    key = await _load_key_by_hash(digest)
    if (key is None or not hmac.compare_digest(str(getattr(key, "secret_hash", "")), digest)
            or str(getattr(key, "status", "")) != "active" or _is_expired(getattr(key, "expires_at", None))):
        raise ApiAuthError("invalid_api_key")
    if not await _is_api_release_active(key.app_id, key.owner_user_id):
        raise ApiReleaseInactiveError("api_release_inactive")
    await enforce_key_rate_limit(key.id)
    try:
        await _mark_key_used(key.id)
    except Exception:
        pass  # telemetry cannot deny a verified credential
    return AgentApiPrincipal(key.id, key.app_id, key.owner_user_id, key.key_prefix)


async def _active_embed_key(key: AgentApiAccessKey, app_id: str | None = None) -> EmbedKeyPrincipal:
    if (
        key is None or str(getattr(key, "key_kind", "")) != "embed"
        or str(getattr(key, "status", "")) != "active" or _is_expired(getattr(key, "expires_at", None))
        or (app_id is not None and str(key.app_id) != str(app_id))
    ):
        raise ApiAuthError("invalid_embed_key")
    async with async_session() as session:
        app = await session.get(WorkflowApp, key.app_id)
    if (
        not app
        or str(app.status or "") != "published"
        or str(app.owner_user_id) != str(key.owner_user_id)
        or not bool(getattr(app, "api_enabled", False))
        or not bool(getattr(app, "iframe_embed_enabled", False))
    ):
        raise ApiReleaseInactiveError("api_release_inactive")
    if not key.embed_origin:
        raise ApiReleaseInactiveError("api_release_inactive")
    return EmbedKeyPrincipal(key.id, key.app_id, key.owner_user_id, key.embed_origin)


async def authenticate_embed_key(raw_token: str, app_id: str) -> EmbedKeyPrincipal:
    token = str(raw_token or "")
    if not token.startswith("axe_") or len(token) < 40:
        raise ApiAuthError("invalid_embed_key")
    key = await _load_key_by_hash(_hash_secret(token))
    if key is None or not hmac.compare_digest(str(getattr(key, "secret_hash", "")), _hash_secret(token)):
        raise ApiAuthError("invalid_embed_key")
    principal = await _active_embed_key(key, app_id)
    await enforce_key_rate_limit(principal.key_id)
    return principal


async def resolve_embed_key_frame(key_id: str, app_id: str) -> EmbedKeyPrincipal:
    async with async_session() as session:
        key = await session.get(AgentApiAccessKey, str(key_id or "")[:64])
    return await _active_embed_key(key, app_id)


async def _revoke_active_keys(app_id: str, reason: str) -> int:
    async with async_session() as session:
        result = await session.execute(update(AgentApiAccessKey).where(
            AgentApiAccessKey.app_id == app_id, AgentApiAccessKey.status == "active"
        ).values(status="revoked", revoked_at=func.now()))
        await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


async def revoke_app_access(app_id: str, reason: str = "access_revoked") -> int:
    """Called only by release handlers after their owning transaction commits."""
    app_id = str(app_id or "")[:64]
    count = await _revoke_active_keys(app_id, str(reason or "access_revoked")[:128])
    try:
        await revoke_external_sessions(app_id)
    except Exception:
        # The revoked key is still the hard authorization boundary; cleanup is
        # best effort and may be retried by a later disable/revocation action.
        pass
    return count


async def revoke_embed_access(app_id: str, reason: str = "embed_disabled") -> int:
    """Disable only static iframe keys while keeping the publisher API keys usable."""
    async with async_session() as session:
        result = await session.execute(update(AgentApiAccessKey).where(
            AgentApiAccessKey.app_id == str(app_id or "")[:64],
            AgentApiAccessKey.key_kind == "embed",
            AgentApiAccessKey.status == "active",
        ).values(status="revoked", revoked_at=func.now()))
        await session.commit()
    try:
        await revoke_external_sessions(str(app_id or "")[:64], key_kind="embed")
    except Exception:
        pass
    return int(getattr(result, "rowcount", 0) or 0)


async def revoke_key(key_id: str, owner_user_id: str) -> bool:
    async with async_session() as session:
        result = await session.execute(update(AgentApiAccessKey).where(
            AgentApiAccessKey.id == str(key_id or "")[:64],
            AgentApiAccessKey.owner_user_id == str(owner_user_id or "")[:64],
            AgentApiAccessKey.status == "active",
        ).values(status="revoked", revoked_at=func.now()))
        await session.commit()
    revoked = bool(getattr(result, "rowcount", 0))
    if revoked:
        try:
            await revoke_external_sessions_for_key(str(key_id or "")[:64])
        except Exception:
            pass
    return revoked


async def list_keys(app_id: str, owner_user_id: str, key_kind: str | None = "api") -> list[AgentApiAccessKey]:
    """Return management metadata only; callers must never serialize secret_hash."""
    async with async_session() as session:
        query = select(AgentApiAccessKey).where(
            AgentApiAccessKey.app_id == str(app_id or "")[:64],
            AgentApiAccessKey.owner_user_id == str(owner_user_id or "")[:64],
        )
        if key_kind is not None:
            query = query.where(AgentApiAccessKey.key_kind == key_kind)
        rows = await session.execute(query.order_by(AgentApiAccessKey.created_at.desc()))
        return list(rows.scalars())


async def set_key_enabled_for_app(
    key_id: str, app_id: str, owner_user_id: str, key_kind: str, *, enabled: bool,
) -> bool:
    """Stop or resume one publisher key without exposing its secret material."""
    key_id = str(key_id or "")[:64]
    app_id = str(app_id or "")[:64]
    owner_user_id = str(owner_user_id or "")[:64]
    key_kind = str(key_kind or "")[:16]
    async with async_session() as session:
        key = await session.scalar(select(AgentApiAccessKey).where(
            AgentApiAccessKey.id == key_id,
            AgentApiAccessKey.app_id == app_id,
            AgentApiAccessKey.owner_user_id == owner_user_id,
            AgentApiAccessKey.key_kind == key_kind,
        ))
        if key is None:
            return False
        if enabled:
            if str(key.status or "") != "disabled" or _is_expired(key.expires_at):
                return False
            key.status = "active"
            key.revoked_at = None
        else:
            if str(key.status or "") != "active":
                return False
            key.status = "disabled"
            key.revoked_at = func.now()
        await session.commit()
    if not enabled:
        try:
            await revoke_external_sessions_for_key(key_id)
        except Exception:
            pass
    return True


async def delete_key_for_app(key_id: str, app_id: str, owner_user_id: str, key_kind: str) -> bool:
    """Permanently remove one publisher key and invalidate its external sessions."""
    key_id = str(key_id or "")[:64]
    async with async_session() as session:
        result = await session.execute(delete(AgentApiAccessKey).where(
            AgentApiAccessKey.id == key_id,
            AgentApiAccessKey.app_id == str(app_id or "")[:64],
            AgentApiAccessKey.owner_user_id == str(owner_user_id or "")[:64],
            AgentApiAccessKey.key_kind == str(key_kind or "")[:16],
        ))
        await session.commit()
    deleted = bool(getattr(result, "rowcount", 0))
    if deleted:
        try:
            await revoke_external_sessions_for_key(key_id)
        except Exception:
            pass
    return deleted


async def revoke_key_for_app(key_id: str, app_id: str, owner_user_id: str) -> bool:
    """Revoke only the key addressed through its owning application route."""
    async with async_session() as session:
        result = await session.execute(update(AgentApiAccessKey).where(
            AgentApiAccessKey.id == str(key_id or "")[:64],
            AgentApiAccessKey.app_id == str(app_id or "")[:64],
            AgentApiAccessKey.owner_user_id == str(owner_user_id or "")[:64],
            AgentApiAccessKey.status == "active",
        ).values(status="revoked", revoked_at=func.now()))
        await session.commit()
    revoked = bool(getattr(result, "rowcount", 0))
    if revoked:
        try:
            await revoke_external_sessions_for_key(str(key_id or "")[:64])
        except Exception:
            pass
    return revoked
