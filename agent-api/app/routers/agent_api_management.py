"""Owner-only management APIs for publisher Agent API credentials and usage."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.core.auth import UserContext, current_user
from app.core.database import async_session
from app.models import AgentApiInvocation, WorkflowApp
from app.services.agent_api.access_service import (
    ApiKeyLimitError,
    create_embed_key,
    create_key,
    delete_key_for_app,
    list_keys,
    recover_key_secret,
    set_key_enabled_for_app,
)
from app.services.agent_api.external_session_service import revoke_external_sessions
from app.services.agents.published_visibility import load_published_visibility_version
from app.services.agent_api.publish_policy import validate_api_workflow_capabilities


router = APIRouter(prefix="/workflow/apps", tags=["publisher-agent-api-management"])


class CreateKeyRequest(BaseModel):
    name: str = Field(default="", max_length=128)
    expiresAt: datetime | None = None


class CreateEmbedKeyRequest(BaseModel):
    name: str = Field(default="", max_length=128)
    origin: str = Field(min_length=1, max_length=255)


class KeyStatusRequest(BaseModel):
    enabled: bool


class PublicConfigurationRequest(BaseModel):
    apiEnabled: bool
    iframeEnabled: bool


async def _require_owner(app_id: str, user: UserContext) -> WorkflowApp:
    async with async_session() as session:
        app = await session.get(WorkflowApp, str(app_id or "")[:64])
    if app is None:
        raise HTTPException(404, "应用不存在")
    if str(app.owner_user_id or "") != str(user.user_id or ""):
        raise HTTPException(403, "仅应用发布者可管理 API")
    return app


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _key_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id), "name": str(row.name or ""), "prefix": str(row.key_prefix or ""),
        # This endpoint is protected by _require_owner. The encrypted database
        # copy is never returned by OpenAI/iframe public endpoints.
        "secret": recover_key_secret(row),
        "status": str(row.status or ""), "expiresAt": _iso(getattr(row, "expires_at", None)),
        "lastUsedAt": _iso(getattr(row, "last_used_at", None)),
        "revokedAt": _iso(getattr(row, "revoked_at", None)),
        "createdAt": _iso(getattr(row, "created_at", None)),
    }


def _public_config_dict(app: WorkflowApp) -> dict[str, bool]:
    return {
        "apiEnabled": bool(getattr(app, "api_enabled", False)),
        "iframeEnabled": bool(getattr(app, "iframe_embed_enabled", False)),
    }


async def get_public_configuration(app_id: str, owner_user_id: str) -> dict[str, bool]:
    async with async_session() as session:
        app = await session.get(WorkflowApp, str(app_id or "")[:64])
    if app is None or str(app.owner_user_id or "") != str(owner_user_id or ""):
        raise HTTPException(404, "应用不存在")
    return _public_config_dict(app)


async def update_public_configuration(
    app_id: str, owner_user_id: str, api_enabled: bool, iframe_enabled: bool,
) -> dict[str, bool]:
    if iframe_enabled and not api_enabled:
        raise HTTPException(400, "启用 iframe 前请先启用 API")
    async with async_session() as session:
        app = await session.scalar(
            select(WorkflowApp).where(WorkflowApp.id == str(app_id or "")[:64]).with_for_update()
        )
        if app is None or str(app.owner_user_id or "") != str(owner_user_id or ""):
            raise HTTPException(404, "应用不存在")
        if api_enabled:
            if str(app.status or "") != "published":
                raise HTTPException(400, "请先发布智能体到广场")
            version = await load_published_visibility_version(session, app.id)
            if version is None:
                raise HTTPException(400, "已发布智能体缺少线上版本")
            validate_api_workflow_capabilities(str(version.definition_json or ""), external_context_ready=True)
        previous_api = bool(getattr(app, "api_enabled", False))
        previous_iframe = bool(getattr(app, "iframe_embed_enabled", False))
        app.api_enabled = api_enabled
        app.iframe_embed_enabled = iframe_enabled
        await session.commit()
        result = _public_config_dict(app)
    if previous_api and not api_enabled:
        # The app-level switch is an access gate, not a destructive key
        # revocation. Keep individual key enablement intact for a later reopen.
        try:
            await revoke_external_sessions(str(app_id or "")[:64])
        except Exception:
            pass
    elif previous_iframe and not iframe_enabled:
        try:
            await revoke_external_sessions(str(app_id or "")[:64], key_kind="embed")
        except Exception:
            pass
    return result


@router.get("/{app_id}/public-config")
async def get_public_config(app_id: str, user: UserContext = Depends(current_user)) -> dict[str, bool]:
    await _require_owner(app_id, user)
    return await get_public_configuration(app_id, user.user_id)


@router.put("/{app_id}/public-config")
async def update_public_config(
    app_id: str, payload: PublicConfigurationRequest, user: UserContext = Depends(current_user),
) -> dict[str, bool]:
    await _require_owner(app_id, user)
    return await update_public_configuration(app_id, user.user_id, payload.apiEnabled, payload.iframeEnabled)


@router.get("/{app_id}/api-keys")
async def get_api_keys(app_id: str, user: UserContext = Depends(current_user)) -> dict[str, Any]:
    await _require_owner(app_id, user)
    return {"items": [_key_dict(row) for row in await list_keys(app_id, user.user_id)]}


@router.post("/{app_id}/api-keys", status_code=201)
async def create_api_key(
    app_id: str, payload: CreateKeyRequest, user: UserContext = Depends(current_user),
) -> dict[str, Any]:
    app = await _require_owner(app_id, user)
    if not bool(getattr(app, "api_enabled", False)):
        raise HTTPException(409, "请先在公开配置中启用 API")
    try:
        created = await create_key(app_id, user.user_id, payload.name.strip(), payload.expiresAt)
    except ApiKeyLimitError as error:
        raise HTTPException(400, error.code) from error
    return {
        "id": created.id, "name": created.name, "prefix": created.prefix,
        "secret": created.secret, "expiresAt": _iso(created.expires_at),
    }


@router.get("/{app_id}/embed-keys")
async def get_embed_keys(app_id: str, user: UserContext = Depends(current_user)) -> dict[str, Any]:
    await _require_owner(app_id, user)
    return {"items": [_key_dict(row) | {"origin": row.embed_origin} for row in await list_keys(app_id, user.user_id, "embed")]}


@router.post("/{app_id}/embed-keys", status_code=201)
async def create_embed_access_key(
    app_id: str, payload: CreateEmbedKeyRequest, user: UserContext = Depends(current_user),
) -> dict[str, Any]:
    app = await _require_owner(app_id, user)
    if not bool(getattr(app, "api_enabled", False)) or not bool(getattr(app, "iframe_embed_enabled", False)):
        raise HTTPException(409, "请先在公开配置中启用 iframe 嵌入")
    try:
        created = await create_embed_key(app_id, user.user_id, payload.name.strip(), payload.origin)
    except ApiKeyLimitError as error:
        raise HTTPException(400, error.code) from error
    return {
        "id": created.id, "name": created.name, "prefix": created.prefix,
        "secret": created.secret, "origin": payload.origin,
    }


async def _set_key_status(
    key_id: str, app_id: str, owner_user_id: str, key_kind: str, enabled: bool,
) -> Response:
    try:
        changed = await set_key_enabled_for_app(
            key_id, app_id, owner_user_id, key_kind, enabled=enabled,
        )
    except ApiKeyLimitError as error:
        raise HTTPException(400, error.code) from error
    if not changed:
        raise HTTPException(404, "Key 不存在、已删除、已过期或当前状态不可切换")
    return Response(status_code=204)


@router.patch("/{app_id}/embed-keys/{key_id}/status", status_code=204)
@router.put("/{app_id}/embed-keys/{key_id}/status", status_code=204)
async def update_embed_key_status(
    app_id: str, key_id: str, payload: KeyStatusRequest, user: UserContext = Depends(current_user),
) -> Response:
    await _require_owner(app_id, user)
    return await _set_key_status(key_id, app_id, user.user_id, "embed", payload.enabled)


@router.patch("/{app_id}/api-keys/{key_id}/status", status_code=204)
@router.put("/{app_id}/api-keys/{key_id}/status", status_code=204)
async def update_api_key_status(
    app_id: str, key_id: str, payload: KeyStatusRequest, user: UserContext = Depends(current_user),
) -> Response:
    await _require_owner(app_id, user)
    return await _set_key_status(key_id, app_id, user.user_id, "api", payload.enabled)


@router.delete("/{app_id}/embed-keys/{key_id}", status_code=204)
async def delete_embed_key(app_id: str, key_id: str, user: UserContext = Depends(current_user)) -> Response:
    await _require_owner(app_id, user)
    if not await delete_key_for_app(key_id, app_id, user.user_id, "embed"):
        raise HTTPException(404, "嵌入 Key 不存在或已删除")
    return Response(status_code=204)


@router.delete("/{app_id}/api-keys/{key_id}", status_code=204)
async def delete_api_key(app_id: str, key_id: str, user: UserContext = Depends(current_user)) -> Response:
    await _require_owner(app_id, user)
    if not await delete_key_for_app(key_id, app_id, user.user_id, "api"):
        raise HTTPException(404, "API Key 不存在或已删除")
    return Response(status_code=204)


@router.get("/{app_id}/api-usage")
async def get_api_usage(
    app_id: str,
    pageNo: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    user: UserContext = Depends(current_user),
) -> dict[str, Any]:
    await _require_owner(app_id, user)
    async with async_session() as session:
        query = select(AgentApiInvocation).where(
            AgentApiInvocation.app_id == str(app_id),
            AgentApiInvocation.owner_user_id == str(user.user_id),
        )
        total = len((await session.execute(query)).scalars().all())
        rows = (await session.execute(
            query.order_by(desc(AgentApiInvocation.started_at)).offset((pageNo - 1) * pageSize).limit(pageSize)
        )).scalars().all()
    return {
        "total": total,
        "records": [{
            "id": row.id, "versionId": row.version_id, "keyId": row.api_key_id,
            "source": row.source, "status": row.status, "httpStatus": row.http_status,
            "durationMs": row.latency_ms, "inputTokens": row.input_tokens,
            "outputTokens": row.output_tokens, "reasoningTokens": row.reasoning_tokens,
            "usageKnown": bool(row.usage_known), "providerAmountRaw": row.provider_amount_raw,
            "providerAmountUnit": row.provider_amount_unit, "startedAt": _iso(row.started_at),
            "completedAt": _iso(row.completed_at), "errorCode": row.error_code,
        } for row in rows],
    }
