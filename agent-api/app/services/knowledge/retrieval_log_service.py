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
