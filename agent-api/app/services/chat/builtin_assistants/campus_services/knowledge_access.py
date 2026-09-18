"""校园百事通读取知识库信息（知识库基础信息 / 文档列表 / ACL）。

知识库由 agent-api 自持（knowledge_base_service），这里在进程内直接调用，不经 HTTP。
历史上这些数据由 JeecgBoot(Java) 提供、本模块经 HTTP 回源，Java 下线后已改为进程内直调。
函数名与返回字段保持不变，调用方无需改动。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def fetch_knowledge_base(token: str, tenant_id: str, knowledge_id: str) -> Optional[dict]:
    """token / tenant_id 保留在签名里只为兼容调用方；本地库按 id 直查。"""
    from app.services.knowledge import knowledge_base_service as kb
    try:
        return await kb.get_base(knowledge_id)
    except Exception:  # noqa: BLE001
        logger.exception("读取知识库失败：%s", knowledge_id)
        return None


async def fetch_documents(token: str, tenant_id: str, knowledge_id: str) -> list[dict]:
    from app.services.knowledge import knowledge_base_service as kb
    try:
        return await kb.list_documents(knowledge_id)
    except Exception:  # noqa: BLE001
        logger.exception("读取知识库文档失败：%s", knowledge_id)
        return []


async def fetch_acl(token: str, tenant_id: str, knowledge_id: str) -> list[dict]:
    from app.services.knowledge import knowledge_base_service as kb
    try:
        return await kb.list_acl(knowledge_id)
    except Exception:  # noqa: BLE001
        logger.exception("读取知识库授权失败：%s", knowledge_id)
        return []


def permission_of(row: dict) -> str:
    for key in (
        "currentPermission", "accessPermission", "aclPermission",
        "permission", "sharePermission",
    ):
        value = str(row.get(key) or "").strip().upper()
        if value:
            return "VIEWER" if value == "USER" else value
    return ""
