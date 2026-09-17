"""Campus assistant admin APIs.  Backend gate is is_platform_admin."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response

from app.core.auth import UserContext, current_user, is_platform_admin
from app.schemas.campus_assistant import (
    DraftPublishRequest,
    DraftUpdateRequest,
    DraftValidateRequest,
    MainChatSkinMetadataUpdateRequest,
    RollbackRequest,
)
from app.services.chat.builtin_assistants.campus_services import config_service
from app.services.campus_assistant import main_chat_skin_service as skin_service
from app.services.campus_assistant.main_chat_skin_package import MAX_PACKAGE_BYTES

router = APIRouter(prefix="/campus-assistant", tags=["campus-assistant"])


def _require_admin(user: UserContext) -> None:
    if not is_platform_admin(user):
        raise HTTPException(status_code=403, detail="需要平台管理员权限")


def _raise(exc: config_service.CampusConfigError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _raise_skin(exc: skin_service.MainChatSkinError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/runtime/skin")
async def get_runtime_skin(user: UserContext = Depends(current_user)):
    """Safe presentation-only projection for the published campus main-chat skin."""
    try:
        return await skin_service.resolve_published_skin(user)
    except skin_service.MainChatSkinError as exc:
        _raise_skin(exc)


@router.get("/skins/{skin_id}/assets/{asset_key}")
async def get_skin_asset(
    skin_id: str,
    asset_key: str,
    user: UserContext = Depends(current_user),
):
    try:
        asset = await skin_service.read_skin_asset(user, skin_id, asset_key)
    except skin_service.MainChatSkinError as exc:
        _raise_skin(exc)
    return Response(
        content=bytes(asset.content),
        media_type=asset.mime_type,
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "ETag": f'"{asset.sha256}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/admin/skins")
async def list_main_chat_skins(user: UserContext = Depends(current_user)):
    _require_admin(user)
    try:
        return await skin_service.list_installed_skins(user)
    except skin_service.MainChatSkinError as exc:
        _raise_skin(exc)


@router.post("/admin/skins/import")
async def import_main_chat_skin(
    file: UploadFile = File(...),
    user: UserContext = Depends(current_user),
):
    _require_admin(user)
    filename = str(file.filename or "").strip()
    if filename and not filename.lower().endswith((".qzskin", ".zip")):
        raise HTTPException(status_code=415, detail="请选择 .qzskin 皮肤包")
    content = await file.read(MAX_PACKAGE_BYTES + 1)
    if len(content) > MAX_PACKAGE_BYTES:
        raise HTTPException(status_code=413, detail="皮肤包超过 12 MiB 限制")
    try:
        return await skin_service.import_skin_package(user, content)
    except skin_service.MainChatSkinError as exc:
        _raise_skin(exc)


@router.get("/admin/skins/{skin_id}")
async def get_main_chat_skin(
    skin_id: str,
    user: UserContext = Depends(current_user),
):
    _require_admin(user)
    try:
        return await skin_service.get_skin_detail(user, skin_id)
    except skin_service.MainChatSkinError as exc:
        _raise_skin(exc)


@router.patch("/admin/skins/{skin_id}")
async def update_main_chat_skin(
    skin_id: str,
    body: MainChatSkinMetadataUpdateRequest,
    user: UserContext = Depends(current_user),
):
    _require_admin(user)
    try:
        return await skin_service.update_skin_metadata(
            user,
            skin_id,
            name=body.name,
            description=body.description,
        )
    except skin_service.MainChatSkinError as exc:
        _raise_skin(exc)


@router.delete("/admin/skins/{skin_id}")
async def delete_main_chat_skin(
    skin_id: str,
    user: UserContext = Depends(current_user),
):
    _require_admin(user)
    try:
        return await skin_service.delete_skin(user, skin_id)
    except skin_service.MainChatSkinError as exc:
        _raise_skin(exc)


@router.get("/admin/skins/{skin_id}/export")
async def export_main_chat_skin(
    skin_id: str,
    user: UserContext = Depends(current_user),
):
    _require_admin(user)
    try:
        skin, package = await skin_service.export_skin_package(user, skin_id)
    except skin_service.MainChatSkinError as exc:
        _raise_skin(exc)
    safe_key = re.sub(r"[^a-z0-9-]", "-", skin.skin_key.lower()).strip("-") or "main-chat-skin"
    safe_version = re.sub(r"[^0-9.]", "", skin.version) or "1.0.0"
    return Response(
        content=package,
        media_type="application/vnd.axiom.skin+zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_key}-{safe_version}.qzskin"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/admin/config")
async def get_config(user: UserContext = Depends(current_user)):
    _require_admin(user)
    try:
        return await config_service.get_or_create_config(user)
    except config_service.CampusConfigError as exc:
        _raise(exc)


@router.post("/admin/draft")
async def create_draft(user: UserContext = Depends(current_user)):
    _require_admin(user)
    try:
        return await config_service.ensure_draft(user)
    except config_service.CampusConfigError as exc:
        _raise(exc)


@router.put("/admin/draft")
async def save_draft(body: DraftUpdateRequest, user: UserContext = Depends(current_user)):
    _require_admin(user)
    try:
        return await config_service.save_draft(user, body)
    except config_service.CampusConfigError as exc:
        _raise(exc)


@router.post("/admin/draft/validate")
async def validate_draft(
    body: DraftValidateRequest | None = None,
    user: UserContext = Depends(current_user),
):
    _require_admin(user)
    try:
        return await config_service.validate_draft(user, body)
    except config_service.CampusConfigError as exc:
        _raise(exc)


@router.post("/admin/draft/publish")
async def publish_draft(body: DraftPublishRequest, user: UserContext = Depends(current_user)):
    _require_admin(user)
    try:
        return await config_service.publish_draft(user, body)
    except config_service.CampusConfigError as exc:
        _raise(exc)


@router.delete("/admin/draft")
async def abandon_draft(
    expected_revision: int = Query(...),
    user: UserContext = Depends(current_user),
):
    _require_admin(user)
    try:
        return await config_service.abandon_draft(user, expected_revision)
    except config_service.CampusConfigError as exc:
        _raise(exc)


@router.get("/admin/releases")
async def list_releases(
    limit: int = 20,
    offset: int = 0,
    user: UserContext = Depends(current_user),
):
    _require_admin(user)
    return await config_service.list_releases(user, limit=limit, offset=offset)


@router.get("/admin/releases/{release_id}")
async def get_release(release_id: str, user: UserContext = Depends(current_user)):
    _require_admin(user)
    try:
        return await config_service.get_release(user, release_id)
    except config_service.CampusConfigError as exc:
        _raise(exc)


@router.post("/admin/releases/{release_id}/rollback")
async def rollback_release(
    release_id: str,
    body: RollbackRequest,
    user: UserContext = Depends(current_user),
):
    _require_admin(user)
    try:
        return await config_service.rollback_release(user, release_id, body)
    except config_service.CampusConfigError as exc:
        _raise(exc)
