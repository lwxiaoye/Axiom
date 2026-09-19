"""Qdrant 客户端共享入口（知识库检索用）。

历史上这里还承载「智能体广场向量索引」（agents_v* 集合的 upsert/search/路由索引与陈旧集合
清理）——那条链路服务于已发布工作流智能体的推荐/路由，随工作流编排整体删除。知识库的
集合命名与读写在 knowledge/chunk_service 与 knowledge_base_service 自持，这里只保留客户端。
"""
import logging
from typing import Optional

from qdrant_client import AsyncQdrantClient

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: Optional[AsyncQdrantClient] = None


def _get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(url=settings.QDRANT_URL)
    return _client
