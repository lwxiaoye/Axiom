"""知识库：增删查、文档入库与向量检索。

原属 JeecgBoot(Java)；Java 下线后只剩 auth-api 里一个统一返回 503 的桩，知识库页面
空转、校园百事通因「至少绑定一个可用知识库」无法发布、RAG 完全不可用。现由 agent-api
自持——Qdrant 与 Embedding 配置本来就在这里。

切片存 Qdrant（集合按 模型+维度 命名，换模型不会读到旧向量），元信息存 MySQL。
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core.database import async_session
from app.models import KnowledgeAcl, KnowledgeBase, KnowledgeDocument
from app.services.knowledge import embedding_service
from app.services.knowledge.vector_service import _get_client

logger = logging.getLogger(__name__)

CHUNK_SIZE = 600
CHUNK_OVERLAP = 80
KB_INDEX_VERSION = 1

# 上传的原始文件按 知识库/文档 落盘留存。切片进 Qdrant 后原文本身还有用：
# 页面上的「下载原文」要它，文档处理失败后重传也要它。放 USER_FILES_DIR 下的
# 独立子目录（该目录已挂卷持久化），不塞进 MySQL——单个文件上限 5MB。
KB_FILES_SUBDIR = "knowledge"


def _base_dir(knowledge_id: str) -> Path:
    return Path(settings.USER_FILES_DIR) / KB_FILES_SUBDIR / str(knowledge_id)


def _document_path(knowledge_id: str, document_id: str) -> Path:
    return _base_dir(knowledge_id) / str(document_id)


def read_document_file(knowledge_id: str, document_id: str) -> Optional[bytes]:
    """取原始文件；上传早于「留存原文」这个改动的老文档会返回 None。"""
    path = _document_path(knowledge_id, document_id)
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        logger.exception("读取原始文档失败：%s", path)
        return None


def kb_collection_name(model_id: str, dimension: int) -> str:
    """集合名绑定 模型+维度：换模型后自然指向新集合，不会读到维度不匹配的旧向量。"""
    version = hashlib.sha256(f"{model_id}:{dimension}".encode()).hexdigest()[:12]
    return f"kb_v{KB_INDEX_VERSION}_{version}"


def _new_id() -> str:
    return uuid.uuid4().hex


def split_text(text: str) -> list[str]:
    """按段落聚合到 CHUNK_SIZE，段落本身超长再按字符切，相邻块保留重叠。"""
    normalized = (text or "").replace("\r\n", "\n").strip()
    if not normalized:
        return []
    paragraphs = [p.strip() for p in normalized.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [normalized]

    chunks: list[str] = []
    buffer = ""
    for para in paragraphs:
        while len(para) > CHUNK_SIZE:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.append(para[:CHUNK_SIZE])
            para = para[CHUNK_SIZE - CHUNK_OVERLAP:]
        if not buffer:
            buffer = para
        elif len(buffer) + len(para) + 2 <= CHUNK_SIZE:
            buffer = f"{buffer}\n\n{para}"
        else:
            chunks.append(buffer)
            buffer = para
    if buffer:
        chunks.append(buffer)
    return chunks


async def _active_embedding():
    config = await embedding_service.get_active_embedding_config()
    if config is None:
        raise ValueError("尚未配置向量模型，请先在管理配置中设置并测试通过")
    return config


async def ensure_kb_collection(collection: str, dimension: int) -> None:
    client = _get_client()
    existing = await client.collection_exists(collection)
    if not existing:
        await client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
        )


# ---- 知识库 ----

async def create_base(
    *, user_id: str, username: str, name: str, description: str = "", tenant_id: str = "0"
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("知识库名称不能为空")
    row = KnowledgeBase(
        id=_new_id(), tenant_id=str(tenant_id or "0"), name=name,
        description=(description or "").strip(), owner_user_id=str(user_id),
        owner_username=username or "", status="ENABLED",
    )
    async with async_session() as session:
        session.add(row)
        # 所有者显式落一条 OWNER 授权：校验要求至少存在一条可检索权限的 ACL
        session.add(KnowledgeAcl(
            id=_new_id(), knowledge_id=row.id,
            subject_type="user", subject_id=str(user_id), permission="OWNER",
        ))
        await session.commit()
    return await get_base(row.id)


async def permission_for(
    row: KnowledgeBase, *, user_id: str, is_admin: bool = False
) -> Optional[str]:
    """返回 user 对该库的权限；None 表示无权访问。

    所有者恒为 OWNER（不依赖 ACL 表里那条 OWNER 记录是否还在），管理员按
    OWNER 处理，其余看 ACL。
    """
    uid = str(user_id)
    if str(row.owner_user_id) == uid or is_admin:
        return "OWNER"
    async with async_session() as session:
        permission = (await session.execute(
            select(KnowledgeAcl.permission).where(
                KnowledgeAcl.knowledge_id == row.id,
                KnowledgeAcl.subject_type == "user",
                KnowledgeAcl.subject_id == uid,
            )
        )).scalars().first()
    return permission or None


async def get_base(
    knowledge_id: str, *, user_id: Optional[str] = None, is_admin: bool = False
) -> Optional[dict[str, Any]]:
    async with async_session() as session:
        row = (await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == str(knowledge_id))
        )).scalars().first()
    if row is None:
        return None
    permission = (
        await permission_for(row, user_id=user_id, is_admin=is_admin)
        if user_id is not None
        else None
    )
    return _serialize_base(row, permission=permission)


async def list_bases(
    *, user_id: str, scope: str = "owned", is_admin: bool = False
) -> list[dict[str, Any]]:
    async with async_session() as session:
        if scope == "shared":
            shared_ids = (await session.execute(
                select(KnowledgeAcl.knowledge_id).where(
                    KnowledgeAcl.subject_type == "user",
                    KnowledgeAcl.subject_id == str(user_id),
                    KnowledgeAcl.permission != "OWNER",
                )
            )).scalars().all()
            if not shared_ids:
                return []
            rows = (await session.execute(
                select(KnowledgeBase).where(KnowledgeBase.id.in_(list(shared_ids)))
            )).scalars().all()
        else:
            rows = (await session.execute(
                select(KnowledgeBase)
                .where(KnowledgeBase.owner_user_id == str(user_id))
                .order_by(KnowledgeBase.create_time.desc())
            )).scalars().all()
    return [
        _serialize_base(r, permission=await permission_for(r, user_id=user_id, is_admin=is_admin))
        for r in rows
    ]


async def delete_base(knowledge_id: str) -> None:
    kid = str(knowledge_id)
    config = await embedding_service.get_active_embedding_config()
    if config and config.dimension:
        collection = kb_collection_name(config.model, int(config.dimension))
        try:
            client = _get_client()
            if await client.collection_exists(collection):
                await client.delete(
                    collection_name=collection,
                    points_selector=Filter(must=[
                        FieldCondition(key="knowledge_id", match=MatchValue(value=kid))
                    ]),
                )
        except Exception:  # noqa: BLE001 - 向量清理失败不应阻塞元数据删除
            logger.exception("删除知识库向量失败：%s", kid)
    async with async_session() as session:
        await session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.knowledge_id == kid))
        await session.execute(delete(KnowledgeAcl).where(KnowledgeAcl.knowledge_id == kid))
        await session.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kid))
        await session.commit()
    try:
        shutil.rmtree(_base_dir(kid), ignore_errors=True)
    except OSError:
        logger.exception("删除知识库原始文档目录失败：%s", kid)


# 库内状态用 ENABLED/DISABLED（校园百事通的发布校验按这套取值判断），前端
# knowledge.types.ts 的契约是 ACTIVE/DISABLED。两套词汇都有既有依赖，所以在
# 序列化这一层翻译，不去改任何一边的取值。
_BASE_STATUS_TO_API = {"ENABLED": "ACTIVE", "DISABLED": "DISABLED"}

# 文档状态同理：库内是 PENDING/PROCESSING/COMPLETED/FAILED（校验里的
# DOC_WARNING_STATUSES 依赖它），前端有一套更细的处理阶段枚举。入库是同步的，
# 中间阶段不可见，因此 PROCESSING 统一映射到 EMBEDDING（文案「原文索引中」）。
_DOC_STATUS_TO_API = {
    "PENDING": "UPLOADED",
    "PROCESSING": "EMBEDDING",
    "COMPLETED": "READY",
    "FAILED": "FAILED",
}

DEFAULT_TOP_K = 5
DEFAULT_SCORE_THRESHOLD = 0.3


def _serialize_base(
    row: KnowledgeBase, *, permission: Optional[str] = None
) -> dict[str, Any]:
    """按 src/views/knowledge/knowledge.types.ts 的 KnowledgeBase 契约输出。

    字段名必须逐个对上，缺一个页面就静默降级：少了 currentPermission，
    getRecordPermission() 会回落到 VIEWER，所有者自己都看不到「设置」和
    「添加文档」；少了 documentCount，卡片恒显示「0 个文档」。
    """
    owner = row.owner_username or ""
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description or "",
        "status": _BASE_STATUS_TO_API.get(row.status, "DISABLED"),
        "tenantId": row.tenant_id,
        "ownerUserId": row.owner_user_id,
        "ownerUsername": owner,
        # 前端按 Jeecg 习惯读 createBy / createBy_dictText 显示「创建人」
        "createBy": owner,
        "createBy_dictText": owner,
        # 原样输出，不回落成 "VIEWER"：None 代表「无权访问」，接口层据此判 403。
        # 回落会让这个字段既表示「只读」又表示「没权限」，判定就没法做了。
        "currentPermission": permission,
        "chunkCount": int(row.chunk_count or 0),
        "documentCount": int(row.doc_count or 0),
        "retrievalMode": "VECTOR",
        "topK": int(row.top_k or DEFAULT_TOP_K),
        "scoreThreshold": float(row.score_threshold if row.score_threshold is not None else DEFAULT_SCORE_THRESHOLD),
        "chunkSize": CHUNK_SIZE,
        "chunkOverlap": CHUNK_OVERLAP,
        "embeddingModel": row.embedding_model or "",
        "embeddingDimension": row.embedding_dimension,
        "createTime": row.create_time.isoformat() if row.create_time else None,
        "updateTime": row.update_time.isoformat() if row.update_time else None,
    }


# ---- 文档 ----

def _serialize_document(row: KnowledgeDocument) -> dict[str, Any]:
    """按 knowledge.types.ts 的 KnowledgeDocument 契约输出。

    页面读 originalName / fileType / fileSize / progress / enabled，库里存的是
    name / content_type / size_bytes，名字对不上就整列显示空白。
    """
    status = _DOC_STATUS_TO_API.get(row.status, "FAILED")
    name = row.name or ""
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return {
        "id": row.id,
        "knowledgeId": row.knowledge_id,
        "name": name,
        "originalName": name,
        "fileType": suffix or "txt",
        "contentType": row.content_type or "text/plain",
        "fileSize": int(row.size_bytes or 0),
        "status": status,
        # 入库同步完成，没有真实进度可报：只有成/败两态，不画一条假的进度条。
        "progress": 100 if status == "READY" else 0,
        # 停用＝删掉向量（见 set_document_enabled），所以留在列表里的必然是启用的。
        "enabled": 1,
        "chunkCount": int(row.chunk_count or 0),
        "errorMessage": row.error_message or "",
        "createTime": row.create_time.isoformat() if row.create_time else None,
        "updateTime": row.update_time.isoformat() if row.update_time else None,
    }


async def list_documents(knowledge_id: str) -> list[dict[str, Any]]:
    async with async_session() as session:
        rows = (await session.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.knowledge_id == str(knowledge_id))
            .order_by(KnowledgeDocument.create_time.desc())
        )).scalars().all()
    return [_serialize_document(r) for r in rows]


async def add_document(
    *,
    knowledge_id: str,
    name: str,
    text: str,
    content_type: str = "text/plain",
    raw: Optional[bytes] = None,
) -> dict[str, Any]:
    """同步入库：切片 → 向量化 → 写 Qdrant → 回写计数。

    失败时把原因写进文档的 error_message 并置 FAILED，不静默吞掉——
    「用不了却无报错」是此前 RAG 的主要投诉来源。
    """
    kid = str(knowledge_id)
    base = await get_base(kid)
    if base is None:
        raise ValueError("知识库不存在")

    doc = KnowledgeDocument(
        id=_new_id(), knowledge_id=kid, name=(name or "未命名").strip(),
        content_type=content_type, size_bytes=len((text or "").encode("utf-8")),
        status="PROCESSING",
    )
    async with async_session() as session:
        session.add(doc)
        await session.commit()

    # 先落原文再向量化：入库失败时原文仍在，用户可以重新提交同一份文件。
    blob = raw if raw is not None else (text or "").encode("utf-8")
    path = _document_path(kid, doc.id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    except OSError:
        # 留存失败只影响「下载原文」，不该连带让入库失败。
        logger.exception("保存原始文档失败：%s", path)

    try:
        chunks = split_text(text)
        if not chunks:
            raise ValueError("文档内容为空，没有可索引的片段")
        config = await _active_embedding()
        vectors = await embedding_service.embed_texts(
            chunks, config=config, audit_purpose_detail="knowledge_ingest",
        )
        valid = [(c, v) for c, v in zip(chunks, vectors) if isinstance(v, list) and v]
        if not valid:
            raise ValueError("向量化全部失败，请检查向量模型配置")
        dimension = len(valid[0][1])
        collection = kb_collection_name(config.model, dimension)
        await ensure_kb_collection(collection, dimension)
        await _get_client().upsert(
            collection_name=collection,
            points=[
                PointStruct(
                    id=uuid.uuid4().hex,
                    vector=vector,
                    payload={
                        "knowledge_id": kid, "document_id": doc.id,
                        "document_name": doc.name, "content": chunk, "chunk_index": index,
                    },
                )
                for index, (chunk, vector) in enumerate(valid)
            ],
        )
        await _mark_document(doc.id, "COMPLETED", chunk_count=len(valid))
        await _refresh_base_counts(kid, config.model, dimension)
    except Exception as exc:  # noqa: BLE001
        await _mark_document(doc.id, "FAILED", error=str(exc)[:500])
        raise
    return await get_document(doc.id)


async def _mark_document(doc_id: str, status: str, *, chunk_count: int = 0, error: str = "") -> None:
    async with async_session() as session:
        row = (await session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id)
        )).scalars().first()
        if row is None:
            return
        row.status = status
        row.chunk_count = chunk_count
        row.error_message = error
        await session.commit()


async def _refresh_base_counts(knowledge_id: str, model: str, dimension: int) -> None:
    async with async_session() as session:
        total_chunks = (await session.execute(
            select(func.coalesce(func.sum(KnowledgeDocument.chunk_count), 0))
            .where(
                KnowledgeDocument.knowledge_id == knowledge_id,
                KnowledgeDocument.status == "COMPLETED",
            )
        )).scalar() or 0
        total_docs = (await session.execute(
            select(func.count(KnowledgeDocument.id))
            .where(KnowledgeDocument.knowledge_id == knowledge_id)
        )).scalar() or 0
        row = (await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == knowledge_id)
        )).scalars().first()
        if row is not None:
            row.chunk_count = int(total_chunks)
            row.doc_count = int(total_docs)
            row.embedding_model = model
            row.embedding_dimension = dimension
        await session.commit()


# ---- 检索 ----

async def search_chunks(
    *,
    knowledge_ids: list[str],
    query: str,
    top_k: Optional[int] = None,
    score_threshold: Optional[float] = None,
) -> list[dict[str, Any]]:
    """检索。top_k / 阈值未显式传入时，取知识库设置里保存的值。

    此前两者都是写死的默认值，于是「知识库设置」里改完保存、检索行为却纹丝不动。
    多库检索取各库中最宽松的一组，否则严格的那个库会把宽松库的结果一起砍掉。
    """
    ids = [str(x).strip() for x in (knowledge_ids or []) if str(x).strip()]
    if not ids or not (query or "").strip():
        return []
    if top_k is None or score_threshold is None:
        async with async_session() as session:
            rows = (await session.execute(
                select(KnowledgeBase.top_k, KnowledgeBase.score_threshold)
                .where(KnowledgeBase.id.in_(ids))
            )).all()
        if top_k is None:
            top_k = max([int(r[0] or DEFAULT_TOP_K) for r in rows], default=DEFAULT_TOP_K)
        if score_threshold is None:
            score_threshold = min(
                [float(r[1] if r[1] is not None else DEFAULT_SCORE_THRESHOLD) for r in rows],
                default=DEFAULT_SCORE_THRESHOLD,
            )
    config = await _active_embedding()
    vector = await embedding_service.embed_query(
        query, config=config, audit_purpose_detail="knowledge_search",
    )
    if not isinstance(vector, list) or not vector:
        raise ValueError("检索向量化失败")
    collection = kb_collection_name(config.model, len(vector))
    client = _get_client()
    if not await client.collection_exists(collection):
        return []
    hits = await client.search(
        collection_name=collection,
        query_vector=vector,
        limit=max(1, int(top_k)),
        query_filter=Filter(must=[
            FieldCondition(key="knowledge_id", match=MatchAny(any=ids))
        ]),
        score_threshold=float(score_threshold) or None,
        with_payload=True,
    )
    return [{
        "content": (h.payload or {}).get("content", ""),
        "source": (h.payload or {}).get("document_name", ""),
        "knowledgeId": (h.payload or {}).get("knowledge_id", ""),
        "documentId": (h.payload or {}).get("document_id", ""),
        "score": float(h.score),
    } for h in hits]


async def get_document(document_id: str) -> Optional[dict[str, Any]]:
    async with async_session() as session:
        row = (await session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == str(document_id))
        )).scalars().first()
    return _serialize_document(row) if row else None


async def set_document_enabled(document_id: str, enabled: bool) -> dict[str, Any]:
    """停用文档＝移除其向量但保留元信息；重新启用需要重新入库原文。"""
    doc = await get_document(document_id)
    if doc is None:
        raise ValueError("文档不存在")
    if enabled:
        raise ValueError("重新启用需重新上传原文：知识库只保存切片，未保留原始文件")
    await delete_documents(doc["knowledgeId"], [document_id])
    return {"id": document_id, "removed": True}


async def update_base(
    knowledge_id: str,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    top_k: Optional[int] = None,
    score_threshold: Optional[float] = None,
    user_id: Optional[str] = None,
    is_admin: bool = False,
) -> dict[str, Any]:
    async with async_session() as session:
        row = (await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == str(knowledge_id))
        )).scalars().first()
        if row is None:
            raise ValueError("知识库不存在")
        if name is not None:
            cleaned = name.strip()
            if not cleaned:
                raise ValueError("知识库名称不能为空")
            row.name = cleaned
        if description is not None:
            row.description = description.strip()
        # 抽屉里这两个控件本来就有取值范围（1–20 / 0–1），服务端同样夹一次：
        # 越界值会让检索要么恒空、要么把整库灌进上下文。
        if top_k is not None:
            row.top_k = max(1, min(20, int(top_k)))
        if score_threshold is not None:
            row.score_threshold = max(0.0, min(1.0, float(score_threshold)))
        await session.commit()
    return await get_base(knowledge_id, user_id=user_id, is_admin=is_admin)


async def set_base_enabled(
    knowledge_id: str, enabled: bool, *, user_id: Optional[str] = None, is_admin: bool = False
) -> dict[str, Any]:
    async with async_session() as session:
        row = (await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == str(knowledge_id))
        )).scalars().first()
        if row is None:
            raise ValueError("知识库不存在")
        row.status = "ENABLED" if enabled else "DISABLED"
        await session.commit()
    return await get_base(knowledge_id, user_id=user_id, is_admin=is_admin)


async def delete_documents(knowledge_id: str, document_ids: list[str]) -> int:
    """删文档同时清掉它的向量，否则检索仍会命中已删内容。"""
    kid = str(knowledge_id)
    ids = [str(x).strip() for x in (document_ids or []) if str(x).strip()]
    if not ids:
        return 0
    config = await embedding_service.get_active_embedding_config()
    if config and config.dimension:
        collection = kb_collection_name(config.model, int(config.dimension))
        try:
            client = _get_client()
            if await client.collection_exists(collection):
                await client.delete(
                    collection_name=collection,
                    points_selector=Filter(must=[
                        FieldCondition(key="knowledge_id", match=MatchValue(value=kid)),
                        FieldCondition(key="document_id", match=MatchAny(any=ids)),
                    ]),
                )
        except Exception:  # noqa: BLE001
            logger.exception("删除文档向量失败：%s", ids)
    async with async_session() as session:
        await session.execute(
            delete(KnowledgeDocument).where(
                KnowledgeDocument.knowledge_id == kid,
                KnowledgeDocument.id.in_(ids),
            )
        )
        await session.commit()
    for did in ids:
        try:
            _document_path(kid, did).unlink(missing_ok=True)
        except OSError:
            logger.exception("删除原始文档失败：%s/%s", kid, did)
    model = config.model if config else ""
    dimension = int(config.dimension) if config and config.dimension else 0
    await _refresh_base_counts(kid, model, dimension)
    return len(ids)


async def save_acl(knowledge_id: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """整表替换，但始终保留所有者的 OWNER——否则会把自己锁在外面。"""
    kid = str(knowledge_id)
    base = await get_base(kid)
    if base is None:
        raise ValueError("知识库不存在")
    owner = str(base["ownerUserId"])
    async with async_session() as session:
        await session.execute(delete(KnowledgeAcl).where(KnowledgeAcl.knowledge_id == kid))
        session.add(KnowledgeAcl(
            id=_new_id(), knowledge_id=kid,
            subject_type="user", subject_id=owner, permission="OWNER",
        ))
        for item in entries or []:
            subject_id = str(item.get("subjectId") or item.get("subject_id") or "").strip()
            if not subject_id or subject_id == owner:
                continue
            permission = str(item.get("permission") or "VIEWER").strip().upper()
            session.add(KnowledgeAcl(
                id=_new_id(), knowledge_id=kid,
                subject_type=str(item.get("subjectType") or item.get("subject_type") or "user"),
                subject_id=subject_id,
                permission=permission if permission in {"VIEWER", "EDITOR", "OWNER"} else "VIEWER",
            ))
        await session.commit()
    return await list_acl(kid)


async def accessible_ids(
    knowledge_ids: list[str], *, user_id: str, is_admin: bool = False
) -> list[str]:
    """过滤出用户有权检索的知识库，并剔除已停用的。

    对话侧检索必须自己做这层过滤：原先这道校验在 Java 检索接口里，Java 下线后
    如果直接把前端传来的 id 丢给向量库，就等于谁都能检索任何人的知识库。
    """
    ids = [str(x).strip() for x in (knowledge_ids or []) if str(x).strip()]
    if not ids:
        return []
    async with async_session() as session:
        rows = (await session.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id.in_(ids),
                KnowledgeBase.status == "ENABLED",
            )
        )).scalars().all()
    allowed: list[str] = []
    for row in rows:
        if await permission_for(row, user_id=user_id, is_admin=is_admin):
            allowed.append(row.id)
    # 保持调用方传入的顺序，便于日志比对
    order = {kid: i for i, kid in enumerate(ids)}
    return sorted(allowed, key=lambda kid: order.get(kid, 0))


async def list_acl(knowledge_id: str) -> list[dict[str, Any]]:
    async with async_session() as session:
        rows = (await session.execute(
            select(KnowledgeAcl).where(KnowledgeAcl.knowledge_id == str(knowledge_id))
        )).scalars().all()
    return [{
        "id": r.id, "knowledgeId": r.knowledge_id, "subjectType": r.subject_type,
        "subjectId": r.subject_id, "permission": r.permission,
    } for r in rows]
