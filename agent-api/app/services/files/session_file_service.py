"""大文件会话临时向量化（ADR-041 v1.18 修订形态）。

小文件仍走文本注入（document_parse_service）；**大文件**按用户 query 做内存向量检索
（分块 → embedding → 与 query 相似度 Top-K），只注入相关片段，避免整篇塞满上下文。
不落 Qdrant（无向量库清理负担）；未配置平台 embedding 时降级为截断。

多轮语义由 thread_attachment_service 承担（附件 Thread 级持久、每轮用新问题重检索）；
本模块对同一文件的分块向量按内容哈希做进程内 LRU 缓存，避免每轮全量重算 embedding。
"""
import hashlib
import logging
import math
import re
from collections import OrderedDict
from typing import List, Tuple

from app.services.knowledge import embedding_service

logger = logging.getLogger(__name__)

LARGE_THRESHOLD = 6000   # 超过此长度才做向量检索
CHUNK_SIZE = 600
CHUNK_OVERLAP = 80
TOP_K = 6
VISUAL_CONTEXT_CAP = 6000
_VISUAL_SECTION_RE = re.compile(r"【[^\n】]*中的图片内容（由视觉模型识别）】")

# 分块向量缓存：key = sha256(text)+model，value = (chunks, chunk_vecs)。
# 只缓存全部成功的结果，失败下一轮重算。
_EMB_CACHE: "OrderedDict[str, Tuple[List[str], List[List[float]]]]" = OrderedDict()
_EMB_CACHE_MAX = 32


def _cache_get(key: str):
    value = _EMB_CACHE.get(key)
    if value is not None:
        _EMB_CACHE.move_to_end(key)
    return value


def _cache_put(key: str, value) -> None:
    _EMB_CACHE[key] = value
    _EMB_CACHE.move_to_end(key)
    while len(_EMB_CACHE) > _EMB_CACHE_MAX:
        _EMB_CACHE.popitem(last=False)


def _chunk(text: str) -> List[str]:
    chunks: List[str] = []
    i = 0
    n = len(text)
    step = max(1, CHUNK_SIZE - CHUNK_OVERLAP)
    while i < n:
        chunks.append(text[i:i + CHUNK_SIZE])
        i += step
    return chunks


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else -1.0


async def retrieve_relevant(
    query: str,
    text: str,
    top_k: int = TOP_K,
    *,
    audit_context: dict | None = None,
) -> str:
    """大文件按 query 取相关片段；小文件/未配置 embedding 时原样或截断返回。"""
    if len(text) <= LARGE_THRESHOLD:
        return text

    # 内嵌图片描述位于文档末尾，单纯的头部截断/向量 Top-K 很容易把它丢掉。
    # 将它作为本轮附件的必备事实块优先保留，文字正文再按问题检索。
    visual = ""
    match = _VISUAL_SECTION_RE.search(text)
    if match:
        visual = text[match.start():match.start() + VISUAL_CONTEXT_CAP].strip()
        text = text[:match.start()].strip()
        if not text:
            return visual

    def with_visual(value: str) -> str:
        return f"{visual}\n\n{value}" if visual else value

    config = await embedding_service.get_active_embedding_config()
    if not config:
        return with_visual(text[:LARGE_THRESHOLD] + "\n...(文件超长且未配置向量检索，已截断)")
    cache_key = hashlib.sha256(text.encode("utf-8")).hexdigest() + ":" + str(getattr(config, "model", ""))
    cached = _cache_get(cache_key)
    audit = dict(audit_context or {})
    try:
        if cached is not None:
            chunks, chunk_vecs = cached
        else:
            chunks = _chunk(text)
            chunk_vecs = await embedding_service.embed_texts(
                chunks,
                config=config,
                return_exceptions=True,
                audit_run_id=str(audit.get("run_id") or ""),
                audit_thread_id=str(audit.get("thread_id") or ""),
                audit_root_run_id=str(audit.get("root_run_id") or ""),
                audit_parent_tool_call_id=str(audit.get("parent_tool_call_id") or ""),
                audit_purpose_detail="attachment_chunk_embedding",
            )
            if not any(isinstance(v, Exception) for v in chunk_vecs):
                _cache_put(cache_key, (chunks, chunk_vecs))
        q_vec = await embedding_service.embed_query(
            query,
            config=config,
            audit_run_id=str(audit.get("run_id") or ""),
            audit_thread_id=str(audit.get("thread_id") or ""),
            audit_root_run_id=str(audit.get("root_run_id") or ""),
            audit_parent_tool_call_id=str(audit.get("parent_tool_call_id") or ""),
            audit_purpose_detail="attachment_query_embedding",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("大文件向量检索失败，回退截断: %s", e)
        return with_visual(text[:LARGE_THRESHOLD])
    scored = []
    for chunk, vec in zip(chunks, chunk_vecs):
        if isinstance(vec, Exception):
            continue
        scored.append((_cosine(q_vec, vec), chunk))
    scored.sort(key=lambda s: -s[0])
    top = [c for _score, c in scored[:top_k]]
    if not top:
        return with_visual(text[:LARGE_THRESHOLD])
    return with_visual("（以下为该文件中与本轮问题最相关的片段）\n" + "\n---\n".join(top))
