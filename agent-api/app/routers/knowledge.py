"""知识库管理与检索接口。

归属 agent-api 而非 auth-api：Qdrant 与 Embedding 配置都在这里，让认证服务去
持有向量库既越界也无从实现（auth-api 用的是 SQLite，且不接 Qdrant）。
"""
import io
import logging
import time
import zipfile
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import Response

from app.core.auth import UserContext, current_user, is_admin
from app.services.knowledge import chunk_service
from app.services.knowledge import knowledge_base_service as kb
from app.services.knowledge import retrieval_log_service as retrieval_log

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


async def _load(knowledge_id: str, user: UserContext) -> Optional[dict[str, Any]]:
    """读知识库并附上当前用户的权限（currentPermission）。

    页面靠这个字段决定「设置 / 添加文档 / 授权」是否可见，漏掉它所有者也会
    被当成 VIEWER。
    """
    return await kb.get_base(
        knowledge_id, user_id=str(user.user_id), is_admin=is_admin(user)
    )


async def _require_edit(knowledge_id: str, user: UserContext) -> dict[str, Any]:
    base = await _require_access(knowledge_id, user)
    if base["currentPermission"] not in {"EDITOR", "OWNER"}:
        raise HTTPException(403, "没有该知识库的编辑权限")
    return base


async def _require_owner(knowledge_id: str, user: UserContext) -> dict[str, Any]:
    base = await _require_access(knowledge_id, user)
    if base["currentPermission"] != "OWNER":
        raise HTTPException(403, "只有所有者可以执行该操作")
    return base


async def _require_access(knowledge_id: str, user: UserContext) -> dict[str, Any]:
    base = await _load(knowledge_id, user)
    if base is None:
        raise HTTPException(404, "知识库不存在")
    # permission_for 把「所有者 / 管理员 / ACL 命中」统一成一个权限值，
    # None 就是无权访问。
    if base.get("currentPermission") is None:
        raise HTTPException(403, "没有该知识库的访问权限")
    return base


@router.get("/bases")
async def list_bases(
    scope: str = Query("owned"),
    user: UserContext = Depends(current_user),
):
    return await kb.list_bases(
        user_id=str(user.user_id), scope=scope, is_admin=is_admin(user)
    )


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
    await _require_owner(knowledge_id, user)
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
    await _require_edit(knowledge_id, user)
    try:
        return await kb.add_document(
            knowledge_id=knowledge_id,
            name=str(body.get("name") or "未命名"),
            text=str(body.get("text") or ""),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


async def _read_text_upload(file: UploadFile) -> tuple[bytes, str]:
    """读上传文件并解码成文本；上传与「预览分段」共用，两边看到的必须是同一份文本。"""
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限")
    try:
        return raw, raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw, raw.decode("gbk")
        except UnicodeDecodeError as exc:
            raise HTTPException(400, "暂仅支持 UTF-8 / GBK 编码的纯文本") from exc


@router.post("/bases/{knowledge_id}/documents/preview")
async def preview_document(
    knowledge_id: str,
    file: UploadFile = File(...),
    user: UserContext = Depends(current_user),
):
    """上传向导的「预览分段」：只切不入库。

    用与入库相同的切法，用户在向导里看到的分段就是上传后的分段。向导表单里的
    splitStrategy / chunkSize 等参数是老 Java 的口径，agent-api 入库时并不读它们
    （切法固定为 knowledge_base_service.split_text），这里同样不读，免得预览和实际
    入库两套结果。
    """
    await _require_edit(knowledge_id, user)
    _, text = await _read_text_upload(file)
    return chunk_service.preview_split(file.filename or "未命名", text)


@router.post("/bases/{knowledge_id}/documents/upload")
async def upload_document(
    knowledge_id: str,
    file: UploadFile = File(...),
    user: UserContext = Depends(current_user),
):
    await _require_edit(knowledge_id, user)
    raw, text = await _read_text_upload(file)
    try:
        return await kb.add_document(
            knowledge_id=knowledge_id,
            name=file.filename or "未命名",
            text=text,
            content_type=file.content_type or "text/plain",
            raw=raw,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/bases/{knowledge_id}")
async def update_base(
    knowledge_id: str,
    body: dict = Body(...),
    user: UserContext = Depends(current_user),
):
    await _require_edit(knowledge_id, user)
    try:
        return await kb.update_base(
            knowledge_id,
            name=body.get("name"),
            description=body.get("description"),
            top_k=body.get("topK", body.get("top_k")),
            score_threshold=body.get("scoreThreshold", body.get("score_threshold")),
            retrieval_mode=body.get("retrievalMode", body.get("retrieval_mode")),
            semantic_weight=body.get("semanticWeight", body.get("semantic_weight")),
            keyword_weight=body.get("keywordWeight", body.get("keyword_weight")),
            user_id=str(user.user_id),
            is_admin=is_admin(user),
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/bases/{knowledge_id}/enabled")
async def set_enabled(
    knowledge_id: str,
    body: dict = Body(...),
    user: UserContext = Depends(current_user),
):
    await _require_owner(knowledge_id, user)
    try:
        return await kb.set_base_enabled(
            knowledge_id,
            bool(body.get("enabled", True)),
            user_id=str(user.user_id),
            is_admin=is_admin(user),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/bases/{knowledge_id}/documents/delete")
async def delete_documents(
    knowledge_id: str,
    body: dict = Body(...),
    user: UserContext = Depends(current_user),
):
    await _require_edit(knowledge_id, user)
    ids = body.get("ids") or body.get("documentIds") or []
    if isinstance(ids, str):
        ids = [x for x in ids.split(",") if x]
    removed = await kb.delete_documents(knowledge_id, [str(x) for x in ids])
    return {"success": True, "removed": removed}


@router.post("/bases/{knowledge_id}/acl")
async def save_acl(
    knowledge_id: str,
    body: dict = Body(...),
    user: UserContext = Depends(current_user),
):
    await _require_owner(knowledge_id, user)
    entries = body.get("acls") or body.get("entries") or []
    try:
        return await kb.save_acl(knowledge_id, list(entries))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/bases/{knowledge_id}/acl")
async def list_acl(knowledge_id: str, user: UserContext = Depends(current_user)):
    await _require_access(knowledge_id, user)
    return await kb.list_acl(knowledge_id)


async def _require_doc_access(document_id: str, user: UserContext) -> dict[str, Any]:
    doc = await kb.get_document(document_id)
    if doc is None:
        raise HTTPException(404, "文档不存在")
    await _require_access(doc["knowledgeId"], user)
    return doc


async def _require_doc_edit(document_id: str, user: UserContext) -> dict[str, Any]:
    doc = await kb.get_document(document_id)
    if doc is None:
        raise HTTPException(404, "文档不存在")
    await _require_edit(doc["knowledgeId"], user)
    return doc


@router.post("/documents/delete")
async def delete_documents_flat(
    body: dict = Body(...),
    user: UserContext = Depends(current_user),
):
    """按文档 id 删除，内部定位所属知识库——前端只持有文档 id。"""
    ids = body.get("ids") or body.get("documentIds") or []
    if isinstance(ids, str):
        ids = [x for x in ids.split(",") if x]
    ids = [str(x).strip() for x in ids if str(x).strip()]
    if not ids:
        return {"success": True, "removed": 0}
    grouped: dict[str, list[str]] = {}
    for did in ids:
        doc = await _require_doc_edit(did, user)
        grouped.setdefault(doc["knowledgeId"], []).append(did)
    removed = 0
    for kid, items in grouped.items():
        removed += await kb.delete_documents(kid, items)
    return {"success": True, "removed": removed}


@router.post("/documents/enabled")
async def set_document_enabled_flat(
    body: dict = Body(...),
    user: UserContext = Depends(current_user),
):
    document_id = str(body.get("id") or "")
    await _require_doc_edit(document_id, user)
    try:
        return await kb.set_document_enabled(document_id, bool(body.get("enabled", True)))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/documents/retry")
async def retry_document(
    body: dict = Body(...),
    user: UserContext = Depends(current_user),
):
    """入库是同步的，失败即已知原因；重试等于重新上传，这里明确告知而不是假装排队。"""
    doc = await _require_doc_access(str(body.get("id") or ""), user)
    raise HTTPException(
        400,
        f"「{doc['name']}」需要重新上传：失败原因 {doc['errorMessage'] or '未知'}",
    )


def _attachment_headers(filename: str) -> dict[str, str]:
    """中文文件名必须走 RFC 5987 的 filename*，否则浏览器存成乱码或 download。"""
    fallback = quote(filename.encode("utf-8"))
    return {
        "Content-Disposition":
            f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{fallback}",
    }


@router.get("/documents/{document_id}/download")
async def download_document(document_id: str, user: UserContext = Depends(current_user)):
    doc = await _require_doc_access(document_id, user)
    raw = kb.read_document_file(doc["knowledgeId"], document_id)
    if raw is None:
        raise HTTPException(404, f"「{doc['originalName']}」没有留存原文，请重新上传")
    return Response(
        content=raw,
        media_type=doc["contentType"] or "application/octet-stream",
        headers=_attachment_headers(doc["originalName"]),
    )


@router.post("/documents/download-zip")
async def download_documents_zip(
    body: dict = Body(...),
    user: UserContext = Depends(current_user),
):
    ids = body.get("ids") or body.get("documentIds") or []
    if isinstance(ids, str):
        ids = [x for x in ids.split(",") if x]
    ids = [str(x).strip() for x in ids if str(x).strip()]
    if not ids:
        raise HTTPException(400, "没有选择文档")
    buffer = io.BytesIO()
    missing: list[str] = []
    used: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for did in ids:
            doc = await _require_doc_access(did, user)
            raw = kb.read_document_file(doc["knowledgeId"], did)
            if raw is None:
                missing.append(doc["originalName"])
                continue
            # 同名文档在压缩包里会互相覆盖，重名时补一个序号。
            entry = doc["originalName"] or did
            if entry in used:
                stem, dot, ext = entry.rpartition(".")
                base = stem if dot else entry
                entry = f"{base}({len(used)}){dot}{ext}" if dot else f"{entry}({len(used)})"
            used.add(entry)
            bundle.writestr(entry, raw)
        if missing:
            # 不静默少几个文件：把缺失清单一并放进压缩包。
            bundle.writestr(
                "未能导出的文档.txt",
                "以下文档没有留存原文（早于原文留存功能上线），请重新上传：\n"
                + "\n".join(missing),
            )
    if not used and missing:
        raise HTTPException(404, "所选文档都没有留存原文，请重新上传")
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers=_attachment_headers("知识库原始文档.zip"),
    )


# ---- 分段 ----

async def _require_chunk_edit(chunk_id: str, user: UserContext) -> dict[str, Any]:
    chunk = await chunk_service.get_chunk(chunk_id)
    if chunk is None:
        raise HTTPException(404, "分段不存在")
    await _require_edit(chunk["knowledgeId"], user)
    return chunk


def _chunk_error(exc: Exception) -> HTTPException:
    """向量侧失败给 502：不是用户请求有问题，而是嵌入服务 / Qdrant 那边没成，提示语要区分。"""
    if isinstance(exc, chunk_service.ChunkVectorError):
        return HTTPException(502, str(exc))
    return HTTPException(400, str(exc))


@router.get("/chunks")
async def list_chunks(
    knowledgeId: str = Query(...),
    documentId: Optional[str] = Query(None),
    keyword: str = Query(""),
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=chunk_service.MAX_PAGE_SIZE),
    user: UserContext = Depends(current_user),
):
    """分段列表。查询参数名沿用前端 getChunkList 传的 camelCase，不做翻译层。"""
    await _require_access(knowledgeId, user)
    return await chunk_service.list_chunks(
        knowledgeId, document_id=documentId, keyword=keyword, page=pageNo, page_size=pageSize,
    )


@router.put("/chunks/{chunk_id}")
async def update_chunk(
    chunk_id: str,
    body: dict = Body(...),
    user: UserContext = Depends(current_user),
):
    """改正文并重嵌入。前端还会带 contentWithImages / images（老 Java 的图文分段），
    agent-api 的分段没有图片存储，这两个字段忽略。"""
    await _require_chunk_edit(chunk_id, user)
    try:
        return await chunk_service.update_chunk(chunk_id, str(body.get("content") or ""))
    except (ValueError, chunk_service.ChunkVectorError) as exc:
        raise _chunk_error(exc) from exc


@router.post("/chunks/{chunk_id}/enabled")
async def set_chunk_enabled(
    chunk_id: str,
    body: dict = Body(...),
    user: UserContext = Depends(current_user),
):
    await _require_chunk_edit(chunk_id, user)
    try:
        return await chunk_service.set_chunk_enabled(chunk_id, bool(body.get("enabled", True)))
    except (ValueError, chunk_service.ChunkVectorError) as exc:
        raise _chunk_error(exc) from exc


@router.delete("/chunks/{chunk_id}")
async def delete_chunk(chunk_id: str, user: UserContext = Depends(current_user)):
    await _require_chunk_edit(chunk_id, user)
    try:
        return await chunk_service.delete_chunk(chunk_id)
    except (ValueError, chunk_service.ChunkVectorError) as exc:
        raise _chunk_error(exc) from exc


@router.post("/bases/{knowledge_id}/chunks/rebuild")
async def rebuild_chunks(knowledge_id: str, user: UserContext = Depends(current_user)):
    """从 Qdrant 回填 agent_knowledge_chunk（幂等）。

    回填函数 rebuild_chunk_rows 由「入库双写 + 老数据回填」那条线提供；它还没合进来
    时这个入口返回 501 说明原因，而不是在这里另写一份回填逻辑。
    """
    await _require_edit(knowledge_id, user)
    rebuild = getattr(kb, "rebuild_chunk_rows", None)
    if rebuild is None:
        raise HTTPException(501, "切片回填尚未部署：knowledge_base_service.rebuild_chunk_rows 不存在")
    try:
        count = await rebuild(knowledge_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"rebuilt": int(count or 0)}


@router.post("/retrieval")
async def retrieval(
    body: dict = Body(...),
    user: UserContext = Depends(current_user),
):
    """检索。页面上的「检索测试」也走这里：它想临时试不同的检索方式和权重，
    所以这些参数都可选传，不传就用知识库自己保存的设置。返回结构同时给出
    页面契约需要的 items / latencyMs（RetrievalResponse）和原始 chunks。"""
    ids = body.get("knowledgeIds") or body.get("knowledge_ids") or []
    for kid in ids:
        await _require_access(str(kid), user)

    def _num(key: str, alt: str):
        value = body.get(key, body.get(alt))
        return None if value in (None, "") else float(value)

    started = time.monotonic()
    query = str(body.get("query") or "")
    telemetry: dict[str, Any] = {}
    try:
        chunks = await kb.search_chunks(
            knowledge_ids=[str(x) for x in ids],
            query=query,
            top_k=int(body.get("topK") or body.get("top_k") or 5),
            score_threshold=_num("scoreThreshold", "score_threshold"),
            retrieval_mode=body.get("retrievalMode", body.get("retrieval_mode")) or None,
            semantic_weight=_num("semanticWeight", "semantic_weight"),
            keyword_weight=_num("keywordWeight", "keyword_weight"),
            telemetry=telemetry,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    latency_ms = round((time.monotonic() - started) * 1000)
    # 页面「召回测试」记 source=TEST：运营统计默认不计入它，但留痕能看出谁在调参
    await retrieval_log.record_retrieval(
        knowledge_ids=[str(x) for x in ids], hits=chunks, query=query,
        user_id=str(user.user_id), source="TEST", latency_ms=latency_ms,
        retrieval_mode=telemetry.get("retrieval_mode"), reranked=bool(telemetry.get("reranked")),
    )
    items = [{
        "knowledgeId": c.get("knowledgeId", ""),
        "documentId": c.get("documentId", ""),
        "chunkId": c.get("pointId") or f"{c.get('documentId')}#{c.get('chunkIndex')}",
        "documentName": c.get("source", ""),
        "content": c.get("content", ""),
        "sourceType": "DOCUMENT_CHUNK",
        "score": c.get("score"),
        "vectorScore": c.get("vectorScore"),
        "keywordScore": c.get("keywordScore"),
    } for c in chunks]
    return {
        "ok": True,
        "query": query,
        "latencyMs": latency_ms,
        "items": items,
        "chunks": chunks,
    }


# ---- 运营统计 ----

@router.get("/bases/{knowledge_id}/analytics")
async def base_analytics(
    knowledge_id: str,
    range_: Optional[str] = Query(
        None, alias="range", description="today / 7d / 30d，from/to 缺省时生效",
    ),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    sources: Optional[str] = Query(
        None, description="逗号分隔的来源过滤（CHAT/AGENT/WORKFLOW/TEST）；缺省不含 TEST",
    ),
    user: UserContext = Depends(current_user),
):
    """单个知识库的运营统计，输出前端 KnowledgeAnalyticsOverview 契约。

    原路径 /api/ai/knowledge/base/{id}/analytics 归已下线的 Java，面板一直报「统计迁移」。
    面板发 from / to（YYYY-MM-DD，按用户本地日历），range 只是给直接调接口的人的简写。
    权限与详情页一致（能看这个库就能看它的统计）；是否只对所有者显示由前端决定。
    """
    base = await _require_access(knowledge_id, user)
    date_from, date_to = retrieval_log.resolve_range(from_, to, range_)
    source_filter = (
        [s for s in (sources or "").split(",") if s.strip()]
        or list(retrieval_log.FORMAL_SOURCES)
    )
    return await retrieval_log.build_overview(
        base, date_from=date_from, date_to=date_to, sources=source_filter,
    )
