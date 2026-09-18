"""校园百事通读取知识库信息。

历史上这些数据由 JeecgBoot(Java) 提供，本模块经 HTTP 回源；Java 下线后该路径只会
拿到 auth-api 的 503 桩，导致校园百事通永远校验不过「至少绑定一个可用知识库」。
知识库现由 agent-api 自持（knowledge_base_service），故改为进程内直接调用——
同一进程还绕开一次 HTTP 往返。函数名与返回字段保持不变，调用方无需改动。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def _unwrap(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    if data.get("success") is False:
        return None
    if "result" in data:
        return data.get("result")
    return data


def _records(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("records", "list", "items"):
            raw = payload.get(key)
            if isinstance(raw, list):
                return [item for item in raw if isinstance(item, dict)]
    return []


async def _java_get(
    token: str,
    tenant_id: str,
    path: str,
    params: Optional[dict] = None,
) -> Any:
    headers = {"X-Access-Token": token or ""}
    if tenant_id:
        headers["X-Tenant-Id"] = str(tenant_id)
    url = f"{settings.JAVA_INTERNAL_BASE}{path}"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, params=params or {}, headers=headers)
    except Exception as exc:  # noqa: BLE001
        logger.warning("校园百事通回源 Java 失败 %s: %s", path, exc)
        return None
    if resp.status_code != 200:
        logger.warning("校园百事通回源 Java HTTP %s %s", resp.status_code, path)
        return None
    try:
        return _unwrap(resp.json())
    except Exception:  # noqa: BLE001
        return None


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
