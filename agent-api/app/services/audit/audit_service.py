"""Read and write the safe, user-visible audit timeline.

The service deliberately stores only business metadata.  Prompts, access tokens,
tool arguments and SQL bodies stay out of the monitor-facing ledger; their
specialized stores remain the authority for incident investigation.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, time
from typing import Any

from sqlalchemy import and_, func, not_, or_, select

from app.core.database import async_session
from app.core.runtime_db import runtime_session
from app.models import AuditEvent
from app.runtime_models import AgentModelLogicalCall, AgentRun, AgentToolCall
from app.services.platform.user_display_name import load_user_display_names


CLIENT_CATEGORIES = frozenset({"login", "knowledge_access", "export"})
AUDIT_CATEGORIES = frozenset({
    "login", "knowledge_access", "model_call", "plugin_call", "database_query", "export",
})
_DATABASE_TOOL_PARTS = ("sql", "database", "db_", "query_db", "data_query")
_MAX_READ_ROWS = 1_000


def _compact(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _created_at(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


def _date_boundary(value: str | None, *, end: bool) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if len(value) == 10:
        return datetime.combine(parsed.date(), time.max if end else time.min)
    return parsed


def _database_tool_condition():
    lowered = func.lower(AgentToolCall.name)
    return or_(*[lowered.like(f"%{part}%") for part in _DATABASE_TOOL_PARTS])


def _tool_category(name: str) -> str:
    lowered = name.lower()
    return "database_query" if any(part in lowered for part in _DATABASE_TOOL_PARTS) else "plugin_call"


def _record(
    *,
    event_id: str,
    category: str,
    action: str,
    resource: str,
    status: str,
    actor_user_id: str,
    actor_username: str,
    ip: str,
    detail: str,
    source: str,
    create_time: Any,
) -> dict[str, str]:
    actor_user_id = _compact(actor_user_id, 64)
    actor_username = _compact(actor_username, 128)
    return {
        "id": event_id,
        "category": category,
        "action": action,
        "resource": resource,
        "status": status,
        "actorUserId": actor_user_id,
        "actorUsername": actor_username,
        # Keep the same field contract as the existing system-log table so its
        # operator, operator ID, IP and create-time columns can be reused.
        "userid": actor_user_id,
        "username": actor_username,
        "ip": ip,
        "detail": detail,
        "source": source,
        "createTime": _created_at(create_time),
    }


async def _hydrate_display_fields(records: list[dict[str, str]]) -> None:
    """Resolve runtime user IDs into monitor-facing names and normalize empty cells."""
    user_ids = [str(record.get("actorUserId") or "") for record in records]
    fallback_names = {
        str(record.get("actorUserId") or ""): str(record.get("actorUsername") or "")
        for record in records
        if str(record.get("actorUserId") or "")
    }
    try:
        async with async_session() as session:
            display_names = await load_user_display_names(session, user_ids, fallback_names)
    except Exception:  # noqa: BLE001 - audit listing remains available when name lookup is unavailable
        display_names = fallback_names

    for record in records:
        user_id = str(record.get("actorUserId") or "").strip()
        username = str(display_names.get(user_id) or fallback_names.get(user_id) or user_id).strip()
        record["actorUsername"] = username
        record["username"] = username
        record["userid"] = user_id
        record["ip"] = str(record.get("ip") or "").strip() or "—"


async def record_client_event(
    *,
    user: Any,
    category: str,
    action: str,
    resource: str = "",
    detail: str = "",
    ip: str = "",
) -> None:
    """Persist a client-confirmed critical operation for the authenticated actor."""
    if category not in CLIENT_CATEGORIES:
        raise ValueError("unsupported audit category")
    async with async_session() as session:
        session.add(AuditEvent(
            id=uuid.uuid4().hex,
            tenant_id=_compact(getattr(user, "tenant_id", "0"), 32) or "0",
            category=category,
            action=_compact(action, 128),
            resource=_compact(resource, 255),
            actor_user_id=_compact(getattr(user, "user_id", ""), 64),
            actor_username=_compact(
                getattr(user, "real_name", "") or getattr(user, "username", ""), 128,
            ),
            ip=_compact(ip, 64),
            detail=_compact(detail, 512),
            source="ui",
        ))
        await session.commit()


async def _list_client_records(
    *, category: str | None, keyword: str, start_at: datetime | None, end_at: datetime | None,
    limit: int,
) -> tuple[list[dict[str, str]], int]:
    conditions = []
    if category:
        conditions.append(AuditEvent.category == category)
    if start_at:
        conditions.append(AuditEvent.create_time >= start_at)
    if end_at:
        conditions.append(AuditEvent.create_time <= end_at)
    if keyword:
        token = f"%{keyword}%"
        conditions.append(or_(
            AuditEvent.action.like(token), AuditEvent.resource.like(token),
            AuditEvent.actor_username.like(token), AuditEvent.actor_user_id.like(token),
        ))
    async with async_session() as session:
        where = and_(*conditions) if conditions else None
        count_query = select(func.count()).select_from(AuditEvent)
        rows_query = select(AuditEvent).order_by(AuditEvent.create_time.desc()).limit(limit)
        if where is not None:
            count_query = count_query.where(where)
            rows_query = rows_query.where(where)
        total = int((await session.scalar(count_query)) or 0)
        rows = (await session.execute(rows_query)).scalars().all()
    return [
        _record(
            event_id=f"ui:{row.id}", category=row.category, action=row.action, resource=row.resource,
            status=row.status, actor_user_id=row.actor_user_id, actor_username=row.actor_username,
            ip=row.ip, detail=row.detail, source=row.source, create_time=row.create_time,
        )
        for row in rows
    ], total


async def _list_runtime_records(
    *, category: str | None, keyword: str, start_at: datetime | None, end_at: datetime | None,
    limit: int,
) -> tuple[list[dict[str, str]], int]:
    """Project existing runtime ledgers without duplicating their sensitive payloads."""
    factory = runtime_session()
    if factory is None:
        return [], 0
    requested_model = category == "model_call"
    requested_tool = category in {"plugin_call", "database_query"}
    if category and not (requested_model or requested_tool):
        return [], 0

    async with factory() as session:
        model_conditions = []
        tool_conditions = []
        if start_at:
            model_conditions.append(AgentModelLogicalCall.created_at >= start_at)
            tool_conditions.append(AgentToolCall.created_at >= start_at)
        if end_at:
            model_conditions.append(AgentModelLogicalCall.created_at <= end_at)
            tool_conditions.append(AgentToolCall.created_at <= end_at)
        if keyword:
            token = f"%{keyword.lower()}%"
            model_conditions.append(or_(
                func.lower(AgentModelLogicalCall.model).like(token),
                func.lower(AgentModelLogicalCall.purpose).like(token),
                func.lower(AgentRun.user_id).like(token),
            ))
            tool_conditions.append(or_(
                func.lower(AgentToolCall.name).like(token), func.lower(AgentRun.user_id).like(token),
            ))

        database_condition = _database_tool_condition()
        if category == "database_query":
            tool_conditions.append(database_condition)
        elif category == "plugin_call":
            tool_conditions.append(not_(database_condition))

        records: list[dict[str, str]] = []
        total = 0
        if not requested_tool:
            model_where = and_(*model_conditions) if model_conditions else None
            model_count = select(func.count()).select_from(AgentModelLogicalCall).join(
                AgentRun, AgentRun.id == AgentModelLogicalCall.run_id,
            )
            model_rows = select(AgentModelLogicalCall, AgentRun.user_id).join(
                AgentRun, AgentRun.id == AgentModelLogicalCall.run_id,
            ).order_by(AgentModelLogicalCall.created_at.desc()).limit(limit)
            if model_where is not None:
                model_count = model_count.where(model_where)
                model_rows = model_rows.where(model_where)
            total += int((await session.scalar(model_count)) or 0)
            for row, user_id in (await session.execute(model_rows)).all():
                records.append(_record(
                    event_id=f"model:{row.id}", category="model_call", action="模型调用",
                    resource=f"{row.model or '未指定模型'} · {row.purpose or 'main_loop'}",
                    status=str(row.status or "started"), actor_user_id=str(user_id or ""),
                    actor_username="", ip="", detail=f"运行 {row.run_id}", source="runtime",
                    create_time=row.created_at,
                ))
        if not requested_model:
            tool_where = and_(*tool_conditions) if tool_conditions else None
            tool_count = select(func.count()).select_from(AgentToolCall).join(
                AgentRun, AgentRun.id == AgentToolCall.run_id,
            )
            tool_rows = select(AgentToolCall, AgentRun.user_id).join(
                AgentRun, AgentRun.id == AgentToolCall.run_id,
            ).order_by(AgentToolCall.created_at.desc()).limit(limit)
            if tool_where is not None:
                tool_count = tool_count.where(tool_where)
                tool_rows = tool_rows.where(tool_where)
            total += int((await session.scalar(tool_count)) or 0)
            for row, user_id in (await session.execute(tool_rows)).all():
                event_category = _tool_category(str(row.name or ""))
                records.append(_record(
                    event_id=f"tool:{row.id}", category=event_category,
                    action="数据库查询" if event_category == "database_query" else "插件/工具调用",
                    resource=str(row.name or "未命名工具"), status=str(row.status or "pending"),
                    actor_user_id=str(user_id or ""), actor_username="", ip="",
                    detail=f"运行 {row.run_id}", source="runtime", create_time=row.created_at,
                ))
    return records, total


async def list_audit_events(
    *, category: str | None = None, keyword: str = "", start: str | None = None, end: str | None = None,
    page_no: int = 1, page_size: int = 10,
) -> dict[str, Any]:
    """Return a chronologically merged, paginated timeline from MySQL and Runtime PG."""
    category = category if category in AUDIT_CATEGORIES else None
    page_no = max(1, int(page_no or 1))
    page_size = min(100, max(1, int(page_size or 10)))
    fetch_limit = min(_MAX_READ_ROWS, max(100, page_no * page_size))
    start_at = _date_boundary(start, end=False)
    end_at = _date_boundary(end, end=True)
    keyword = _compact(keyword, 128)

    client, runtime = await asyncio.gather(
        _list_client_records(
            category=category, keyword=keyword, start_at=start_at, end_at=end_at, limit=fetch_limit,
        ),
        _list_runtime_records(
            category=category, keyword=keyword, start_at=start_at, end_at=end_at, limit=fetch_limit,
        ),
        return_exceptions=True,
    )
    client_rows, client_total = client if not isinstance(client, Exception) else ([], 0)
    runtime_rows, runtime_total = runtime if not isinstance(runtime, Exception) else ([], 0)
    rows = sorted(
        [*client_rows, *runtime_rows], key=lambda item: item["createTime"], reverse=True,
    )
    await _hydrate_display_fields(rows)
    offset = (page_no - 1) * page_size
    return {"records": rows[offset:offset + page_size], "total": client_total + runtime_total}
