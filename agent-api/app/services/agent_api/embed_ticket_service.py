"""Short-lived, in-memory sessions derived from static embed iframe keys."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from fastapi import HTTPException

from app.core.database import async_session
from app.models import AgentApiAccessKey, WorkflowApp
from app.services.agent_api.access_service import EmbedKeyPrincipal
from app.services.agent_api.access_service import enforce_key_rate_limit
from app.services.agent_api.execution_service import load_active_api_version
SESSION_TTL_SECONDS = 30 * 60


class EmbedTicketError(RuntimeError):
    """Stable errors for the iframe boundary; never include credential values."""

    def __init__(self, code: str):
        self.code = str(code or "embed_ticket_invalid")
        super().__init__(self.code)


@dataclass(frozen=True)
class EmbedSession:
    token: str
    app_id: str
    version_id: str
    key_id: str
    owner_user_id: str
    origin: str
    expires_at: datetime


@dataclass(frozen=True)
class _SessionRecord:
    session: EmbedSession
    expires_at: float


def _utc_timestamp(value: float) -> datetime:
    return datetime.fromtimestamp(value, tz=timezone.utc)


class EmbedTicketService:
    """Process-local static iframe sessions deliberately disappear on restart."""

    def __init__(self, *, secret: bytes | None = None, now: Callable[[], float] | None = None):
        self._secret = secret or secrets.token_bytes(32)
        self._now = now or time.time
        self._sessions: dict[str, _SessionRecord] = {}
        self._lock = asyncio.Lock()

    def _session_token(self, session_id: str) -> str:
        signature = hmac.new(
            self._secret, session_id.encode("utf-8"), hashlib.sha256,
        ).hexdigest()
        return f"emb_{session_id}.{signature}"

    def _verified_session_id(self, token: str) -> str:
        prefix, separator, signature = str(token or "").partition(".")
        if not prefix.startswith("emb_") or not separator or not signature:
            raise EmbedTicketError("invalid_embed_token")
        session_id = prefix[4:]
        expected = self._session_token(session_id).partition(".")[2]
        if not hmac.compare_digest(signature, expected):
            raise EmbedTicketError("invalid_embed_token")
        return session_id

    async def create_direct_session(self, principal: EmbedKeyPrincipal) -> EmbedSession:
        """Turn a browser-scoped embed key into the same short Embed session as tickets."""
        version = await load_active_api_version(principal.app_id, principal.owner_user_id)
        now = self._now()
        session_id = secrets.token_urlsafe(32)
        expires_at = now + SESSION_TTL_SECONDS
        session = EmbedSession(
            token=self._session_token(session_id), app_id=principal.app_id,
            version_id=str(getattr(version, "id", "") or ""), key_id=principal.key_id,
            owner_user_id=principal.owner_user_id, origin=principal.origin,
            expires_at=_utc_timestamp(expires_at),
        )
        async with self._lock:
            self._purge_locked(now)
            self._sessions[session_id] = _SessionRecord(session=session, expires_at=expires_at)
        return session

    async def authenticate_session(self, token: str) -> EmbedSession:
        # The iframe document is served by this platform, so its HTTP Origin is
        # necessarily the platform origin rather than the frozen parent portal.
        # Task 8's frame-ancestors CSP enforces the parent origin; possession of
        # this short-lived token is the execution authorization here.
        session_id = self._verified_session_id(token)
        now = self._now()
        async with self._lock:
            self._purge_locked(now)
            record = self._sessions.get(session_id)
        if record is None:
            raise EmbedTicketError("invalid_embed_token")
        if record.expires_at <= now:
            raise EmbedTicketError("session_expired")
        session = record.session
        await self._recheck_session_release_and_key(session)
        await enforce_key_rate_limit(session.key_id)
        return session

    async def _recheck_session_release_and_key(self, session: EmbedSession) -> None:
        async with async_session() as db_session:
            key = await db_session.get(AgentApiAccessKey, session.key_id)
            app = await db_session.get(WorkflowApp, session.app_id)
        now = datetime.now(timezone.utc)
        expires_at = getattr(key, "expires_at", None) if key is not None else None
        if (
            key is None
            or str(getattr(key, "status", "")) != "active"
            or str(getattr(key, "app_id", "")) != session.app_id
            or str(getattr(key, "owner_user_id", "")) != session.owner_user_id
            or str(getattr(key, "key_kind", "")) != "embed"
            or app is None
            or not bool(getattr(app, "api_enabled", False))
            or not bool(getattr(app, "iframe_embed_enabled", False))
            or (expires_at is not None and (
                expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at
            ) <= now)
        ):
            raise EmbedTicketError("embed_key_revoked")
        try:
            version = await load_active_api_version(session.app_id, session.owner_user_id)
        except Exception as exc:
            raise EmbedTicketError("api_release_inactive") from exc
        if str(getattr(version, "id", "") or "") != session.version_id:
            raise EmbedTicketError("api_release_inactive")

    def _purge_locked(self, now: float) -> None:
        for store in (self._sessions,):
            for key, record in list(store.items()):
                expires_at = record if isinstance(record, float) else record.expires_at
                if expires_at <= now:
                    store.pop(key, None)


embed_ticket_service = EmbedTicketService()
