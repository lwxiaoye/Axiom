"""Read campus configuration knowledge APIs with the current administrator token.

These helpers never use a service account.  Response shape is taken from the
runtime JSON, not guessed field names.
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
    payload = await _java_get(
        token, tenant_id, "/ai/knowledge/base/queryById", {"id": knowledge_id},
    )
    return payload if isinstance(payload, dict) else None


async def fetch_documents(token: str, tenant_id: str, knowledge_id: str) -> list[dict]:
    payload = await _java_get(
        token, tenant_id, "/ai/knowledge/document/list",
        {"knowledgeId": knowledge_id, "pageNo": 1, "pageSize": 200},
    )
    return _records(payload)


async def fetch_acl(token: str, tenant_id: str, knowledge_id: str) -> list[dict]:
    payload = await _java_get(
        token, tenant_id, "/ai/knowledge/acl/list", {"knowledgeId": knowledge_id},
    )
    return _records(payload)


def permission_of(row: dict) -> str:
    for key in (
        "currentPermission", "accessPermission", "aclPermission",
        "permission", "sharePermission",
    ):
        value = str(row.get(key) or "").strip().upper()
        if value:
            return "VIEWER" if value == "USER" else value
    return ""
