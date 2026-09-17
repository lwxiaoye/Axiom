"""引用快照随消息持久化（§7.3/ADR-033 的另一半，Phase 1 引用结构化下发）。

消息主体与有序引用展示快照共同保存到 MySQL `ai_chat_messages`，Runtime PG
`agent_message_citations` 保留运行环境内的引用索引（零 MySQL DDL）。
Runtime 不可用或切换环境时，历史仍可从同消息展示投影恢复文字来源与 [图N]。
"""
import logging
from typing import Dict, List, Optional

from sqlalchemy import delete, select

from app.core.runtime_db import runtime_session

logger = logging.getLogger(__name__)

# 行内 [编号] 引用角标按全局编号反查来源（web_search start_index 全局递增）：模型一轮可并发
# 多次搜索（实测 8 次×5 条=40 个来源），上限须覆盖否则大编号角标找不到来源退化成纯文本。
MAX_SOURCES_PER_MESSAGE = 50
# 搜索附带图片（type=image，正文 [图N] 图文混排）单独设限：与文本来源分池计数，
# 互不挤占——否则多路搜索来源逼近 50 条时排在尾部的图片会被截掉，[图N] 编号失配。
MAX_IMAGES_PER_MESSAGE = 12


def normalize_sources(sources: List[dict]) -> List[dict]:
    """长度与字段白名单标准化（不可信输入防护：来源文本进前端前收口）。

    图片来源（type=image）排在列表尾部且分池限量；[图N] 编号 = 它在 image 类条目中的
    序号（前端同规则反查），故 image 条目之间的相对顺序必须保持。
    """
    texts = [s for s in sources if isinstance(s, dict) and s.get("type") != "image"]
    images = [s for s in sources if isinstance(s, dict) and s.get("type") == "image"]
    result = []
    for s in texts[:MAX_SOURCES_PER_MESSAGE] + images[:MAX_IMAGES_PER_MESSAGE]:
        item = {
            "type": str(s.get("type") or "web")[:16],
            "title": str(s.get("title") or "")[:200],
            "url": str(s.get("url") or "")[:500],
            "source": str(s.get("source") or "")[:200],
            "snippet": str(s.get("snippet") or "")[:300],
        }
        # 图片直链的缩略图（可选）：列表/弱网场景前端可先展示缩略图
        if s.get("thumbnail"):
            item["thumbnail"] = str(s.get("thumbnail"))[:500]
        result.append(item)
    return result


async def save(thread_id: str, message_id: int, sources: List[dict]) -> None:
    if not sources or message_id is None:
        return
    normalized = normalize_sources(sources)
    from app.services.chat.history_trace_projection import persist_message_citation_projection

    await persist_message_citation_projection(thread_id, message_id, normalized)
    factory = runtime_session()
    if factory is None:
        return
    from app.runtime_models import AgentMessageCitation
    try:
        async with factory() as session:
            session.add(AgentMessageCitation(
                thread_id=thread_id, message_id=int(message_id), sources=normalized,
            ))
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("引用快照持久化失败（不影响对话）: %s", e)


async def get_for_thread(thread_id: str) -> Dict[int, List[dict]]:
    factory = runtime_session()
    if factory is None:
        return {}
    from app.runtime_models import AgentMessageCitation
    try:
        async with factory() as session:
            rows = (
                await session.execute(
                    select(AgentMessageCitation).where(AgentMessageCitation.thread_id == thread_id)
                )
            ).scalars().all()
            return {int(r.message_id): (r.sources or []) for r in rows}
    except Exception as e:  # noqa: BLE001
        logger.warning("读取引用快照失败: %s", e)
        return {}


async def delete_for_thread(thread_id: str) -> None:
    factory = runtime_session()
    if factory is None:
        return
    from app.runtime_models import AgentMessageCitation
    try:
        async with factory() as session:
            await session.execute(
                delete(AgentMessageCitation).where(AgentMessageCitation.thread_id == thread_id)
            )
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("清理引用快照失败: %s", e)
