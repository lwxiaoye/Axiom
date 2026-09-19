"""Campus assistant admin APIs.  Backend gate is is_platform_admin."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import UserContext, current_user, is_platform_admin
from app.schemas.campus_assistant import (
    DraftPublishRequest,
    DraftUpdateRequest,
    DraftValidateRequest,
    RollbackRequest,
)
from app.services.chat.builtin_assistants.campus_services import config_service

router = APIRouter(prefix="/campus-assistant", tags=["campus-assistant"])


def _require_admin(user: UserContext) -> None:
    if not is_platform_admin(user):
        raise HTTPException(status_code=403, detail="需要平台管理员权限")


def _raise(exc: config_service.CampusConfigError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


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
