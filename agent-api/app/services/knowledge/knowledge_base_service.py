"""知识库：增删查、文档入库与向量检索。

原属 JeecgBoot(Java)；Java 下线后只剩 auth-api 里一个统一返回 503 的桩，知识库页面
空转、校园百事通因「至少绑定一个可用知识库」无法发布、RAG 完全不可用。现由 agent-api
自持——Qdrant 与 Embedding 配置本来就在这里。

切片存 Qdrant（集合按 模型+维度 命名，换模型不会读到旧向量），元信息存 MySQL。
"""
from __future__ import annotations

import hashlib
import logging
import uuid
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

from app.core.database import async_session
from app.models import KnowledgeAcl, KnowledgeBase, KnowledgeDocument
from app.services.knowledge import embedding_service
from app.services.knowledge.vector_service import _get_client

logger = logging.getLogger(__name__)

CHUNK_SIZE = 600
CHUNK_OVERLAP = 80
KB_INDEX_VERSION = 1


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


async def get_base(knowledge_id: str) -> Optional[dict[str, Any]]:
    async with async_session() as session:
        row = (await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == str(knowledge_id))
        )).scalars().first()
    return _serialize_base(row) if row else None


async def list_bases(*, user_id: str, scope: str = "owned") -> list[dict[str, Any]]:
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
    return [_serialize_base(r) for r in rows]


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


def _serialize_base(row: KnowledgeBase) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description or "",
        "status": row.status,
        "tenantId": row.tenant_id,
        "ownerUserId": row.owner_user_id,
        "ownerUsername": row.owner_username or "",
        "chunkCount": int(row.chunk_count or 0),
        "docCount": int(row.doc_count or 0),
        "embeddingModel": row.embedding_model or "",
        "embeddingDimension": row.embedding_dimension,
        "createTime": row.create_time.isoformat() if row.create_time else None,
    }


# ---- 文档 ----

async def list_documents(knowledge_id: str) -> list[dict[str, Any]]:
    async with async_session() as session:
        rows = (await session.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.knowledge_id == str(knowledge_id))
            .order_by(KnowledgeDocument.create_time.desc())
        )).scalars().all()
    return [{
        "id": r.id, "knowledgeId": r.knowledge_id, "name": r.name,
        "status": r.status, "chunkCount": int(r.chunk_count or 0),
        "sizeBytes": int(r.size_bytes or 0), "errorMessage": r.error_message or "",
        "createTime": r.create_time.isoformat() if r.create_time else None,
    } for r in rows]


async def add_document(
    *, knowledge_id: str, name: str, text: str, content_type: str = "text/plain"
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
    return {"id": doc.id, "chunkCount": len(valid), "status": "COMPLETED"}


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
    *, knowledge_ids: list[str], query: str, top_k: int = 5
) -> list[dict[str, Any]]:
    ids = [str(x).strip() for x in (knowledge_ids or []) if str(x).strip()]
    if not ids or not (query or "").strip():
        return []
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
        with_payload=True,
    )
    return [{
        "content": (h.payload or {}).get("content", ""),
        "source": (h.payload or {}).get("document_name", ""),
        "knowledgeId": (h.payload or {}).get("knowledge_id", ""),
        "score": float(h.score),
    } for h in hits]


async def list_acl(knowledge_id: str) -> list[dict[str, Any]]:
    async with async_session() as session:
        rows = (await session.execute(
            select(KnowledgeAcl).where(KnowledgeAcl.knowledge_id == str(knowledge_id))
        )).scalars().all()
    return [{
        "id": r.id, "knowledgeId": r.knowledge_id, "subjectType": r.subject_type,
        "subjectId": r.subject_id, "permission": r.permission,
    } for r in rows]
