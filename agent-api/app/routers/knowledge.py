"""知识库管理与检索接口。

归属 agent-api 而非 auth-api：Qdrant 与 Embedding 配置都在这里，让认证服务去
持有向量库既越界也无从实现（auth-api 用的是 SQLite，且不接 Qdrant）。
"""
import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, UploadFile, File

from app.core.auth import UserContext, current_user, is_admin
from app.services.knowledge import knowledge_base_service as kb

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


async def _require_access(knowledge_id: str, user: UserContext) -> dict[str, Any]:
    base = await kb.get_base(knowledge_id)
    if base is None:
        raise HTTPException(404, "知识库不存在")
    if base["ownerUserId"] != str(user.user_id) and not is_admin(user):
        acls = await kb.list_acl(knowledge_id)
        if not any(
            a["subjectType"] == "user" and a["subjectId"] == str(user.user_id)
            for a in acls
        ):
            raise HTTPException(403, "没有该知识库的访问权限")
    return base


@router.get("/bases")
async def list_bases(
    scope: str = Query("owned"),
    user: UserContext = Depends(current_user),
):
    return await kb.list_bases(user_id=str(user.user_id), scope=scope)


@router.post("/bases")
async def create_base(
    body: dict = Body(...),
    user: UserContext = Depends(current_user),
):
    try:
        return await kb.create_base(
            user_id=str(user.user_id),
            username=getattr(user, "username", "") or "",
            name=str(body.get("name") or ""),
            description=str(body.get("description") or ""),
            tenant_id=str(getattr(user, "tenant_id", "0") or "0"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/bases/{knowledge_id}")
async def get_base(knowledge_id: str, user: UserContext = Depends(current_user)):
    return await _require_access(knowledge_id, user)


@router.delete("/bases/{knowledge_id}")
async def delete_base(knowledge_id: str, user: UserContext = Depends(current_user)):
    base = await _require_access(knowledge_id, user)
    if base["ownerUserId"] != str(user.user_id) and not is_admin(user):
        raise HTTPException(403, "只有所有者可以删除知识库")
    await kb.delete_base(knowledge_id)
    return {"success": True}


@router.get("/bases/{knowledge_id}/documents")
async def list_documents(knowledge_id: str, user: UserContext = Depends(current_user)):
    await _require_access(knowledge_id, user)
    return await kb.list_documents(knowledge_id)


@router.post("/bases/{knowledge_id}/documents")
async def add_document(
    knowledge_id: str,
    body: dict = Body(...),
    user: UserContext = Depends(current_user),
):
    """以纯文本入库。文件上传见 /documents/upload。"""
    await _require_access(knowledge_id, user)
    try:
        return await kb.add_document(
            knowledge_id=knowledge_id,
            name=str(body.get("name") or "未命名"),
            text=str(body.get("text") or ""),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/bases/{knowledge_id}/documents/upload")
async def upload_document(
    knowledge_id: str,
    file: UploadFile = File(...),
    user: UserContext = Depends(current_user),
):
    await _require_access(knowledge_id, user)
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gbk")
        except UnicodeDecodeError as exc:
            raise HTTPException(400, "暂仅支持 UTF-8 / GBK 编码的纯文本") from exc
    try:
        return await kb.add_document(
            knowledge_id=knowledge_id,
            name=file.filename or "未命名",
            text=text,
            content_type=file.content_type or "text/plain",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/bases/{knowledge_id}/acl")
async def list_acl(knowledge_id: str, user: UserContext = Depends(current_user)):
    await _require_access(knowledge_id, user)
    return await kb.list_acl(knowledge_id)


@router.post("/retrieval")
async def retrieval(
    body: dict = Body(...),
    user: UserContext = Depends(current_user),
):
    ids = body.get("knowledgeIds") or body.get("knowledge_ids") or []
    for kid in ids:
        await _require_access(str(kid), user)
    try:
        chunks = await kb.search_chunks(
            knowledge_ids=[str(x) for x in ids],
            query=str(body.get("query") or ""),
            top_k=int(body.get("topK") or body.get("top_k") or 5),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "chunks": chunks}
