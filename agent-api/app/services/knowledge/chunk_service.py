"""知识库分段的单条读写：MySQL 正本与 Qdrant 点位双写。

分段正本在 agent_knowledge_chunk（约定见 models.KnowledgeChunk 的 docstring），向量在
Qdrant，靠 point_id 连起来。页面「分段」面板的列表 / 编辑 / 停用 / 删除都走这里；
入库时的双写和老数据回填属于 knowledge_base_service（另一条线），这里不碰。

双写顺序统一为「先 Qdrant 后 MySQL」：检索读的是 Qdrant，先改它用户马上能在检索里
看到效果；Qdrant 一步失败就直接抛错、MySQL 原样不动，不会出现「正本改了、向量没改」
这种页面看着对、检索却对不上的假象。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from qdrant_client.models import PointIdsList, PointStruct
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session
from app.models import KnowledgeChunk, KnowledgeDocument
from app.services.knowledge import embedding_service
from app.services.knowledge import knowledge_base_service as kb
from app.services.knowledge.vector_service import _get_client

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 200

# 点不在当前集合里（换过向量模型后没重新入库）：只改 MySQL 会让页面显示「已保存 / 已停用」
# 而检索行为纹丝不动，三个写操作统一明确报错，不做半套。
_COLLECTION_MISSING = "当前向量集合不存在，该知识库需要重新入库后才能管理分段"


class ChunkVectorError(RuntimeError):
    """向量侧（嵌入 / Qdrant）失败。单独一类，接口层据此给 502 而不是 400。"""


def _serialize_chunk(row: KnowledgeChunk) -> dict[str, Any]:
    """按 src/views/knowledge/knowledge.types.ts 的 KnowledgeChunk 契约输出。

    页面读 chunkIndex / charCount / enabled（数字 1/0，开关按 `=== 1` 判），库里是
    chunk_index / char_count / SmallInteger；名字或类型对不上，列表就是一片空白或开关
    全灭——知识库卡片吃过一样的亏。
    """
    return {
        "id": row.id,
        "knowledgeId": row.knowledge_id,
        "documentId": row.document_id,
        "chunkIndex": int(row.chunk_index or 0),
        "content": row.content or "",
        "charCount": int(row.char_count or 0),
        "enabled": 1 if int(row.enabled or 0) else 0,
        "createTime": row.create_time.isoformat() if row.create_time else None,
        "updateTime": row.update_time.isoformat() if row.update_time else None,
    }


async def _load_row(session: AsyncSession, chunk_id: str) -> KnowledgeChunk:
    row = (await session.execute(
        select(KnowledgeChunk).where(KnowledgeChunk.id == str(chunk_id))
    )).scalars().first()
    if row is None:
        raise ValueError("分段不存在")
    return row


async def _active_config() -> embedding_service.EmbeddingConfig:
    config = await embedding_service.get_active_embedding_config()
    if config is None:
        raise ValueError("尚未配置向量模型，请先在管理配置中设置并测试通过")
    return config


def _collection_for(config: embedding_service.EmbeddingConfig, dimension: Optional[int] = None) -> str:
    """定位分段所在集合。

    集合按「模型+维度」命名，这里始终按当前启用的向量配置算——与 delete_documents /
    search_chunks 一致。换过模型的老库，其点位留在旧集合里，检索本来也读不到；
    三个写操作发现当前集合不存在时统一报 _COLLECTION_MISSING，而不是在新集合里
    孤零零写一个点。
    """
    dim = int(dimension or config.dimension or 0)
    if dim <= 0:
        raise ValueError("向量模型未记录维度，请先在管理配置中重新测试该模型")
    return kb.kb_collection_name(config.model, dim)


# ---- 读 ----

async def list_chunks(
    knowledge_id: str,
    *,
    document_id: Optional[str] = None,
    keyword: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """按 文档 → 文档内顺序 列出分段；keyword 走 LIKE，页面上翻翻找找够用。

    全文索引留给检索那条线（关键词/混合检索），这里不需要：分页列表本来就按
    document_id 索引缩过范围。
    """
    kid = str(knowledge_id)
    page = max(1, int(page or 1))
    page_size = max(1, min(MAX_PAGE_SIZE, int(page_size or 20)))
    conditions = [KnowledgeChunk.knowledge_id == kid]
    doc_id = str(document_id or "").strip()
    if doc_id:
        conditions.append(KnowledgeChunk.document_id == doc_id)
    kw = str(keyword or "").strip()
    if kw:
        # autoescape：用户搜「100%」时 % 得当字面量，而不是通配符
        conditions.append(KnowledgeChunk.content.contains(kw, autoescape=True))
    async with async_session() as session:
        total = (await session.execute(
            select(func.count(KnowledgeChunk.id)).where(*conditions)
        )).scalar() or 0
        rows = (await session.execute(
            select(KnowledgeChunk)
            .where(*conditions)
            .order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index, KnowledgeChunk.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )).scalars().all()
    return {
        "records": [_serialize_chunk(r) for r in rows],
        "total": int(total),
        # PageResult 契约还有这两个字段；前端目前只读 records/total，补上免得以后谁用到再空白
        "size": page_size,
        "current": page,
    }


async def get_chunk(chunk_id: str) -> Optional[dict[str, Any]]:
    async with async_session() as session:
        row = (await session.execute(
            select(KnowledgeChunk).where(KnowledgeChunk.id == str(chunk_id))
        )).scalars().first()
    return _serialize_chunk(row) if row else None


# ---- 写 ----

async def update_chunk(chunk_id: str, content: str) -> dict[str, Any]:
    """改正文：重嵌入 → upsert 同一 point_id（payload 沿用原键）→ 再改 MySQL。"""
    cleaned = (content or "").replace("\r\n", "\n").strip()
    if not cleaned:
        raise ValueError("分段内容不能为空")
    async with async_session() as session:
        row = await _load_row(session, chunk_id)
        doc_name = (await session.execute(
            select(KnowledgeDocument.name).where(KnowledgeDocument.id == row.document_id)
        )).scalar() or ""
        knowledge_id, document_id, chunk_index, point_id, was_enabled = (
            row.knowledge_id, row.document_id, int(row.chunk_index or 0), row.point_id, bool(row.enabled),
        )

    config = await _active_config()
    try:
        vectors = await embedding_service.embed_texts(
            [cleaned], config=config, audit_purpose_detail="knowledge_chunk_edit",
        )
    except Exception as exc:  # noqa: BLE001
        raise ChunkVectorError(f"分段重新向量化失败，内容未保存：{exc}") from exc
    vector = vectors[0] if vectors else None
    if not isinstance(vector, list) or not vector:
        raise ChunkVectorError("分段重新向量化失败（返回空向量），内容未保存")

    collection = _collection_for(config, len(vector))
    client = _get_client()
    # 沿用原点的 payload：入库时写的 document_name 等键、以及停用标记都在里面，
    # 只覆盖 content。集合里找不到原点（被外部清过）就按正本重建一份。
    payload: dict[str, Any] = {}
    try:
        if not await client.collection_exists(collection):
            raise ChunkVectorError(_COLLECTION_MISSING)
        existing = await client.retrieve(collection_name=collection, ids=[point_id], with_payload=True)
        if existing:
            payload = dict(existing[0].payload or {})
    except ChunkVectorError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ChunkVectorError(f"读取向量库失败，内容未保存：{exc}") from exc
    payload.setdefault("knowledge_id", knowledge_id)
    payload.setdefault("document_id", document_id)
    payload.setdefault("document_name", doc_name)
    payload.setdefault("chunk_index", chunk_index)
    if not was_enabled:
        payload["enabled"] = False
    payload["content"] = cleaned
    try:
        await client.upsert(
            collection_name=collection,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )
    except Exception as exc:  # noqa: BLE001
        raise ChunkVectorError(f"写入向量库失败，内容未保存：{exc}") from exc

    async with async_session() as session:
        row = await _load_row(session, chunk_id)
        row.content = cleaned
        row.char_count = len(cleaned)
        await session.commit()
        await session.refresh(row)
        return _serialize_chunk(row)


async def set_chunk_enabled(chunk_id: str, enabled: bool) -> dict[str, Any]:
    """停用 = Qdrant 点 payload 写 enabled=false + MySQL enabled=0；不删点，重开不用重嵌入。"""
    flag = bool(enabled)
    async with async_session() as session:
        row = await _load_row(session, chunk_id)
        point_id = row.point_id
    config = await _active_config()
    collection = _collection_for(config)
    client = _get_client()
    try:
        if not await client.collection_exists(collection):
            raise ChunkVectorError(_COLLECTION_MISSING)
        await client.set_payload(
            collection_name=collection, payload={"enabled": flag}, points=[point_id],
        )
    except ChunkVectorError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ChunkVectorError(f"更新向量库失败：{exc}") from exc
    async with async_session() as session:
        row = await _load_row(session, chunk_id)
        row.enabled = 1 if flag else 0
        await session.commit()
        await session.refresh(row)
        return _serialize_chunk(row)


async def delete_chunk(chunk_id: str) -> dict[str, Any]:
    """删点 + 删行 + 回写文档与知识库的计数。"""
    async with async_session() as session:
        row = await _load_row(session, chunk_id)
        knowledge_id, document_id, point_id = row.knowledge_id, row.document_id, row.point_id
    config = await _active_config()
    collection = _collection_for(config)
    client = _get_client()
    try:
        if await client.collection_exists(collection):
            await client.delete(
                collection_name=collection, points_selector=PointIdsList(points=[point_id]),
            )
        else:
            # 集合都没了说明这批向量早已不可检索，行照删；留痕方便排查计数为什么对不上。
            logger.warning("删除分段 %s 时向量集合 %s 不存在，仅删除正本", chunk_id, collection)
    except Exception as exc:  # noqa: BLE001
        raise ChunkVectorError(f"删除向量失败，分段未删除：{exc}") from exc
    async with async_session() as session:
        await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.id == str(chunk_id)))
        doc = (await session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
        )).scalars().first()
        if doc is not None:
            doc.chunk_count = max(0, int(doc.chunk_count or 0) - 1)
        await session.commit()
    # 知识库的 chunk_count 是各文档 chunk_count 之和，文档减完后同步一次
    await kb._refresh_base_counts(knowledge_id, config.model, int(config.dimension or 0))
    return {"id": str(chunk_id), "removed": True}


# ---- 入库前预览 ----

def preview_split(name: str, text: str) -> dict[str, Any]:
    """上传向导第三步的「预览分段」：用与入库完全相同的切法，让用户看到的就是入库后的样子。

    输出按 knowledge.types.ts 的 KnowledgeDocumentPreview：fileName / fileType /
    totalChunks / chunks[{content}]。
    """
    file_name = (name or "未命名").strip() or "未命名"
    suffix = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    chunks = kb.split_text(text)
    return {
        "fileName": file_name,
        "fileType": suffix or "txt",
        "totalChunks": len(chunks),
        "chunks": [{"content": c} for c in chunks],
    }
