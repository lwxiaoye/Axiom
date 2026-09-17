"""Fail-open audit ledger for model-visible main-chat requests."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from sqlalchemy import delete, select

from app.core.runtime_db import runtime_session
from app.runtime_models import AgentModelCacheAudit, AgentModelInputAudit

logger = logging.getLogger(__name__)

_SENSITIVE_KEYS = {
    "api_key", "apikey", "authorization", "cookie", "password", "secret",
    "token", "access_token", "refresh_token", "x-access-token", "x-api-key",
}
AUDIT_METRICS = {
    "attempted": 0,
    "persisted": 0,
    "persistence_failed": 0,
    "matched": 0,
    "mismatched": 0,
    "cache_persisted": 0,
    "cache_persistence_failed": 0,
}


@dataclass(frozen=True)
class ModelRequestAuditHandle:
    request_id: str
    run_id: str
    thread_id: str
    request_sequence: int
    model: str
    context_epoch: int = 0
    epoch_reason: str = "initial"
    base_prompt_hash: str = ""
    tool_schema_hash: str = ""
    state_snapshot_hash: str = ""
    prefix_diagnostics: dict[str, Any] = field(default_factory=dict)
    logical_call_id: str = ""
    attempt_id: str = ""
    run_sequence: int = 0
    purpose: str = "main_loop"
    purpose_detail: str = ""


@dataclass(frozen=True)
class NormalizedCacheUsage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int | None
    cache_miss_tokens: int | None
    cache_write_tokens: int | None
    cache_miss_source: str
    usage_schema: str
    reasoning_tokens: int | None = None
    provider_amount_raw: str | None = None
    provider_amount_unit: str | None = None


def _sanitize(value: Any, *, depth: int = 0) -> Any:
    if depth > 24:
        return "[depth-limit]"
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in {item.replace("-", "_") for item in _SENSITIVE_KEYS}:
                cleaned[str(key)] = "[redacted]"
            elif depth == 0 and normalized == "reasoning" and isinstance(item, dict):
                # Provider-visible request controls are part of the exact wire payload and affect
                # billing.  Preserve the public control surface, but never nested model reasoning.
                allowed = {"effort", "summary"}
                cleaned[str(key)] = {
                    str(child_key): _sanitize(child_value, depth=depth + 1)
                    for child_key, child_value in item.items()
                    if str(child_key).strip().lower().replace("-", "_") in allowed
                }
            elif depth == 0 and normalized == "thinking" and isinstance(item, dict):
                # DeepSeek Chat Completions uses this public request switch.
                cleaned[str(key)] = {
                    str(child_key): _sanitize(child_value, depth=depth + 1)
                    for child_key, child_value in item.items()
                    if str(child_key).strip().lower().replace("-", "_") == "type"
                }
            elif normalized in {"reasoning", "reasoning_content", "thinking", "chain_of_thought"}:
                # Hidden reasoning is not model input and must not become an audit payload if a
                # provider-specific retry object happens to contain it.
                continue
            else:
                cleaned[str(key)] = _sanitize(item, depth=depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth=depth + 1) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _manifest(payload: Dict[str, Any]) -> list[dict]:
    rows = []
    model_items = payload.get("input") if isinstance(payload.get("input"), list) else payload.get("messages")
    for index, message in enumerate(model_items or []):
        if not isinstance(message, dict):
            continue
        content = _sanitize(message)
        role = str(message.get("role") or "").strip().lower()
        item_type = str(message.get("type") or "").strip().lower()
        if item_type == "function_call_output":
            kind = "tool_result"
        elif item_type == "function_call":
            kind = "tool_call"
        elif role in {"system", "developer"}:
            kind = "system_prompt"
        elif role == "tool":
            kind = "tool_result"
        elif role == "assistant" and message.get("tool_calls"):
            kind = "tool_call"
        elif role == "assistant":
            kind = "assistant_message"
        elif role == "user":
            kind = "user_message"
        else:
            kind = "message"
        rows.append({
            "kind": kind,
            "source_id": str(
                message.get("tool_call_id")
                or message.get("call_id")
                or message.get("id")
                or index
            ),
            "role": role,
            "content_hash": _canonical_hash(content),
        })
    for index, tool in enumerate(payload.get("tools") or []):
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") or tool
        rows.append({
            "kind": "tool_schema",
            "source_id": str(function.get("name") or index),
            "content_hash": _canonical_hash(_sanitize(tool)),
        })
    return rows


def _model_items(payload: Dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("input") if isinstance(payload.get("input"), list) else payload.get("messages")
    return [item for item in (raw or []) if isinstance(item, dict)]


def _item_kind(item: dict[str, Any] | None) -> str:
    if not isinstance(item, dict):
        return "end_of_input"
    item_type = str(item.get("type") or "").strip().lower()
    role = str(item.get("role") or "").strip().lower()
    if item_type:
        return item_type
    return role or "message"


def _common_prefix_chars(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


def _prefix_diagnostics(
    previous_payload: Dict[str, Any] | None,
    current_payload: Dict[str, Any],
    *,
    previous_request_id: str = "",
) -> dict[str, Any]:
    if not isinstance(previous_payload, dict):
        return {"available": False}
    previous_items = _model_items(previous_payload)
    current_items = _model_items(current_payload)
    item_lcp = 0
    for before, after in zip(previous_items, current_items):
        if _canonical_hash(before) != _canonical_hash(after):
            break
        item_lcp += 1
    previous_text = json.dumps(
        previous_items, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    current_text = json.dumps(
        current_items, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    divergence = current_items[item_lcp] if item_lcp < len(current_items) else None
    previous_tools = _sanitize(previous_payload.get("tools") or [])
    current_tools = _sanitize(current_payload.get("tools") or [])
    return {
        "available": True,
        "previous_request_id": str(previous_request_id or ""),
        "previous_item_count": len(previous_items),
        "current_item_count": len(current_items),
        "lcp_item_count": item_lcp,
        "previous_canonical_chars": len(previous_text),
        "current_canonical_chars": len(current_text),
        "lcp_canonical_chars": _common_prefix_chars(previous_text, current_text),
        "first_divergence_index": item_lcp,
        "first_divergence_kind": _item_kind(divergence),
        "tool_schema_equal": _canonical_hash(previous_tools) == _canonical_hash(current_tools),
    }


def _as_non_negative_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, parsed)


def _first_usage_int(*values: Any) -> int | None:
    for value in values:
        parsed = _as_non_negative_int(value)
        if parsed is not None:
            return parsed
    return None


def normalize_cache_usage(usage: Any) -> NormalizedCacheUsage:
    # The v4 ledger is the canonical normalizer.  Keeping this adapter preserves the
    # runtime_0015 API while preventing usage-shape drift between old and new callers.
    try:
        from app.services.agent_harness.model_usage_audit import normalize_model_usage

        normalized = normalize_model_usage(usage)
        return NormalizedCacheUsage(
            input_tokens=normalized.input_tokens or 0,
            output_tokens=normalized.output_tokens or 0,
            cache_read_tokens=normalized.cache_read_tokens,
            cache_miss_tokens=normalized.cache_miss_tokens,
            cache_write_tokens=normalized.cache_write_tokens,
            cache_miss_source=normalized.cache_miss_source,
            usage_schema=normalized.usage_schema,
            reasoning_tokens=normalized.reasoning_tokens,
            provider_amount_raw=normalized.provider_amount_raw,
            provider_amount_unit=normalized.provider_amount_unit,
        )
    except ImportError:
        # Transitional fallback for deployments that have not loaded the new module yet.
        pass
    payload = usage if isinstance(usage, dict) else {}
    input_details = payload.get("input_tokens_details")
    prompt_details = payload.get("prompt_tokens_details")
    input_details = input_details if isinstance(input_details, dict) else {}
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
    input_tokens = _first_usage_int(payload.get("input_tokens"), payload.get("prompt_tokens")) or 0
    output_tokens = _first_usage_int(payload.get("output_tokens"), payload.get("completion_tokens")) or 0
    direct_hit = _as_non_negative_int(payload.get("prompt_cache_hit_tokens"))
    detail_hit = _first_usage_int(
        input_details.get("cached_tokens"),
        prompt_details.get("cached_tokens"),
    )
    cache_read = direct_hit if direct_hit is not None else detail_hit
    reported_miss = _as_non_negative_int(payload.get("prompt_cache_miss_tokens"))
    if reported_miss is not None:
        cache_miss = reported_miss
        cache_miss_source = "reported"
    elif cache_read is not None:
        cache_miss = max(0, input_tokens - cache_read)
        cache_miss_source = "derived"
    else:
        cache_miss = None
        cache_miss_source = "unknown"
    cache_write = _first_usage_int(
        payload.get("cache_write_tokens"),
        payload.get("prompt_cache_write_tokens"),
        payload.get("cache_creation_input_tokens"),
        input_details.get("cache_write_tokens"),
        prompt_details.get("cache_write_tokens"),
    )
    if direct_hit is not None or reported_miss is not None:
        usage_schema = "deepseek"
    elif input_details:
        usage_schema = "responses"
    elif prompt_details or "prompt_tokens" in payload:
        usage_schema = "chat_completions"
    else:
        usage_schema = "unknown"
    return NormalizedCacheUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_miss_tokens=cache_miss,
        cache_write_tokens=cache_write,
        cache_miss_source=cache_miss_source,
        usage_schema=usage_schema,
    )


async def record_model_request(
    *, payload: Dict[str, Any], run_id: str, thread_id: str = "", model: str,
    request_sequence: int, shadow_payload: Optional[Dict[str, Any]] = None,
    context_metadata: Optional[Dict[str, Any]] = None,
) -> Optional[ModelRequestAuditHandle]:
    """Persist one request snapshot; every failure is logged and returned as None."""
    AUDIT_METRICS["attempted"] += 1
    factory = runtime_session()
    if factory is None or not run_id:
        AUDIT_METRICS["persistence_failed"] += 1
        return None
    request_id = uuid.uuid4().hex
    request_sequence = max(0, int(request_sequence or 0))
    context_metadata = dict(context_metadata or {})
    visible = _sanitize(payload)
    shadow = _sanitize(shadow_payload if shadow_payload is not None else dict(payload))
    payload_hash = _canonical_hash(visible)
    shadow_hash = _canonical_hash(shadow)
    match_status = "match" if payload_hash == shadow_hash else "mismatch"
    if match_status == "match":
        AUDIT_METRICS["matched"] += 1
    else:
        AUDIT_METRICS["mismatched"] += 1
    row = AgentModelInputAudit(
        id=uuid.uuid4().hex,
        run_id=str(run_id),
        thread_id=str(thread_id or "") or None,
        request_id=request_id,
        request_sequence=request_sequence,
        model=str(model or "")[:255],
        source_manifest=_manifest(visible),
        visible_payload=visible,
        payload_hash=payload_hash,
        shadow_hash=shadow_hash,
        match_status=match_status,
        mismatch_detail=(
            None if match_status == "match" else {
                "visible_keys": sorted(visible.keys()),
                "shadow_keys": sorted(shadow.keys()),
            }
        ),
    )
    diagnostics: dict[str, Any] = {"available": False}
    try:
        async def _write() -> None:
            nonlocal diagnostics
            async with factory() as session:
                try:
                    previous_result = await session.execute(
                        select(AgentModelInputAudit)
                        .where(
                            AgentModelInputAudit.run_id == str(run_id),
                            AgentModelInputAudit.request_sequence < request_sequence,
                        )
                        .order_by(AgentModelInputAudit.request_sequence.desc())
                        .limit(1)
                    )
                    previous = previous_result.scalars().first()
                    if previous is not None:
                        diagnostics = _prefix_diagnostics(
                            previous.visible_payload,
                            visible,
                            previous_request_id=str(previous.request_id or ""),
                        )
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "model input prefix lookup skipped run_id=%s sequence=%s",
                        run_id,
                        request_sequence,
                        exc_info=True,
                    )
                session.add(row)
                await session.commit()

        await asyncio.wait_for(_write(), timeout=0.75)
        AUDIT_METRICS["persisted"] += 1
        if match_status == "mismatch":
            logger.warning(
                "model_input_audit_mismatch run_id=%s request_id=%s", run_id, request_id
            )
        return ModelRequestAuditHandle(
            request_id=request_id,
            run_id=str(run_id),
            thread_id=str(thread_id or ""),
            request_sequence=request_sequence,
            model=str(model or "")[:255],
            context_epoch=max(0, int(context_metadata.get("context_epoch") or 0)),
            epoch_reason=str(context_metadata.get("epoch_reason") or "initial")[:64],
            base_prompt_hash=str(context_metadata.get("base_prompt_hash") or "")[:64],
            tool_schema_hash=str(context_metadata.get("tool_schema_hash") or "")[:64],
            state_snapshot_hash=str(context_metadata.get("state_snapshot_hash") or "")[:64],
            prefix_diagnostics=diagnostics,
        )
    except Exception:  # noqa: BLE001
        AUDIT_METRICS["persistence_failed"] += 1
        logger.warning("模型输入审计写入失败（fail-open） run_id=%s", run_id, exc_info=True)
        return None


async def record_model_cache_usage(
    *,
    handle: ModelRequestAuditHandle | None,
    usage: Any,
    transport: str,
) -> bool:
    """Persist normalized provider cache usage for one successful request attempt."""
    if handle is None:
        return False
    factory = runtime_session()
    if factory is None:
        AUDIT_METRICS["cache_persistence_failed"] += 1
        return False
    normalized = normalize_cache_usage(usage)
    row = AgentModelCacheAudit(
        id=uuid.uuid4().hex,
        run_id=handle.run_id,
        thread_id=handle.thread_id or None,
        request_id=handle.request_id,
        request_sequence=handle.request_sequence,
        model=handle.model,
        transport=str(transport or "")[:32],
        context_epoch=handle.context_epoch,
        epoch_reason=handle.epoch_reason,
        base_prompt_hash=handle.base_prompt_hash,
        tool_schema_hash=handle.tool_schema_hash,
        state_snapshot_hash=handle.state_snapshot_hash,
        prefix_diagnostics=dict(handle.prefix_diagnostics or {}),
        input_tokens=normalized.input_tokens,
        output_tokens=normalized.output_tokens,
        cache_read_tokens=normalized.cache_read_tokens,
        cache_miss_tokens=normalized.cache_miss_tokens,
        cache_write_tokens=normalized.cache_write_tokens,
        cache_miss_source=normalized.cache_miss_source,
        usage_schema=normalized.usage_schema,
    )
    try:
        async def _write() -> bool:
            async with factory() as session:
                existing_result = await session.execute(
                    select(AgentModelCacheAudit.id).where(
                        AgentModelCacheAudit.run_id == handle.run_id,
                        AgentModelCacheAudit.request_id == handle.request_id,
                    )
                )
                if existing_result.scalar_one_or_none() is not None:
                    return False
                session.add(row)
                await session.commit()
                return True

        inserted = await asyncio.wait_for(_write(), timeout=0.75)
        if not inserted:
            return True
        AUDIT_METRICS["cache_persisted"] += 1
        logger.info(
            "model_cache_usage request_id=%s run_id=%s epoch=%s reason=%s "
            "base=%s tools=%s state=%s input=%s cache_read=%s cache_miss=%s "
            "cache_write=%s lcp_items=%s",
            handle.request_id,
            handle.run_id,
            handle.context_epoch,
            handle.epoch_reason,
            handle.base_prompt_hash,
            handle.tool_schema_hash,
            handle.state_snapshot_hash,
            normalized.input_tokens,
            normalized.cache_read_tokens,
            normalized.cache_miss_tokens,
            normalized.cache_write_tokens,
            handle.prefix_diagnostics.get("lcp_item_count"),
        )
        return True
    except Exception:  # noqa: BLE001
        AUDIT_METRICS["cache_persistence_failed"] += 1
        logger.warning(
            "模型缓存用量审计写入失败（fail-open） run_id=%s request_id=%s",
            handle.run_id,
            handle.request_id,
            exc_info=True,
        )
        return False


async def delete_for_thread(thread_id: str) -> None:
    """Apply the chat thread's existing best-effort Runtime-PG deletion policy."""
    factory = runtime_session()
    if factory is None or not thread_id:
        return
    try:
        async with factory() as session:
            await session.execute(
                delete(AgentModelInputAudit).where(AgentModelInputAudit.thread_id == str(thread_id))
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("清理会话模型输入审计失败 thread_id=%s", thread_id, exc_info=True)
    try:
        from app.services.agent_harness import model_usage_audit

        await model_usage_audit.delete_for_thread(thread_id)
    except Exception:  # noqa: BLE001
        logger.warning("清理会话模型尝试审计失败 thread_id=%s", thread_id, exc_info=True)
