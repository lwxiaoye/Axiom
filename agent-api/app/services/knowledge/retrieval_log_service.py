"""知识库检索日志与「运营统计」聚合。

写入侧：record_retrieval 由两处调用——路由 POST /knowledge/retrieval（页面召回测试，
source=TEST）与 chat/tools/knowledge.retrieve_knowledge（对话 / 智能体 / 工作流）。写入失败
只 warning，绝不影响检索本身。

读取侧：build_overview 按 src/views/knowledge/knowledge.types.ts 的 KnowledgeAnalyticsOverview
契约输出。聚合在 Python 里做而不是写 SQL 的 JSON 函数：一个时间窗内的日志行数是「每次检索
一行」的量级（月级几千行），一次 select 装进内存比在 MySQL 里拆 JSON 数组简单得多，也不绑
方言。区间上限 MAX_RANGE_DAYS，防止一次拉全表。

指标定义（面板文案已经写死了口径，这里照它实现）：
- retrievalCount 召回次数：日志行数，即「真实检索执行次数」（多库检索按库各计一次）。
- qaCount 知识问答量：按 turn_id 去重；没有 turn_id 的行（召回测试）按行计。
- fileRetrievalCount 文件召回：每行 hit_document_ids 去重后的文档数之和（同次召回同一文件只计一次）。
- chunkHitCount 分片命中：每行 hit_count 之和（最终返回的切片数）。
- noHitCount / noHitRate：hit_count == 0 的行数 / 召回次数；命中率 = 1 − noHitRate。
- averageLatencyMs：latency_ms 平均值（四舍五入）。
默认只统计 FORMAL_SOURCES（CHAT / AGENT / WORKFLOW），面板上「手工召回测试不会计入这里的
数据」说的就是 TEST 被排除；调用方可显式传 sources 把它算进来。
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy import select

from app.core.database import async_session
from app.models import KnowledgeDocument, KnowledgeRetrievalLog
from app.services.agent_time import agent_timezone

logger = logging.getLogger(__name__)

SOURCES = ("CHAT", "TEST", "AGENT", "WORKFLOW")
FORMAL_SOURCES = ("CHAT", "AGENT", "WORKFLOW")
QUERY_MAX_LEN = 512
MAX_RANGE_DAYS = 366
DEFAULT_RANGE_DAYS = 30
RANKING_LIMIT = 10
TOP_QUERY_LIMIT = 10

_RANGE_RE = re.compile(r"^(\d{1,3})d$", re.IGNORECASE)


def _now_local() -> datetime:
    """平台业务时区的「现在」（naive）。见 KnowledgeRetrievalLog.create_time 的注释。"""
    return datetime.now(agent_timezone()).replace(tzinfo=None)


def normalize_source(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in SOURCES:
        return text
    # 调用方只会传这四个值；真出现别的拼写按 CHAT 归类并留痕，不让一条日志因此丢掉
    if text:
        logger.warning("未知的检索来源 %r，按 CHAT 记录", value)
    return "CHAT"


async def record_retrieval(
    *,
    knowledge_ids: Iterable[str],
    hits: list[dict[str, Any]],
    query: str,
    user_id: Optional[str],
    source: str,
    latency_ms: float,
    retrieval_mode: Optional[str],
    reranked: bool,
    turn_id: Optional[str] = None,
) -> int:
    """每个参与检索的知识库各记一行，返回写入行数；任何失败只 warning 并返回 0。

    hits 是 search_chunks 的返回（每条带 knowledgeId / documentId / score），按 knowledgeId
    分到各库；不在 knowledge_ids 里的命中（理论上不会有）忽略。无命中的库照样记一行
    hit_count=0——「无命中率」就是靠这些行算出来的。
    """
    try:
        ids: list[str] = []
        for kid in knowledge_ids or []:
            text = str(kid or "").strip()
            if text and text not in ids:
                ids.append(text)
        if not ids:
            return 0
        per_base: dict[str, list[dict[str, Any]]] = {kid: [] for kid in ids}
        for hit in hits or []:
            if not isinstance(hit, dict):
                continue
            kid = str(hit.get("knowledgeId") or "").strip()
            if kid in per_base:
                per_base[kid].append(hit)
        now = _now_local()
        source_value = normalize_source(source)
        mode = str(retrieval_mode or "VECTOR").strip().upper()[:16] or "VECTOR"
        turn = str(turn_id or "").strip()[:64] or None
        rows = []
        for kid in ids:
            base_hits = per_base[kid]
            scores = [
                float(h["score"]) for h in base_hits
                if h.get("score") is not None
            ]
            rows.append(KnowledgeRetrievalLog(
                id=uuid.uuid4().hex,
                knowledge_id=kid,
                user_id=str(user_id or "").strip()[:64],
                source=source_value,
                query=str(query or "").strip()[:QUERY_MAX_LEN],
                hit_count=len(base_hits),
                top_score=max(scores) if scores else None,
                latency_ms=max(0, int(round(float(latency_ms or 0)))),
                retrieval_mode=mode,
                reranked=bool(reranked),
                hit_document_ids=json.dumps(
                    [str(h.get("documentId") or "") for h in base_hits], ensure_ascii=False,
                ),
                turn_id=turn,
                create_time=now,
            ))
        async with async_session() as session:
            session.add_all(rows)
            await session.commit()
        return len(rows)
    except Exception as exc:  # noqa: BLE001 —— 日志是旁路，任何异常都不能冒泡到检索
        logger.warning("知识库检索日志写入失败（不影响检索结果）：%s", exc)
        return 0


def _parse_date(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def resolve_range(
    date_from: Any = None, date_to: Any = None, range_key: Any = None,
) -> tuple[date, date]:
    """把前端的 from / to（YYYY-MM-DD）或 range（today / 7d / 30d）解析成闭区间。

    面板发的是 from / to；range 是给直接调接口的人用的简写。两者都没有就近 30 天。
    区间倒置时交换；超过 MAX_RANGE_DAYS 时只保留靠近 to 的那一段，不报错——统计页多拉
    几天不该变成 400。
    """
    today = _now_local().date()
    start = _parse_date(date_from)
    end = _parse_date(date_to)
    if start is None or end is None:
        key = str(range_key or "").strip().lower()
        if key == "today":
            days = 1
        else:
            matched = _RANGE_RE.match(key)
            days = int(matched.group(1)) if matched else DEFAULT_RANGE_DAYS
        days = max(1, days)
        end = end or today
        start = start or (end - timedelta(days=days - 1))
    if start > end:
        start, end = end, start
    if (end - start).days >= MAX_RANGE_DAYS:
        start = end - timedelta(days=MAX_RANGE_DAYS - 1)
    return start, end


def _decode_document_ids(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(x) for x in data if str(x or "").strip()] if isinstance(data, list) else []


def _empty_ranking(item_id: str, name: str, knowledge_id: str) -> dict[str, Any]:
    return {
        "id": item_id, "knowledgeId": knowledge_id, "name": name,
        "qaCount": 0, "retrievalCount": 0, "fileRetrievalCount": 0,
        "chunkHitCount": 0, "noHitCount": 0, "noHitRate": 0.0,
    }


async def _document_names(document_ids: Iterable[str]) -> dict[str, str]:
    ids = [d for d in set(document_ids) if d]
    if not ids:
        return {}
    async with async_session() as session:
        rows = (await session.execute(
            select(KnowledgeDocument.id, KnowledgeDocument.name)
            .where(KnowledgeDocument.id.in_(ids))
        )).all()
    return {str(r[0]): str(r[1] or "") for r in rows}


async def load_logs(
    knowledge_id: str, *, date_from: date, date_to: date, sources: Iterable[str] = FORMAL_SOURCES,
) -> list[KnowledgeRetrievalLog]:
    """取一个知识库在闭区间 [date_from, date_to] 内的日志行，按时间升序。"""
    start = datetime.combine(date_from, datetime.min.time())
    end = datetime.combine(date_to + timedelta(days=1), datetime.min.time())
    source_values = [normalize_source(s) for s in sources] or list(FORMAL_SOURCES)
    async with async_session() as session:
        return list((await session.execute(
            select(KnowledgeRetrievalLog)
            .where(
                KnowledgeRetrievalLog.knowledge_id == str(knowledge_id),
                KnowledgeRetrievalLog.create_time >= start,
                KnowledgeRetrievalLog.create_time < end,
                KnowledgeRetrievalLog.source.in_(source_values),
            )
            .order_by(KnowledgeRetrievalLog.create_time.asc())
        )).scalars().all())


async def build_overview(
    base: dict[str, Any],
    *,
    date_from: date,
    date_to: date,
    sources: Iterable[str] = FORMAL_SOURCES,
    ranking_limit: int = RANKING_LIMIT,
) -> dict[str, Any]:
    """单个知识库的运营统计，输出 KnowledgeAnalyticsOverview。

    base 是 knowledge_base_service.get_base 的返回（路由做完权限校验后顺手传进来，省一次
    查询）；库存 stock 直接取它的 documentCount / chunkCount——库存是「现在」的量，与统计
    区间无关。

    trend 只在区间内至少有一条日志时才铺满每一天（缺的天补零），否则返回空数组让面板走
    「暂无正式检索数据」的空态；铺零会让空库也画出一条贴地直线，看不出到底有没有数据。
    knowledgeBases 恒为一项（本库），面板在单库视图里不显示它，但契约字段要在。
    chunks 契约里有、面板不展示，也没有按切片记日志的必要，恒为空数组。
    """
    knowledge_id = str(base.get("id") or "")
    rows = await load_logs(knowledge_id, date_from=date_from, date_to=date_to, sources=sources)

    retrieval_count = len(rows)
    no_hit_count = sum(1 for r in rows if int(r.hit_count or 0) == 0)
    chunk_hit_count = sum(int(r.hit_count or 0) for r in rows)
    latencies = [int(r.latency_ms or 0) for r in rows]
    turns: set[str] = set()
    file_retrieval_count = 0
    daily: dict[date, dict[str, Any]] = defaultdict(
        lambda: {"qa": set(), "retrieval": 0, "file": 0, "chunk": 0, "noHit": 0},
    )
    per_document: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"qa": set(), "retrieval": 0, "chunk": 0},
    )
    query_counter: Counter[str] = Counter()
    query_no_hit: Counter[str] = Counter()

    for row in rows:
        turn_key = row.turn_id or row.id
        turns.add(turn_key)
        doc_ids = _decode_document_ids(row.hit_document_ids)
        unique_docs = list(dict.fromkeys(doc_ids))
        file_retrieval_count += len(unique_docs)

        day = daily[row.create_time.date()]
        day["qa"].add(turn_key)
        day["retrieval"] += 1
        day["file"] += len(unique_docs)
        day["chunk"] += int(row.hit_count or 0)
        if int(row.hit_count or 0) == 0:
            day["noHit"] += 1

        per_doc_chunks = Counter(doc_ids)
        for doc_id in unique_docs:
            stat = per_document[doc_id]
            stat["qa"].add(turn_key)
            stat["retrieval"] += 1
            stat["chunk"] += per_doc_chunks[doc_id]

        query_text = (row.query or "").strip()
        if query_text:
            query_counter[query_text] += 1
            if int(row.hit_count or 0) == 0:
                query_no_hit[query_text] += 1

    trend: list[dict[str, Any]] = []
    if rows:
        cursor = date_from
        while cursor <= date_to:
            day = daily.get(cursor)
            trend.append({
                "date": cursor.isoformat(),
                "qaCount": len(day["qa"]) if day else 0,
                "retrievalCount": day["retrieval"] if day else 0,
                "fileRetrievalCount": day["file"] if day else 0,
                "chunkHitCount": day["chunk"] if day else 0,
                "noHitCount": day["noHit"] if day else 0,
            })
            cursor += timedelta(days=1)

    names = await _document_names(per_document.keys())
    documents = []
    for doc_id, stat in per_document.items():
        item = _empty_ranking(doc_id, names.get(doc_id) or "（已删除的文档）", knowledge_id)
        item.update({
            "qaCount": len(stat["qa"]),
            "retrievalCount": stat["retrieval"],
            # 一个文档的「文件召回」就是它被召回的次数——每次召回内同一文件只算一次
            "fileRetrievalCount": stat["retrieval"],
            "chunkHitCount": stat["chunk"],
        })
        documents.append(item)
    documents.sort(key=lambda d: (-d["fileRetrievalCount"], -d["chunkHitCount"], d["name"]))
    limit = max(1, int(ranking_limit or RANKING_LIMIT))

    no_hit_rate = (no_hit_count / retrieval_count) if retrieval_count else 0.0
    base_rank = _empty_ranking(knowledge_id, str(base.get("name") or ""), knowledge_id)
    base_rank.update({
        "qaCount": len(turns),
        "retrievalCount": retrieval_count,
        "fileRetrievalCount": file_retrieval_count,
        "chunkHitCount": chunk_hit_count,
        "noHitCount": no_hit_count,
        "noHitRate": round(no_hit_rate, 4),
    })
    top_queries = [
        {"query": text, "count": count, "noHitCount": query_no_hit.get(text, 0)}
        for text, count in query_counter.most_common(TOP_QUERY_LIMIT)
    ]

    return {
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "stock": {
            "knowledgeBaseCount": 1,
            "documentCount": int(base.get("documentCount") or 0),
            "chunkCount": int(base.get("chunkCount") or 0),
        },
        "metrics": {
            "qaCount": len(turns),
            "retrievalCount": retrieval_count,
            "fileRetrievalCount": file_retrieval_count,
            "chunkHitCount": chunk_hit_count,
            "noHitCount": no_hit_count,
            "noHitRate": round(no_hit_rate, 4),
            "averageLatencyMs": int(round(sum(latencies) / len(latencies))) if latencies else 0,
        },
        "trend": trend,
        "knowledgeBases": [base_rank],
        "documents": documents[:limit],
        "chunks": [],
        # 契约之外的附加字段：热门问题（含各自的无命中次数），面板有则展示、无则忽略
        "topQueries": top_queries,
    }
