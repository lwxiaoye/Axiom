"""知识库：增删查、文档入库与向量检索。

原属 JeecgBoot(Java)；Java 下线后只剩 auth-api 里一个统一返回 503 的桩，知识库页面
空转、校园百事通因「至少绑定一个可用知识库」无法发布、RAG 完全不可用。现由 agent-api
自持——Qdrant 与 Embedding 配置本来就在这里。

切片双写：向量进 Qdrant（集合按 模型+维度 命名，换模型不会读到旧向量），正文进 MySQL
的 agent_knowledge_chunk（ngram 全文索引，供关键词/混合检索与分段管理），point_id 把两边
连起来；文档/知识库元信息存 MySQL。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
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
    PointIdsList,
    PointStruct,
    VectorParams,
)
from sqlalchemy import bindparam, delete, func, select, text

from app.core.config import settings
from app.core.database import async_session
from app.models import KnowledgeAcl, KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.services.knowledge import embedding_service, rerank_service
from app.services.knowledge.vector_service import _get_client

logger = logging.getLogger(__name__)

CHUNK_SIZE = 600
CHUNK_OVERLAP = 80
KB_INDEX_VERSION = 1

# 检索方式。VECTOR = 纯向量；KEYWORD = 纯 MySQL 全文；HYBRID = 两路召回后加权融合。
RETRIEVAL_MODES = ("VECTOR", "KEYWORD", "HYBRID")
DEFAULT_RETRIEVAL_MODE = "VECTOR"
DEFAULT_SEMANTIC_WEIGHT = 0.5
DEFAULT_KEYWORD_WEIGHT = 0.5
# 工作流节点、前端旧契约与 FastGPT 风格的取值各有一套拼写，统一收敛到 RETRIEVAL_MODES。
# 键是去掉空白/下划线/连字符再大写后的形式，见 normalize_retrieval_mode。
_RETRIEVAL_MODE_ALIASES = {
    "VECTOR": "VECTOR", "SEMANTIC": "VECTOR", "EMBEDDING": "VECTOR",
    "KEYWORD": "KEYWORD", "FULLTEXT": "KEYWORD", "FULLTEXTRECALL": "KEYWORD", "BM25": "KEYWORD",
    "HYBRID": "HYBRID", "MIXED": "HYBRID", "MIXEDRECALL": "HYBRID",
}
# Qdrant scroll 一页的点数：回填只读 payload 不读向量，一页 256 条既不会撑爆内存也不会太碎。
_SCROLL_PAGE_SIZE = 256


def normalize_retrieval_mode(value: Any, *, strict: bool = False) -> Optional[str]:
    """把各处拼写归一到 VECTOR / KEYWORD / HYBRID。

    空值返回 None（由调用方决定取知识库设置还是默认值）。不认识的值：strict 时抛
    ValueError（保存设置这种用户输入必须拒绝），否则 warning 后按 VECTOR——检索路径上
    宁可退化成能用的向量召回，也不能因为一个拼写把整次对话打挂，但要留痕。
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    key = re.sub(r"[\s_\-]", "", raw).upper()
    mode = _RETRIEVAL_MODE_ALIASES.get(key)
    if mode is None:
        if strict:
            raise ValueError(f"检索方式取值无效：{raw}（可选 VECTOR / KEYWORD / HYBRID）")
        logger.warning("检索方式取值不认识：%r，本次按 VECTOR 处理", raw)
        return DEFAULT_RETRIEVAL_MODE
    return mode


def _canonical_point_id(value: Any) -> str:
    """Qdrant 会把 uuid4().hex 这种无连字符的 id 规范成带连字符的形式再返回。

    MySQL 里 point_id 与 Qdrant 返回的 id 要能直接相等比较（回填去重、两路召回按
    point_id 合并），所以两边统一走这里：能解析成 UUID 的一律转成带连字符的规范写法，
    其它（例如整数 id）原样转字符串。
    """
    raw = str(value or "").strip()
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError):
        return raw


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

    所有者恒为 OWNER（不依赖 ACL 表里那条 OWNER 记录是否还在），其余看 ACL。
    **管理员没有旁路**：用户的知识库是隐私（2026-09-19 拍板，与技能同一口径），
    管理员只能看到自己建的和别人显式授权给他的。is_admin 形参保留只为兼容调用方，
    不再参与判定。
    """
    uid = str(user_id)
    if str(row.owner_user_id) == uid:
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
        # 切片正本与向量同生共死：留下来会让关键词检索继续命中已删库的内容
        await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.knowledge_id == kid))
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
# 重排是二次筛选：候选得比 top_k 宽，否则只是给同一批结果换个顺序。
RERANK_CANDIDATE_MIN = 20


def _candidate_limit(top_k: int) -> int:
    """粗召回候选数。重排与混合检索共用一个口径：混合两路各取这么多再融合，
    否则某一路只取 top_k 条，融合后能进前 top_k 的基本只剩交集。"""
    return max(int(top_k) * 4, RERANK_CANDIDATE_MIN)


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
        "retrievalMode": normalize_retrieval_mode(row.retrieval_mode) or DEFAULT_RETRIEVAL_MODE,
        "semanticWeight": float(
            row.semantic_weight if row.semantic_weight is not None else DEFAULT_SEMANTIC_WEIGHT
        ),
        "keywordWeight": float(
            row.keyword_weight if row.keyword_weight is not None else DEFAULT_KEYWORD_WEIGHT
        ),
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
    """同步入库：切片 → 向量化 → 写 Qdrant → 写 agent_knowledge_chunk → 回写计数。

    失败时把原因写进文档的 error_message 并置 FAILED，不静默吞掉——
    「用不了却无报错」是此前 RAG 的主要投诉来源。

    双写顺序是先 Qdrant 后 MySQL：反过来的话 Qdrant 失败会留下一批属于 FAILED 文档的
    正文行，关键词检索照样命中它们。MySQL 这步失败则回收刚写的点，两边不留半套。
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
        # 点 id 直接生成带连字符的规范 UUID：Qdrant 返回的就是这个形式，MySQL 存同一个字符串，
        # 两边比对不用再转换。
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "knowledge_id": kid, "document_id": doc.id,
                    "document_name": doc.name, "content": chunk, "chunk_index": index,
                    # 停用分段时改成 false；检索用 must_not enabled==false 过滤
                    "enabled": True,
                },
            )
            for index, (chunk, vector) in enumerate(valid)
        ]
        client = _get_client()
        await client.upsert(collection_name=collection, points=points)
        try:
            await _insert_chunk_rows([
                KnowledgeChunk(
                    id=_new_id(), knowledge_id=kid, document_id=doc.id,
                    chunk_index=index, content=chunk, char_count=len(chunk),
                    point_id=str(point.id), enabled=1,
                )
                for index, ((chunk, _vector), point) in enumerate(zip(valid, points))
            ])
        except Exception:
            # 正本没落下就把向量也撤掉，否则向量检索能命中、关键词检索查不到，分段页也是空的
            try:
                await client.delete(
                    collection_name=collection,
                    points_selector=PointIdsList(points=[point.id for point in points]),
                )
            except Exception:  # noqa: BLE001
                logger.exception("切片正本写入失败后回收向量也失败，文档 %s 可能残留向量", doc.id)
            raise
        await _mark_document(doc.id, "COMPLETED", chunk_count=len(valid))
        await _refresh_base_counts(kid, config.model, dimension)
    except Exception as exc:  # noqa: BLE001
        await _mark_document(doc.id, "FAILED", error=str(exc)[:500])
        raise
    return await get_document(doc.id)


async def _insert_chunk_rows(rows: list[KnowledgeChunk]) -> int:
    if not rows:
        return 0
    async with async_session() as session:
        session.add_all(rows)
        await session.commit()
    return len(rows)


async def rebuild_chunk_rows(knowledge_id: str) -> int:
    """老数据回填：把 Qdrant 里该库的全部点按 payload 写回 agent_knowledge_chunk，返回新写入条数。

    切片正本表晚于向量库出现，早先入库的文档只在 Qdrant 里有切片。幂等：MySQL 已有的
    point_id 跳过，重复调用不会翻倍。以 Qdrant 为准而不是重新切片原文——重切结果未必和
    当年一致，point_id 也对不上。payload 里 enabled=false 的点照样回填，但行也置为停用。
    """
    kid = str(knowledge_id)
    config = await embedding_service.get_active_embedding_config()
    if config is None or not config.dimension:
        raise ValueError("尚未配置向量模型，无法定位知识库所在的向量集合")
    collection = kb_collection_name(config.model, int(config.dimension))
    client = _get_client()
    if not await client.collection_exists(collection):
        logger.info("回填切片正本：向量集合 %s 不存在，知识库 %s 无可回填数据", collection, kid)
        return 0
    async with async_session() as session:
        existing = {
            _canonical_point_id(pid)
            for pid in (await session.execute(
                select(KnowledgeChunk.point_id).where(KnowledgeChunk.knowledge_id == kid)
            )).scalars().all()
        }
    written = 0
    offset = None
    while True:
        points, offset = await client.scroll(
            collection_name=collection,
            scroll_filter=Filter(must=[
                FieldCondition(key="knowledge_id", match=MatchValue(value=kid))
            ]),
            limit=_SCROLL_PAGE_SIZE,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        rows: list[KnowledgeChunk] = []
        for point in points:
            pid = _canonical_point_id(point.id)
            payload = point.payload or {}
            content = str(payload.get("content") or "")
            if pid in existing or not content:
                continue
            existing.add(pid)
            rows.append(KnowledgeChunk(
                id=_new_id(), knowledge_id=kid,
                document_id=str(payload.get("document_id") or ""),
                chunk_index=int(payload.get("chunk_index") or 0),
                content=content, char_count=len(content), point_id=pid,
                enabled=0 if payload.get("enabled") is False else 1,
            ))
        written += await _insert_chunk_rows(rows)
        if offset is None:
            break
    logger.info("回填切片正本：知识库 %s 新写入 %d 行", kid, written)
    return written


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
    """回写知识库的切片数/文档数。

    切片数以 agent_knowledge_chunk 为准（分段级增删改只动这张表，不用再逐个文档改计数）；
    该库一行都没有时退回按文档 chunk_count 求和——那是还没跑 rebuild_chunk_rows 的老数据，
    此时算成 0 会让校园百事通的「至少一个可用知识库」校验把它判成空库。
    """
    async with async_session() as session:
        total_chunks = (await session.execute(
            select(func.count(KnowledgeChunk.id))
            .where(KnowledgeChunk.knowledge_id == knowledge_id)
        )).scalar() or 0
        if not total_chunks:
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

async def _load_base_settings(ids: list[str]) -> dict[str, Any]:
    """多库检索时合并各库保存的检索设置。

    top_k 取最大、阈值取最小（宽松者胜，否则严格的库把宽松库的结果一起砍掉）；检索方式
    各库一致就用它，不一致取 HYBRID（两路都跑才覆盖得住每个库的意图）；权重取平均。
    没查到任何行（库不存在）时全部回落默认值。
    """
    async with async_session() as session:
        rows = (await session.execute(
            select(
                KnowledgeBase.top_k, KnowledgeBase.score_threshold, KnowledgeBase.retrieval_mode,
                KnowledgeBase.semantic_weight, KnowledgeBase.keyword_weight,
            ).where(KnowledgeBase.id.in_(ids))
        )).all()
    modes = {normalize_retrieval_mode(r[2]) or DEFAULT_RETRIEVAL_MODE for r in rows}
    if not modes:
        mode = DEFAULT_RETRIEVAL_MODE
    elif len(modes) == 1:
        mode = modes.pop()
    else:
        mode = "HYBRID"
    semantic = [float(r[3]) for r in rows if r[3] is not None]
    keyword = [float(r[4]) for r in rows if r[4] is not None]
    return {
        "top_k": max([int(r[0] or DEFAULT_TOP_K) for r in rows], default=DEFAULT_TOP_K),
        "score_threshold": min(
            [float(r[1] if r[1] is not None else DEFAULT_SCORE_THRESHOLD) for r in rows],
            default=DEFAULT_SCORE_THRESHOLD,
        ),
        "retrieval_mode": mode,
        "semantic_weight": sum(semantic) / len(semantic) if semantic else DEFAULT_SEMANTIC_WEIGHT,
        "keyword_weight": sum(keyword) / len(keyword) if keyword else DEFAULT_KEYWORD_WEIGHT,
    }


def resolve_weights(
    semantic_weight: Optional[float], keyword_weight: Optional[float]
) -> tuple[float, float]:
    """混合检索两路权重。都缺省 0.5/0.5；只给一个则另一个取 1−它；都给了各自夹到 0–1，
    再按比例缩放到和为 1——融合分是两路归一分的加权和，权重和为 1 时它才落在 0–1，
    前端只放一个滑块也正是为了保证这一点，这里对直接调接口的调用方补同样的约束。
    """
    if semantic_weight is None and keyword_weight is None:
        return DEFAULT_SEMANTIC_WEIGHT, DEFAULT_KEYWORD_WEIGHT
    if semantic_weight is None:
        kw = max(0.0, min(1.0, float(keyword_weight)))
        return 1.0 - kw, kw
    if keyword_weight is None:
        sw = max(0.0, min(1.0, float(semantic_weight)))
        return sw, 1.0 - sw
    sw = max(0.0, min(1.0, float(semantic_weight)))
    kw = max(0.0, min(1.0, float(keyword_weight)))
    total = sw + kw
    if total <= 0:
        logger.warning("混合检索两路权重都是 0，本次按 0.5/0.5 融合")
        return DEFAULT_SEMANTIC_WEIGHT, DEFAULT_KEYWORD_WEIGHT
    return sw / total, kw / total


def _hit_from_payload(payload: dict[str, Any], point_id: Any) -> dict[str, Any]:
    return {
        "content": payload.get("content", ""),
        "source": payload.get("document_name", ""),
        "knowledgeId": payload.get("knowledge_id", ""),
        "documentId": payload.get("document_id", ""),
        "chunkIndex": payload.get("chunk_index"),
        "pointId": _canonical_point_id(point_id) if point_id is not None else "",
    }


async def _vector_search(
    ids: list[str], query: str, limit: int, score_threshold: float,
) -> list[dict[str, Any]]:
    """Qdrant 向量召回。score / vectorScore 都是余弦相似度；score_threshold 在这里生效。

    停用的分段在 payload 上标 enabled=false，用 must_not 排除：老点没有这个键，
    must_not 对缺键不成立，照常命中——用 must enabled==true 就会把老点全部漏掉。
    """
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
        limit=limit,
        query_filter=Filter(
            must=[FieldCondition(key="knowledge_id", match=MatchAny(any=ids))],
            must_not=[FieldCondition(key="enabled", match=MatchValue(value=False))],
        ),
        score_threshold=float(score_threshold) or None,
        with_payload=True,
    )
    results = []
    for h in hits:
        item = _hit_from_payload(h.payload or {}, getattr(h, "id", None))
        item["vectorScore"] = float(h.score)
        item["keywordScore"] = None
        item["score"] = float(h.score)
        results.append(item)
    return results


# MySQL 自然语言全文检索。MATCH 出现两次是有意的：WHERE 里那次借全文索引筛掉相关度为 0 的行，
# SELECT 里那次拿分数排序；优化器会把它们识别成同一次求值，不会多算。document_name 不在
# 切片表上，LEFT JOIN 文档表补——文档行没了（不该发生）也让切片带空名回来，别把结果吞掉。
_KEYWORD_SQL = text(
    "SELECT c.point_id, c.knowledge_id, c.document_id, c.chunk_index, c.content, "
    "       d.name AS document_name, "
    "       MATCH(c.content) AGAINST(:q IN NATURAL LANGUAGE MODE) AS relevance "
    "FROM agent_knowledge_chunk c "
    "LEFT JOIN agent_knowledge_document d ON d.id = c.document_id "
    "WHERE c.knowledge_id IN :ids AND c.enabled = 1 "
    "  AND MATCH(c.content) AGAINST(:q IN NATURAL LANGUAGE MODE) "
    "ORDER BY relevance DESC, c.document_id, c.chunk_index "
    "LIMIT :limit"
).bindparams(bindparam("ids", expanding=True))


async def _keyword_search(ids: list[str], query: str, limit: int) -> list[dict[str, Any]]:
    """MySQL ngram 全文召回。score / keywordScore 是 MATCH…AGAINST 的相关度。

    量纲说明：InnoDB 的相关度是 TF-IDF 风格的非负实数，没有上界、随语料变化，和余弦
    相似度（0–1）完全不可比，所以 score_threshold 不作用于这一路；混合检索也必须先各自
    归一再融合。查询词同样会被 ngram 切成 2-gram（"借书期限" → 借书/书期/期限），单字查询
    切不出 token、必然空结果——空结果不是错误。
    """
    async with async_session() as session:
        rows = (await session.execute(
            _KEYWORD_SQL, {"q": query, "ids": ids, "limit": int(limit)},
        )).mappings().all()
    results = []
    for r in rows:
        item = _hit_from_payload({
            "content": r["content"], "document_name": r["document_name"] or "",
            "knowledge_id": r["knowledge_id"], "document_id": r["document_id"],
            "chunk_index": r["chunk_index"],
        }, r["point_id"])
        score = float(r["relevance"] or 0.0)
        item["vectorScore"] = None
        item["keywordScore"] = score
        item["score"] = score
        results.append(item)
    return results


def _min_max(values: list[float]) -> list[float]:
    """min-max 归一到 0–1。只有一条或全部相等时给 1.0：那是这一路的最佳命中，不能因为
    没有参照物就算成 0，否则单命中的一路在融合里毫无贡献。"""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo <= 1e-12:
        return [1.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def fuse_hits(
    vector_hits: list[dict[str, Any]], keyword_hits: list[dict[str, Any]],
    semantic_weight: float, keyword_weight: float,
) -> list[dict[str, Any]]:
    """混合检索融合：两路分数各自 min-max 归一，按权重加权求和，按 point_id 去重。

    为什么不直接加原始分：余弦相似度在 0–1，MySQL 相关度无上界，直接相加等于只看关键词。
    归一是相对本次候选集的——同一条切片在不同查询里的融合分不可横向比较，这也是
    score_threshold 不卡融合分的原因。只在一路出现的切片，另一路按 0 计。
    """
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for hits, key in ((vector_hits, "vectorScore"), (keyword_hits, "keywordScore")):
        normalized = _min_max([float(h[key]) for h in hits])
        for hit, norm in zip(hits, normalized):
            pid = hit.get("pointId") or f"{hit.get('documentId')}#{hit.get('chunkIndex')}"
            item = merged.get(pid)
            if item is None:
                item = dict(hit)
                item["vectorScore"] = None
                item["keywordScore"] = None
                item["_vector_norm"] = 0.0
                item["_keyword_norm"] = 0.0
                merged[pid] = item
                order.append(pid)
            item[key] = hit[key]
            item["_vector_norm" if key == "vectorScore" else "_keyword_norm"] = norm
    fused = []
    for pid in order:
        item = merged[pid]
        item["score"] = (
            semantic_weight * item.pop("_vector_norm") + keyword_weight * item.pop("_keyword_norm")
        )
        fused.append(item)
    fused.sort(key=lambda x: x["score"], reverse=True)
    return fused


async def search_chunks(
    *,
    knowledge_ids: list[str],
    query: str,
    top_k: Optional[int] = None,
    score_threshold: Optional[float] = None,
    rerank: Optional[bool] = None,
    retrieval_mode: Optional[str] = None,
    semantic_weight: Optional[float] = None,
    keyword_weight: Optional[float] = None,
    telemetry: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """检索。top_k / 阈值 / 检索方式 / 权重未显式传入时，取知识库设置里保存的值。

    telemetry：调用方传一个 dict 进来，返回前填入本次实际生效的 retrieval_mode 与 reranked，
    供检索日志记录。放在这里而不是让调用方自己再算一遍：检索方式与重排配置的解析
    （库设置回落、拼写归一化、重排开关）都在本函数内部，外面重算既多两次查询又会漂移。
    不传则一切如旧。

    此前两者都是写死的默认值，于是「知识库设置」里改完保存、检索行为却纹丝不动。
    多库检索的合并规则见 _load_base_settings。

    retrieval_mode：VECTOR 纯向量；KEYWORD 纯 MySQL 全文，不走向量、不受 score_threshold
    约束（阈值是余弦相似度的量纲，套在全文相关度上没有意义）；HYBRID 两路各取
    _candidate_limit(top_k) 条候选，见 fuse_hits 融合后再截 top_k。取值拼写在入口归一化，
    不认识的按 VECTOR 并 warning。

    rerank：None = 配置并启用了重排模型就用；True = 要求重排（未配置则记日志、按召回序）；
    False = 不重排（工作流节点没勾重排时传的就是它）。启用时先多取候选、重排、再截到 top_k。

    每条结果：`score` 是最终排序依据（重排后就是重排分，混合模式下是融合分，否则等于本路
    召回分）；`vectorScore` 是余弦相似度、`keywordScore` 是 MySQL 全文相关度，没走那一路的
    为 None。三者量纲互不相同，只有 vectorScore 能和 score_threshold 比。
    """
    ids = [str(x).strip() for x in (knowledge_ids or []) if str(x).strip()]
    if not ids or not (query or "").strip():
        return []
    mode = normalize_retrieval_mode(retrieval_mode)
    if top_k is None or score_threshold is None or mode is None \
            or (mode == "HYBRID" and semantic_weight is None and keyword_weight is None):
        saved = await _load_base_settings(ids)
        if top_k is None:
            top_k = saved["top_k"]
        if score_threshold is None:
            score_threshold = saved["score_threshold"]
        if mode is None:
            mode = saved["retrieval_mode"]
        if mode == "HYBRID" and semantic_weight is None and keyword_weight is None:
            semantic_weight, keyword_weight = saved["semantic_weight"], saved["keyword_weight"]
    rerank_config = None
    if rerank is not False:
        rerank_config = await rerank_service.get_active_rerank_config()
        if rerank and rerank_config is None:
            logger.info("检索方要求重排，但重排模型未配置或未启用，按召回顺序返回")
    k = max(1, int(top_k))
    # 混合检索与重排都需要比 top_k 宽的候选；纯向量/纯关键词且不重排时取 top_k 就够
    limit = _candidate_limit(k) if (rerank_config or mode == "HYBRID") else k

    if mode == "KEYWORD":
        results = await _keyword_search(ids, query, limit)
    elif mode == "HYBRID":
        sw, kw = resolve_weights(semantic_weight, keyword_weight)
        vector_hits, keyword_hits = await asyncio.gather(
            _vector_search(ids, query, limit, float(score_threshold)),
            _keyword_search(ids, query, limit),
        )
        results = fuse_hits(vector_hits, keyword_hits, sw, kw)
    else:
        results = await _vector_search(ids, query, limit, float(score_threshold))
    if telemetry is not None:
        # reranked 反映「重排配置生效且有候选送去重排」；重排调用失败退回召回序的情形
        # 在 _rerank_hits 里只 warning，这里不区分——它是配置维度的事实，不是成功率
        telemetry["retrieval_mode"] = mode
        telemetry["reranked"] = bool(rerank_config is not None and results)
    if rerank_config is None or not results:
        return results[:k]
    return await _rerank_hits(query, results, k, rerank_config)


async def _rerank_hits(
    query: str, results: list[dict[str, Any]], top_k: int, config: rerank_service.RerankConfig,
) -> list[dict[str, Any]]:
    """召回候选 -> 重排 -> 截 top_k。重排失败退回召回排序，但 warning 留痕，不能静默降级。

    vectorScore / keywordScore 原样保留，只覆盖 score——调用方仍能看到这条是怎么被召回的。
    """
    candidates = [r for r in results if str(r.get("content") or "").strip()]
    if not candidates:
        return results[:top_k]
    try:
        ranked = await rerank_service.rerank(
            query, [r["content"] for r in candidates], top_n=top_k, config=config,
            audit_purpose_detail="knowledge_rerank",
        )
    except rerank_service.RerankError as exc:
        logger.warning("知识库重排失败，本次退回召回排序：%s", exc)
        return results[:top_k]
    reranked = []
    for index, score in ranked[:top_k]:
        item = dict(candidates[index])
        item["score"] = score
        reranked.append(item)
    return reranked


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
    retrieval_mode: Optional[str] = None,
    semantic_weight: Optional[float] = None,
    keyword_weight: Optional[float] = None,
    user_id: Optional[str] = None,
    is_admin: bool = False,
) -> dict[str, Any]:
    # 先校验再开事务：取值无效直接 400，不留半截更新
    mode = normalize_retrieval_mode(retrieval_mode, strict=True)
    weights: Optional[tuple[float, float]] = None
    if semantic_weight is not None or keyword_weight is not None:
        for label, value in (("语义权重", semantic_weight), ("关键词权重", keyword_weight)):
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{label}必须是 0–1 之间的数字") from exc
            if not 0.0 <= number <= 1.0:
                raise ValueError(f"{label}必须在 0–1 之间")
        # 只给一个时另一个取 1−它（前端只有一个滑块）；两个都给则按比例缩到和为 1 再存，
        # 存下来的永远是检索时真正生效的那对值，页面回显不会和实际行为对不上
        weights = resolve_weights(semantic_weight, keyword_weight)
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
        if mode is not None:
            row.retrieval_mode = mode
        if weights is not None:
            row.semantic_weight, row.keyword_weight = weights
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
        # 切片正本随文档一起删：留着会让关键词检索继续命中已删文档
        await session.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.knowledge_id == kid,
                KnowledgeChunk.document_id.in_(ids),
            )
        )
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
