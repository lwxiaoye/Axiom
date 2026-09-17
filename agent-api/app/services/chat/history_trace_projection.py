"""Immutable terminal presentation projection for cross-environment chat history.

``AgentRun`` / ``AgentPlan`` / events in Runtime PG remain authoritative while a
Run is active and for all lifecycle decisions. The shared MySQL chat history can
be opened through deployments backed by different Runtime databases, so a
terminal message also carries the already-committed user-visible projection.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.core.database import async_session
from app.core.runtime_db import runtime_session
from app.models import ChatMessage
from app.runtime_models import AgentRun


logger = logging.getLogger(__name__)

_MAX_PROJECTION_BYTES = 8 * 1024 * 1024
_LARGE_TEXT_KEYS = frozenset({"command", "preview", "error", "text", "preamble", "plan_report"})


def _trim_large_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [_trim_large_fields(item) for item in value]
    if not isinstance(value, dict):
        return value
    trimmed: dict[str, Any] = {}
    for key, item in value.items():
        # data-URI screenshots dominate old traces. They remain available in the
        # originating Runtime store; the shared projection prioritises truthful
        # step/plan/status structure and stays safely below MEDIUMTEXT.
        if key == "shot":
            continue
        if key in _LARGE_TEXT_KEYS and isinstance(item, str):
            trimmed[key] = item[:12000]
        else:
            trimmed[key] = _trim_large_fields(item)
    return trimmed


def encode_execution_trace_projection(trace: dict[str, Any]) -> str:
    payload = dict(trace)
    payload["projection_version"] = 1
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) <= _MAX_PROJECTION_BYTES:
        return encoded
    payload = _trim_large_fields(payload)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) > _MAX_PROJECTION_BYTES:
        raise ValueError("execution trace projection exceeds MEDIUMTEXT safety budget")
    return encoded


def decode_execution_trace_projection(raw: Any) -> Optional[dict[str, Any]]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def citations_from_projection(raw: Any) -> list[dict[str, Any]]:
    """Recover only the same message's saved, ordered display references."""
    from app.services.knowledge.citation_service import normalize_sources

    payload = decode_execution_trace_projection(raw) or {}
    sources = payload.get("citations")
    return normalize_sources(sources) if isinstance(sources, list) else []


async def persist_message_citation_projection(
    thread_id: str,
    message_id: Optional[int],
    sources: list[dict[str, Any]],
) -> bool:
    """Keep image/source references with the shared message, even if Runtime is unavailable."""
    from app.services.knowledge.citation_service import normalize_sources

    normalized = normalize_sources(sources)
    if not thread_id or not message_id or not normalized:
        return False
    try:
        async with async_session() as session:
            row = await session.get(ChatMessage, int(message_id), with_for_update=True)
            if row is None or row.role != "assistant" or str(row.thread_id) != str(thread_id):
                return False
            payload = decode_execution_trace_projection(row.execution_trace_json) or {}
            payload["citations"] = normalized
            encoded = encode_execution_trace_projection(payload)
            if row.execution_trace_json != encoded:
                row.execution_trace_json = encoded
                await session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - display durability must not rewrite Run status
        logger.warning("message citation projection failed message=%s: %s", message_id, exc)
        return False


async def persist_terminal_execution_trace_projection(
    run_id: str,
    message_id: Optional[int],
) -> bool:
    """Persist one terminal Run's committed event projection onto its message.

    This runs after the terminal SSE frame has been yielded and durably recorded.
    Failure never rewrites the Run's already-committed terminal state; startup or
    maintenance backfill can retry the immutable projection later.
    """
    if not run_id or not message_id:
        return False
    factory = runtime_session()
    if factory is None:
        return False
    try:
        async with factory() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                return False
            thread_id = str(run.thread_id or "")
        if not thread_id:
            return False

        from app.services.tasks import task_run_service

        run_traces: dict[str, dict[str, Any]] = {}
        by_message = await task_run_service.get_execution_traces_by_thread(
            thread_id,
            run_traces=run_traces,
            only_run_id=run_id,
        )
        trace = by_message.get(int(message_id)) or run_traces.get(str(run_id))
        if not isinstance(trace, dict):
            return False
        async with async_session() as session:
            row = await session.get(ChatMessage, int(message_id), with_for_update=True)
            if row is None or row.role != "assistant" or str(row.run_id or "") != str(run_id):
                return False
            # Citations arrive just before terminal finalization. Both writers lock this row
            # and merge their own fields, so neither can erase the other's display snapshot.
            payload = dict(trace)
            sources = citations_from_projection(row.execution_trace_json)
            if sources:
                payload["citations"] = sources
            encoded = encode_execution_trace_projection(payload)
            if row.execution_trace_json != encoded:
                row.execution_trace_json = encoded
                await session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - terminal state is already committed
        logger.warning("terminal execution trace projection failed run=%s: %s", run_id, exc)
        return False
