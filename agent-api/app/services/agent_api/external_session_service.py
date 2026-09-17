"""Durable, key-scoped workspaces for external Agent API and static embeds.

External callers are deliberately not mapped to a platform user.  Every workspace
and uploaded file is instead scoped by the immutable access-key identity, so the
publisher remains the billing principal without exposing their private files.
"""
from __future__ import annotations

import inspect
import mimetypes
import os
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.database import async_session
from app.models import AgentApiAccessKey, ExternalAgentSession, ExternalAgentSessionFile
from app.services.files.storage import get_file_storage

if TYPE_CHECKING:
    from app.services.agent_api.access_service import AgentApiPrincipal, EmbedKeyPrincipal


class ExternalSessionAccessError(RuntimeError):
    """Stable boundary error that reveals neither other scopes nor storage paths."""

    code = "external_session_access_error"

    def __init__(self, code: str):
        self.code = str(code or self.code)
        super().__init__(self.code)


@dataclass(frozen=True)
class ExternalSessionHandle:
    id: str
    app_id: str
    api_key_id: str
    owner_user_id: str
    session_id: str
    workspace_ref: str


@dataclass(frozen=True)
class ExternalFileHandle:
    id: str
    external_session_id: str
    storage_ref: str
    original_name: str
    content_type: str
    size_bytes: int


ExternalPrincipal = Any
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class _ExternalSessionStore(Protocol):
    async def get_session(self, app_id: str, api_key_id: str, session_id: str) -> Any | None:
        ...

    async def create_session(self, **values: Any) -> Any:
        ...

    async def get_file(self, external_session_id: str, file_id: str) -> Any | None:
        ...

    async def create_file(self, **values: Any) -> Any:
        ...


class _SqlAlchemyExternalSessionStore:
    async def get_session(self, app_id: str, api_key_id: str, session_id: str) -> ExternalAgentSession | None:
        async with async_session() as session:
            return await session.scalar(
                select(ExternalAgentSession).where(
                    ExternalAgentSession.app_id == app_id,
                    ExternalAgentSession.api_key_id == api_key_id,
                    ExternalAgentSession.session_id == session_id,
                )
            )

    async def create_session(self, **values: Any) -> ExternalAgentSession:
        row = ExternalAgentSession(**values)
        async with async_session() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise
            await session.refresh(row)
        return row

    async def get_file(self, external_session_id: str, file_id: str) -> ExternalAgentSessionFile | None:
        async with async_session() as session:
            return await session.scalar(
                select(ExternalAgentSessionFile).where(
                    ExternalAgentSessionFile.external_session_id == external_session_id,
                    ExternalAgentSessionFile.id == file_id,
                )
            )

    async def create_file(self, **values: Any) -> ExternalAgentSessionFile:
        row = ExternalAgentSessionFile(**values)
        async with async_session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row


def _session_handle(row: Any) -> ExternalSessionHandle:
    return ExternalSessionHandle(
        id=str(row.id),
        app_id=str(row.app_id),
        api_key_id=str(row.api_key_id),
        owner_user_id=str(row.owner_user_id),
        session_id=str(row.session_id),
        workspace_ref=str(row.workspace_ref),
    )


def _file_handle(row: Any) -> ExternalFileHandle:
    return ExternalFileHandle(
        id=str(row.id),
        external_session_id=str(row.external_session_id),
        storage_ref=str(row.storage_ref),
        original_name=str(row.original_name),
        content_type=str(getattr(row, "mime_type", "") or "application/octet-stream"),
        size_bytes=int(getattr(row, "size_bytes", 0) or 0),
    )


def _safe_filename(filename: str) -> str:
    value = os.path.basename(str(filename or "").replace("\\", "/")).replace("\x00", "").strip()
    return (value if value not in {"", ".", ".."} else "file")[:200]


async def _read_upload(stream: Any) -> bytes:
    if isinstance(stream, bytes):
        return stream
    if isinstance(stream, bytearray):
        return bytes(stream)
    read = getattr(stream, "read", None)
    if not callable(read):
        raise ExternalSessionAccessError("external_file_invalid")
    data = read()
    if inspect.isawaitable(data):
        data = await data
    if not isinstance(data, (bytes, bytearray)):
        raise ExternalSessionAccessError("external_file_invalid")
    return bytes(data)


class ExternalAgentSessionService:
    def __init__(self, *, store: _ExternalSessionStore | None = None, storage: Any | None = None):
        self._store = store or _SqlAlchemyExternalSessionStore()
        self._storage = storage or get_file_storage()

    async def get_or_create_external_session(
        self, principal: ExternalPrincipal, session_id: str
    ) -> ExternalSessionHandle:
        app_id = str(principal.app_id or "")[:64]
        key_id = str(principal.key_id or "")[:64]
        owner_user_id = str(principal.owner_user_id or "")[:64]
        clean_session_id = str(session_id or "").strip()
        if (
            not app_id or not key_id or not owner_user_id
            or not _SESSION_ID.fullmatch(clean_session_id)
        ):
            raise ExternalSessionAccessError("external_session_invalid")

        current = await self._store.get_session(app_id, key_id, clean_session_id)
        if current is not None:
            if str(getattr(current, "status", "active")) != "active":
                raise ExternalSessionAccessError("external_session_inactive")
            return _session_handle(current)

        external_session_id = f"exts_{uuid.uuid4().hex}"
        values = {
            "id": external_session_id,
            "app_id": app_id,
            # Legacy deployments used the same table for ticket sessions and
            # may still require these columns. They are compatibility metadata
            # only; the key/app/session tuple below remains the authority.
            "published_version": 0,
            "visitor_id": key_id,
            "origin": "publisher-agent-api",
            "api_key_id": key_id,
            "owner_user_id": owner_user_id,
            "session_id": clean_session_id,
            # This reference becomes the external execution namespace in the Harness.
            "workspace_ref": f"external/{external_session_id}",
            "status": "active",
        }
        try:
            created = await self._store.create_session(**values)
        except IntegrityError:
            # The unique scope constraint resolves multi-worker create races without
            # ever widening the lookup to another access key.
            created = await self._store.get_session(app_id, key_id, clean_session_id)
            if created is None:
                raise
        return _session_handle(created)

    async def save_external_file(
        self,
        handle: ExternalSessionHandle,
        filename: str,
        content_type: str,
        stream: Any,
    ) -> ExternalFileHandle:
        data = await _read_upload(stream)
        file_id = f"extfile_{uuid.uuid4().hex}"
        safe_name = _safe_filename(filename)
        storage_ref = f"external/{handle.id}/{file_id}_{safe_name}"
        mime_type = str(content_type or "application/octet-stream")[:255]
        await self._storage.write_bytes(storage_ref, data, content_type=mime_type)
        try:
            row = await self._store.create_file(
                id=file_id,
                external_session_id=handle.id,
                storage_ref=storage_ref,
                original_name=safe_name,
                mime_type=mime_type,
                size_bytes=len(data),
                status="ready",
            )
        except Exception:
            await self._storage.delete(storage_ref)
            raise
        return _file_handle(row)

    async def resolve_external_file(
        self, handle: ExternalSessionHandle, file_id: str
    ) -> ExternalFileHandle:
        row = await self._store.get_file(handle.id, str(file_id or "")[:64])
        if row is None or str(getattr(row, "status", "")) != "ready":
            raise ExternalSessionAccessError("external_file_not_found")
        result = _file_handle(row)
        if not await self._storage.exists(result.storage_ref):
            raise ExternalSessionAccessError("external_file_not_found")
        return result


_service = ExternalAgentSessionService()


async def get_or_create_external_session(
    principal: ExternalPrincipal, session_id: str
) -> ExternalSessionHandle:
    return await _service.get_or_create_external_session(principal, session_id)


async def save_external_file(
    handle: ExternalSessionHandle, filename: str, content_type: str, stream: Any
) -> ExternalFileHandle:
    return await _service.save_external_file(handle, filename, content_type, stream)


async def resolve_external_file(handle: ExternalSessionHandle, file_id: str) -> ExternalFileHandle:
    return await _service.resolve_external_file(handle, file_id)


async def read_external_file(external_session_id: str, file_id: str) -> tuple[ExternalFileHandle, bytes]:
    """Internal Harness resolver. The opaque session ID is never accepted as a public authority."""
    async with async_session() as session:
        row = await session.scalar(
            select(ExternalAgentSessionFile).where(
                ExternalAgentSessionFile.external_session_id == str(external_session_id or "")[:64],
                ExternalAgentSessionFile.id == str(file_id or "")[:64],
                ExternalAgentSessionFile.status == "ready",
            )
        )
    if row is None:
        raise ExternalSessionAccessError("external_file_not_found")
    handle = _file_handle(row)
    storage = get_file_storage()
    if not await storage.exists(handle.storage_ref):
        raise ExternalSessionAccessError("external_file_not_found")
    return handle, await storage.read_bytes(handle.storage_ref)


async def get_external_file_content(
    external_session_id: str,
    file_id: str,
    *,
    newapi_key: str = "",
    ocr_embedded_images: bool = False,
    ocr_visual: bool = False,
    audit_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the same parsing shape as a private upload, without a user-file lookup."""
    from app.services.files import document_parse_service

    handle, data = await read_external_file(external_session_id, file_id)
    filename = handle.original_name
    mime = handle.content_type or mimetypes.guess_type(filename)[0] or ""
    base = {"id": handle.id, "filename": filename, "mime": mime, "size": handle.size_bytes}
    if mime.startswith("image/") and not ocr_embedded_images:
        return {**base, "kind": "image", "text": "", "truncated": False, "data": data}
    extension = os.path.splitext(filename.lower())[1]
    if mime.startswith("text/") or extension in {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".xml", ".yaml", ".yml", ".html"}:
        text = data.decode("utf-8", errors="replace")
        return {**base, "kind": "text", "text": text[:50_000], "truncated": len(text) > 50_000, "data": data}
    parsed = await document_parse_service.parse_upload(
        filename,
        data,
        newapi_key=newapi_key,
        ocr_embedded_images=ocr_embedded_images,
        ocr_visual=ocr_visual,
        audit_context=audit_context,
    )
    return {
        **base,
        "kind": str(parsed.get("kind") or ""),
        "text": str(parsed.get("text") or ""),
        "truncated": bool(parsed.get("truncated")),
        "status": str(parsed.get("status") or "ok"),
        "note": parsed.get("note"),
        "data": data,
    }


async def revoke_external_sessions(app_id: str, key_kind: str | None = None) -> int:
    """Deactivate sessions after their API/iframe access is disabled or revoked."""
    app_id = str(app_id or "")[:64]
    if not app_id:
        return 0
    async with async_session() as session:
        criteria = [
            ExternalAgentSession.app_id == app_id,
            ExternalAgentSession.status == "active",
        ]
        if key_kind is not None:
            key_ids = select(AgentApiAccessKey.id).where(
                AgentApiAccessKey.app_id == app_id,
                AgentApiAccessKey.key_kind == str(key_kind or "")[:16],
            )
            criteria.append(ExternalAgentSession.api_key_id.in_(key_ids))
        result = await session.execute(
            update(ExternalAgentSession).where(*criteria).values(status="inactive")
        )
        await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


async def revoke_external_sessions_for_key(key_id: str) -> int:
    async with async_session() as session:
        result = await session.execute(
            update(ExternalAgentSession)
            .where(
                ExternalAgentSession.api_key_id == str(key_id or "")[:64],
                ExternalAgentSession.status == "active",
            )
            .values(status="inactive")
        )
        await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)
