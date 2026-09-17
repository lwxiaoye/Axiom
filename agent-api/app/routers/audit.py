"""Monitor-facing audit endpoints."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.auth import UserContext, current_user, is_platform_admin
from app.services.audit.audit_service import AUDIT_CATEGORIES, CLIENT_CATEGORIES, list_audit_events, record_client_event


router = APIRouter(prefix="/audit", tags=["audit"])


class ClientAuditEvent(BaseModel):
    category: Literal["login", "knowledge_access", "export"]
    action: str = Field(min_length=1, max_length=128)
    resource: str = Field(default="", max_length=255)
    detail: str = Field(default="", max_length=512)


@router.post("/events", status_code=204)
async def create_audit_event(
    payload: ClientAuditEvent,
    request: Request,
    user: UserContext = Depends(current_user),
):
    # The category is intentionally a small server-side allowlist: clients cannot forge
    # model/tool/database events, which are projected from the runtime's own ledger.
    if payload.category not in CLIENT_CATEGORIES:
        raise HTTPException(status_code=422, detail="不支持的审计类型")
    await record_client_event(
        user=user,
        category=payload.category,
        action=payload.action,
        resource=payload.resource,
        detail=payload.detail,
        ip=str(request.client.host if request.client else ""),
    )


@router.get("/events")
async def get_audit_events(
    category: str | None = None,
    keyWord: str = "",
    createTime_begin: str | None = None,
    createTime_end: str | None = None,
    pageNo: int = 1,
    pageSize: int = 10,
    user: UserContext = Depends(current_user),
):
    if not is_platform_admin(user):
        raise HTTPException(status_code=403, detail="仅平台管理员可查看日志审计")
    return await list_audit_events(
        category=category if category in AUDIT_CATEGORIES else None,
        keyword=keyWord,
        start=createTime_begin,
        end=createTime_end,
        page_no=pageNo,
        page_size=pageSize,
    )
