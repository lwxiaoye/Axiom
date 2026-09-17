"""Persistence and read models for developer workflow evaluation traces.

The execution engine remains the source of truth for a live run.  This module
only persists a bounded, redacted snapshot after a route receives progress or a
terminal result.  It must never make workflow execution fail when Runtime PG is
temporarily unavailable.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import and_, func, select

from app.core.runtime_db import runtime_session
from app.runtime_models import AgentModelAttemptAudit, WorkflowEvaluationRun


logger = logging.getLogger(__name__)
MAX_TEXT_CHARS = 32_000
MAX_DOCUMENT_CHARS = 512_000
_SENSITIVE_KEYS = {"authorization", "cookie", "password", "secret", "token", "api_key", "apikey"}


def workflow_definition_hash(workflow_json: str) -> str:
    raw = workflow_json or ""
    try:
        raw = json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        pass
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    """Keep traces inspectable without persisting common credentials or huge bodies."""
    if depth >= 8:
        return "[深度截断]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if any(marker in key.lower() for marker in _SENSITIVE_KEYS):
                result[key] = "[已脱敏]"
            else:
                result[key] = _safe_value(raw_value, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        values = [_safe_value(item, depth=depth + 1) for item in value[:200]]
        if len(value) > 200:
            values.append("[列表截断]")
        return values
    if isinstance(value, str):
        return value if len(value) <= MAX_TEXT_CHARS else f"{value[:MAX_TEXT_CHARS]}\n[内容截断]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:MAX_TEXT_CHARS]


def _safe_document(value: Any, fallback: Any) -> Any:
    safe = _safe_value(value)
    rendered = repr(safe)
    if len(rendered) <= MAX_DOCUMENT_CHARS:
        return safe
    return fallback


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _record_dict(row: WorkflowEvaluationRun, *, include_trace: bool = False) -> dict[str, Any]:
    payload = {
        "runId": row.id,
        "appId": row.app_id,
        "mode": row.mode,
        "definitionHash": row.definition_hash,
        "previewOnly": bool(row.preview_only),
        "status": row.status,
        "input": row.input_text or "",
        "output": row.output or "",
        "errorMessage": row.error_message,
        "durationMs": row.duration_ms,
        "startedAt": _iso(row.started_at),
        "completedAt": _iso(row.completed_at),
        "updatedAt": _iso(row.updated_at),
    }
    if include_trace:
        payload.update({
            "variables": row.variables or {},
            "nodeRuns": row.node_runs or [],
            "outputs": row.outputs or {},
            "edges": row.edges or [],
        })
    return payload


async def record_workflow_evaluation_run(
    *,
    app_id: str,
    user_id: str,
    workflow_json: str,
    mode: str,
    input_text: str,
    variables: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    preview_only: bool,
) -> None:
    """Insert or refresh one run snapshot; observability is deliberately fail-open."""
    run_id = str(result.get("runId") or "")[:64]
    factory = runtime_session()
    if not run_id or factory is None:
        return
    duration_ms = result.get("durationMs")
    try:
        duration = max(0, int(duration_ms)) if duration_ms is not None else None
    except (TypeError, ValueError):
        duration = None
    now = datetime.utcnow()
    status = str(result.get("status") or "running")[:32]
    terminal = status in {"success", "failed", "completed", "stopped"}
    try:
        async with factory() as session:
            row = await session.get(WorkflowEvaluationRun, run_id)
            if row is None:
                row = WorkflowEvaluationRun(
                    id=run_id,
                    app_id=str(app_id)[:64],
                    user_id=str(user_id)[:64],
                    mode=str(mode)[:32],
                    definition_hash=workflow_definition_hash(workflow_json),
                    preview_only=bool(preview_only),
                    started_at=now - timedelta(milliseconds=duration or 0),
                )
                session.add(row)
            row.status = status
            row.input_text = str(input_text or "")[:MAX_TEXT_CHARS]
            row.variables = _safe_document(dict(variables or {}), {})
            row.output = str(result.get("output") or "")[:MAX_TEXT_CHARS]
            row.error_message = str(result.get("errorMessage") or "")[:MAX_TEXT_CHARS] or None
            row.duration_ms = duration
            row.node_runs = _safe_document(result.get("nodeRuns") or [], [])
            row.outputs = _safe_document(result.get("outputs") or {}, {})
            row.edges = _safe_document(result.get("edges") or [], [])
            row.completed_at = now if terminal else None
            await session.commit()
    except Exception:  # noqa: BLE001 - evaluation records cannot break a workflow run
        logger.warning("workflow evaluation trace persistence failed run_id=%s", run_id, exc_info=True)


def _parse_datetime(value: str | None, name: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} 必须是 ISO 时间") from exc
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


async def list_workflow_evaluation_runs(
    *, app_id: str, start_at: str | None, end_at: str | None, status: str | None,
    mode: str | None, min_duration_ms: int | None, min_total_tokens: int | None,
    page_no: int, page_size: int,
) -> dict[str, Any]:
    factory = runtime_session()
    if factory is None:
        raise RuntimeError("Runtime 数据库未配置，暂不能查询评测记录")
    if page_no < 1 or page_size < 1 or page_size > 100:
        raise ValueError("分页参数无效")
    start, end = _parse_datetime(start_at, "startAt"), _parse_datetime(end_at, "endAt")
    if start and end and start >= end:
        raise ValueError("startAt 必须早于 endAt")
    if min_duration_ms is not None and min_duration_ms < 0:
        raise ValueError("minDurationMs 不能小于 0")
    if min_total_tokens is not None and min_total_tokens < 0:
        raise ValueError("minTotalTokens 不能小于 0")
    async with factory() as session:
        conditions = [WorkflowEvaluationRun.app_id == str(app_id)]
        if start:
            conditions.append(WorkflowEvaluationRun.started_at >= start)
        if end:
            conditions.append(WorkflowEvaluationRun.started_at < end)
        if status:
            conditions.append(WorkflowEvaluationRun.status == str(status)[:32])
        if mode:
            conditions.append(WorkflowEvaluationRun.mode == str(mode)[:32])
        if min_duration_ms is not None:
            conditions.append(WorkflowEvaluationRun.duration_ms >= min_duration_ms)
        statement = select(WorkflowEvaluationRun).where(and_(*conditions))
        if min_total_tokens is not None:
            token_total = (
                func.sum(func.coalesce(AgentModelAttemptAudit.input_tokens, 0))
                + func.sum(func.coalesce(AgentModelAttemptAudit.output_tokens, 0))
            )
            matching_runs = (
                select(AgentModelAttemptAudit.run_id)
                .group_by(AgentModelAttemptAudit.run_id)
                .having(token_total >= min_total_tokens)
            )
            statement = statement.where(WorkflowEvaluationRun.id.in_(matching_runs))
        count = await session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = (await session.execute(
            statement.order_by(WorkflowEvaluationRun.started_at.desc(), WorkflowEvaluationRun.id.desc())
            .offset((page_no - 1) * page_size).limit(page_size)
        )).scalars().all()
        return {"records": [_record_dict(row) for row in rows], "total": int(count or 0)}


async def get_workflow_evaluation_run(*, app_id: str, run_id: str) -> dict[str, Any] | None:
    factory = runtime_session()
    if factory is None:
        raise RuntimeError("Runtime 数据库未配置，暂不能查询评测记录")
    async with factory() as session:
        row = await session.get(WorkflowEvaluationRun, str(run_id)[:64])
        if row is None or row.app_id != str(app_id):
            return None
        attempts = (await session.execute(
            select(AgentModelAttemptAudit)
            .where(AgentModelAttemptAudit.run_id == row.id)
            .order_by(AgentModelAttemptAudit.started_at.asc(), AgentModelAttemptAudit.run_sequence.asc())
        )).scalars().all()
        result = _record_dict(row, include_trace=True)
        result["modelCalls"] = [
            {
                "id": attempt.id,
                "model": attempt.model,
                "purpose": attempt.purpose,
                "nodeId": attempt.scope_key,
                "status": attempt.terminal_status,
                "durationMs": attempt.latency_ms,
                "inputTokens": attempt.input_tokens,
                "outputTokens": attempt.output_tokens,
                "reasoningTokens": attempt.reasoning_tokens,
                "cacheReadTokens": attempt.cache_read_tokens,
                "startedAt": _iso(attempt.started_at),
                "completedAt": _iso(attempt.completed_at),
                "errorCode": attempt.error_code,
            }
            for attempt in attempts
        ]
        return result
