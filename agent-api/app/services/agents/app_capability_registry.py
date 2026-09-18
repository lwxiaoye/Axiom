"""Capability Registry 权威表服务（§12.1/§12.2）——`app_info_capability_registry`。

与 Java 业务库同库 `ai_boot`，关联 `app_info`。设计上由 Java 发布/删除流程写入 + 发签名
变更事件；Java 侧接管前，agent-api 暂代：
- **写**：从 `app_info`（app_type=external 广场应用）同步为 `execution_scope=external_app` 的
  Capability，供 R6 外部兜底推荐（§8.1 R6 / §8.4 「前往使用」卡，不派发 call_subagent）。
- **消费**：`POST /workflow/registry/events` 幂等消费 Java 签名变更事件（§12.2）。

工作台**内部**可路由子智能体不在本表（它们无 app_info 行）——仍由 `capability_registry`
（`agent_capability_registry`）承载，两者边界见架构 §12.1。
"""
import base64
import hashlib
import hmac
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text

from app.core.config import settings
from app.core.database import async_session
from app.models import AppInfoCapabilityRegistry
from app.services.agents.published_visibility import tenant_matches, tenant_visible_clause

logger = logging.getLogger(__name__)

_EXTERNAL_SOURCE = "external_catalog"
_EXTERNAL_SCOPE = "external_app"


def _cap_code_for_app_info(app_info_id: str) -> str:
    """external 应用的稳定 capability_code（租户内唯一，幂等 upsert 依据）。"""
    return f"ext_{app_info_id}"


def _enabled_from_status(status: Any) -> int:
    s = str(status).strip().lower() if status is not None else ""
    return 0 if s in ("0", "disabled", "off", "false", "no") else 1


async def _fetch_external_app_info(limit: Optional[int] = None) -> List[dict]:
    """读 app_info 的 external 广场应用（del_flag=0）。列映射对齐 backfill_service。"""
    sql = (
        "SELECT id, app_name AS name, app_remark AS description, app_icon AS icon, "
        "app_category AS category, status, tenant_id, pc_url, h5_url, create_by AS owner "
        "FROM app_info WHERE app_type = 'external' "
        "AND (del_flag = '0' OR del_flag = 0 OR del_flag IS NULL)"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    async with async_session() as session:
        result = await session.execute(text(sql))
        return [dict(r) for r in result.mappings().all()]


async def _upsert_external(session, row: dict) -> None:
    app_info_id = str(row["id"])
    cap_code = _cap_code_for_app_info(app_info_id)
    tenant_id = str(row.get("tenant_id") or "0")
    existing = (
        await session.execute(
            select(AppInfoCapabilityRegistry).where(
                AppInfoCapabilityRegistry.tenant_id == tenant_id,
                AppInfoCapabilityRegistry.capability_code == cap_code,
            )
        )
    ).scalar_one_or_none()
    reg = existing or AppInfoCapabilityRegistry(id=uuid.uuid4().hex, capability_code=cap_code)
    reg.app_info_id = app_info_id
    reg.tenant_id = tenant_id
    reg.capability_name = str(row.get("name") or "")[:128]
    reg.source_system = _EXTERNAL_SOURCE
    reg.source_app_id = app_info_id
    reg.route_description = str(row.get("description") or "")
    reg.capability_type = "subagent"
    reg.execution_scope = _EXTERNAL_SCOPE
    reg.launch_mode = "redirect"
    reg.runtime_type = "external_link"
    reg.endpoint = str(row.get("pc_url") or row.get("h5_url") or "")[:500]
    reg.risk_level = "low"
    reg.enabled = _enabled_from_status(row.get("status"))
    reg.health_status = "healthy"
    reg.owner = str(row.get("owner") or "")[:64]
    if not existing:
        session.add(reg)


async def sync_external_from_app_info(limit: Optional[int] = None) -> int:
    """把 app_info 的 external 广场应用同步为权威表里的 external_app Capability。返回同步条数。"""
    rows = await _fetch_external_app_info(limit)
    async with async_session() as session:
        for row in rows:
            await _upsert_external(session, row)
        await session.commit()
    logger.info("app_info external → 权威 registry 同步 %d 条", len(rows))
    return len(rows)


async def external_candidates(limit: int = 50, *, tenant_id: str) -> List[Dict[str, Any]]:
    """R6 外部兜底候选（§8.1）：权威表里 execution_scope=external_app 且**本租户可见**的启用条目。

    返回 {id(app_info_id), name, description, url, source:external}——用于推荐「前往使用」，
    **不**派发 call_subagent、**不**建 Task Run（§8.4/§12.1 边界）。

    租户过滤（深扫 P0-3）：不自造语义，直接复用 published_visibility 的唯一事实源——
    SQL 侧 tenant_visible_clause（保证 LIMIT 之前就过滤掉别租户），返回前再用 tenant_matches
    复核。应用侧 NULL/''/'0' 视为全租户可见（历史/未分配占位），真实租户（非 0）严格相等。
    tenant_id 为必传关键字参数：调用方必须显式给出调用者租户，漏传即 TypeError 而不是静默
    跨租户泄漏。
    """
    caller_tenant = str(tenant_id or "0").strip() or "0"
    async with async_session() as session:
        rows = (
            await session.execute(
                select(AppInfoCapabilityRegistry)
                .where(AppInfoCapabilityRegistry.execution_scope == _EXTERNAL_SCOPE)
                .where(AppInfoCapabilityRegistry.enabled == 1)
                .where(tenant_visible_clause(AppInfoCapabilityRegistry.tenant_id, caller_tenant))
                .order_by(AppInfoCapabilityRegistry.update_time.desc())
                .limit(limit)
            )
        ).scalars().all()
    return [
        {
            "id": r.app_info_id,
            "name": r.capability_name or "",
            "description": r.route_description or "",
            "url": r.endpoint or "",
            "source": "external",
        }
        for r in rows
        if tenant_matches(r.tenant_id, caller_tenant)
    ]


async def backfill_if_empty() -> int:
    """启动期一次性：权威表无 external_app 行时从 app_info 同步。幂等、best-effort。"""
    try:
        async with async_session() as session:
            has = (
                await session.execute(
                    select(AppInfoCapabilityRegistry.id)
                    .where(AppInfoCapabilityRegistry.execution_scope == _EXTERNAL_SCOPE)
                    .limit(1)
                )
            ).scalars().first()
        if has:
            return 0
        return await sync_external_from_app_info()
    except Exception:  # noqa: BLE001
        logger.warning("权威 registry 启动回填失败", exc_info=True)
        return 0


# ---------- 签名变更事件消费（§12.2）----------

def verify_event_signature(canonical: str, timestamp: str, signature: str) -> None:
    """校验 Java 变更事件签名：HMAC-SHA256(INTERNAL_SYNC_SECRET, canonical) base64url。

    失败关闭：未配置（空或占位 CHANGE_ME）secret 时**拒绝**而非放行——该接口无用户鉴权，
    放行等于开放无鉴权写入。要联调请显式配 INTERNAL_SYNC_SECRET。抛 ValueError 表示拒绝。
    """
    secret = settings.INTERNAL_SYNC_SECRET
    if not secret or secret in {"CHANGE_ME", "change-me"}:
        raise ValueError("内部同步密钥未配置")
    if not timestamp or not signature:
        raise ValueError("缺少签名或时间戳")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise ValueError("时间戳非法") from exc
    if abs(time.time() - ts) > settings.INTERNAL_SYNC_MAX_AGE_SECONDS:
        raise ValueError("事件已过期")
    digest = hmac.new(secret.encode(), f"{canonical}\n{timestamp}".encode(), hashlib.sha256).digest()
    expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    if not hmac.compare_digest(signature, expected):
        raise ValueError("签名不匹配")


async def consume_change_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """幂等消费一条能力变更事件（§12.2）。event: {operation, capability_code, tenant_id, ...fields}。

    operation=upsert：按 (tenant_id, capability_code) 幂等 upsert 路由/边界/运维字段；
    operation=delete：按 (tenant_id, capability_code) 删除。返回 {status, capability_code}。
    """
    op = str(event.get("operation") or "upsert").lower()
    cap_code = str(event.get("capability_code") or "").strip()
    tenant_id = str(event.get("tenant_id") or "0")
    if not cap_code:
        return {"status": "rejected", "reason": "missing capability_code"}
    async with async_session() as session:
        existing = (
            await session.execute(
                select(AppInfoCapabilityRegistry).where(
                    AppInfoCapabilityRegistry.tenant_id == tenant_id,
                    AppInfoCapabilityRegistry.capability_code == cap_code,
                )
            )
        ).scalar_one_or_none()
        if op == "delete":
            if existing:
                await session.delete(existing)
                await session.commit()
                from app.services.agents.recommendation_index import request_rebuild
                request_rebuild()
                return {"status": "deleted", "capability_code": cap_code}
            return {"status": "noop", "capability_code": cap_code}
        # upsert
        reg = existing or AppInfoCapabilityRegistry(id=uuid.uuid4().hex, capability_code=cap_code)
        reg.tenant_id = tenant_id
        if event.get("app_info_id"):
            reg.app_info_id = str(event["app_info_id"])
        elif not existing:
            reg.app_info_id = str(event.get("source_app_id") or cap_code)
        for field in (
            "capability_name", "source_system", "source_app_id", "route_description",
            "trigger_examples", "negative_examples", "tags", "capability_type",
            "execution_scope", "provider", "launch_mode", "runtime_type", "endpoint",
            "remote_app_id", "protocol_version", "risk_level", "approval_policy",
            "health_status", "owner",
        ):
            if field in event and event[field] is not None:
                setattr(reg, field, event[field])
        if "enabled" in event and event["enabled"] is not None:
            reg.enabled = 1 if event["enabled"] in (1, True, "1", "true") else 0
        if event.get("source_published_version") is not None:
            try:
                reg.source_published_version = int(event["source_published_version"])
            except (TypeError, ValueError):
                pass
        if not existing:
            session.add(reg)
        await session.commit()
    from app.services.agents.recommendation_index import request_rebuild
    request_rebuild()
    return {"status": "upserted", "capability_code": cap_code}
