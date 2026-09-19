"""Fail-open accounting for logical model calls and physical Provider attempts.

The Provider boundary has two different identities:

* a logical call is the semantic operation requested by the Harness;
* an attempt is one physical HTTP request that may be billed independently.

All sequence allocation happens in Runtime PostgreSQL.  No in-process counter is
authoritative, so a worker restart, HITL resume, or a second worker cannot reuse a
Run sequence.  Audit failures never change the model result.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping

from sqlalchemy import delete, select, text, update

from app.core.runtime_db import runtime_session
from app.runtime_models import (
    AgentModelAttemptAudit,
    AgentModelCacheAudit,
    AgentModelInputAudit,
    AgentModelLogicalCall,
    AgentRun,
)

logger = logging.getLogger(__name__)

# 每个审计阶段给数据库写入的预算：超时就放弃这一行、不拖慢模型调用。缺省 0.75s 是按
# 多核机器定的；1 核小机器上负载一高 MySQL 写常常超过它，用量审计整行丢失、日志里一片
# audit_write_failed——所以允许用 MODEL_AUDIT_TIMEOUT_SECONDS 按部署环境调大。
AUDIT_TIMEOUT_SECONDS = float(os.environ.get("MODEL_AUDIT_TIMEOUT_SECONDS") or 0.75)
MODEL_CALL_PURPOSES = frozenset(
    {
        "main_loop",
        "plain_answer",
        "public_preamble",
        "research_commentary",
        "compaction_live",
        "compaction_preflight",
        "compaction_background",
        "router",
        "title",
        "memory_extract",
        "memory_summary",
        "paid_search",
        "browser_digest",
        "subagent_model",
        "workflow_node",
        "acceptance",
        "parent_summary",
        "tool_internal",
    }
)


class ProviderCallPurpose(str, Enum):
    """Stable semantic categories for every model/provider call reachable from main chat."""

    MAIN_LOOP = "main_loop"
    PLAIN_ANSWER = "plain_answer"
    PUBLIC_PREAMBLE = "public_preamble"
    RESEARCH_COMMENTARY = "research_commentary"
    COMPACTION_LIVE = "compaction_live"
    COMPACTION_PREFLIGHT = "compaction_preflight"
    COMPACTION_BACKGROUND = "compaction_background"
    ROUTER = "router"
    TITLE = "title"
    MEMORY_EXTRACT = "memory_extract"
    MEMORY_SUMMARY = "memory_summary"
    PAID_SEARCH = "paid_search"
    BROWSER_DIGEST = "browser_digest"
    SUBAGENT_MODEL = "subagent_model"
    WORKFLOW_NODE = "workflow_node"
    ACCEPTANCE = "acceptance"
    PARENT_SUMMARY = "parent_summary"
    TOOL_INTERNAL = "tool_internal"


class ProviderAttemptOutcome(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NOT_SENT = "not_sent"

AUDIT_METRICS = {
    "logical_started": 0,
    "logical_persisted": 0,
    "logical_failed": 0,
    "attempt_started": 0,
    "attempt_persisted": 0,
    "attempt_failed": 0,
    "attempt_finished": 0,
    "attempt_finish_failed": 0,
    "legacy_cache_persisted": 0,
    "legacy_cache_failed": 0,
}


@dataclass(frozen=True)
class ExternalAttribution:
    """Identifier-only link from an API invocation to runtime audit rows."""

    invocation_id: str
    key_id: str
    app_id: str
    owner_user_id: str
    external_session_id: str = ""


@dataclass(frozen=True)
class ModelLogicalCallHandle:
    logical_call_id: str
    run_id: str
    root_run_id: str
    thread_id: str
    parent_logical_call_id: str
    model: str
    transport: str
    purpose: str
    parent_tool_call_id: str = ""
    endpoint_family: str = ""
    provider_key_fingerprint: str = ""
    purpose_detail: str = ""
    fallback_reason: str = ""
    scope_key: str = ""
    context_epoch: int = 0
    epoch_reason: str = "initial"
    base_prompt_hash: str = ""
    tool_schema_hash: str = ""
    state_snapshot_hash: str = ""
    external_attribution: ExternalAttribution | None = None

    @property
    def id(self) -> str:
        return self.logical_call_id

    @property
    def call_scope_id(self) -> str:
        return self.scope_key


@dataclass(frozen=True)
class ModelAttemptAuditHandle:
    attempt_id: str
    logical_call_id: str
    request_id: str
    run_id: str
    root_run_id: str
    thread_id: str
    run_sequence: int
    attempt_index: int
    model: str
    transport: str
    purpose: str
    execution_segment: str = ""
    endpoint_family: str = ""
    provider_key_fingerprint: str = ""
    purpose_detail: str = ""
    scope_key: str = ""
    context_epoch: int = 0
    epoch_reason: str = "initial"
    base_prompt_hash: str = ""
    tool_schema_hash: str = ""
    state_snapshot_hash: str = ""
    external_attribution: ExternalAttribution | None = None
    semantic_payload_hash: str = ""
    wire_payload_hash: str = ""
    prefix_diagnostics: dict[str, Any] = field(default_factory=dict)
    legacy_compatible: bool = True
    started_monotonic: float = 0.0

    @property
    def id(self) -> str:
        return self.attempt_id

    @property
    def request_sequence(self) -> int:
        """Compatibility name used by the runtime_0015 cache audit."""
        return self.run_sequence

    @property
    def run_request_sequence(self) -> int:
        """Canonical durable name for the database-allocated physical request order."""
        return self.run_sequence

    @property
    def call_scope_id(self) -> str:
        return self.scope_key


@dataclass(frozen=True)
class NormalizedModelUsage:
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    cache_read_tokens: int | None
    cache_miss_tokens: int | None
    cache_write_tokens: int | None
    cache_miss_source: str
    usage_schema: str
    amount_raw: str | None
    provider_amount_unit: str | None

    @property
    def provider_amount_raw(self) -> str | None:
        """Compatibility spelling matching the durable column name."""
        return self.amount_raw

    @property
    def has_token_usage(self) -> bool:
        return any(
            value is not None
            for value in (
                self.input_tokens,
                self.output_tokens,
                self.reasoning_tokens,
                self.cache_read_tokens,
                self.cache_miss_tokens,
                self.cache_write_tokens,
            )
        )

    @property
    def has_usage(self) -> bool:
        return self.has_token_usage or self.amount_raw is not None


@dataclass(frozen=True)
class ModelCallPolicy:
    """Frozen control-plane policy for one logical call; never rendered into model input."""

    max_physical_attempts: int = 1
    sdk_retries: int = 0
    stateful_responses_continuation: bool = False


# Canonical v4 interface names.  Older names stay as compatibility aliases while the
# runtime_0015 readers are retired gradually.
LogicalModelCall = ModelLogicalCallHandle
ProviderAttemptHandle = ModelAttemptAuditHandle
NormalizedProviderUsage = NormalizedModelUsage


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    for method_name in ("model_dump", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                dumped = method()
            except Exception:  # noqa: BLE001
                continue
            if isinstance(dumped, Mapping):
                return {str(key): item for key, item in dumped.items()}
    return {}


def _non_negative_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0, parsed)


def _first_int(*values: Any) -> int | None:
    for value in values:
        parsed = _non_negative_int(value)
        if parsed is not None:
            return parsed
    return None


def _raw_scalar(value: Any) -> str | None:
    """Preserve an already-decoded Provider scalar without numeric conversion."""
    if value is None or isinstance(value, (dict, list, tuple, bool)):
        return None
    rendered = str(value)
    return rendered if rendered else None


def normalize_model_usage(usage: Any) -> NormalizedModelUsage:
    """Normalize supported usage shapes while retaining missing fields as ``None``.

    Cache miss is derived only when both total input and an explicit cached-token
    detail are present.  Amount is copied as text and is never used to infer cost.
    """
    payload = _as_mapping(usage)
    if not any(
        key in payload
        for key in (
            "input_tokens",
            "prompt_tokens",
            "output_tokens",
            "completion_tokens",
            "prompt_cache_hit_tokens",
            "provider_amount",
            "provider_amount_raw",
        )
    ):
        nested = _as_mapping(payload.get("usage"))
        if nested:
            # Some gateways expose token counts in ``usage`` but keep the exact billed
            # amount in a sibling ``usage_metadata`` object.  Flattening to the token
            # object must not silently discard that independently reported fact.
            sibling_metadata = _as_mapping(payload.get("usage_metadata"))
            payload = dict(nested)
            if sibling_metadata and "usage_metadata" not in payload:
                payload["usage_metadata"] = sibling_metadata

    input_details = _as_mapping(payload.get("input_tokens_details"))
    prompt_details = _as_mapping(payload.get("prompt_tokens_details"))
    output_details = _as_mapping(payload.get("output_tokens_details"))
    completion_details = _as_mapping(payload.get("completion_tokens_details"))
    usage_metadata = _as_mapping(payload.get("usage_metadata"))
    amount_metadata = _as_mapping(usage_metadata.get("amount"))

    input_tokens = _first_int(payload.get("input_tokens"), payload.get("prompt_tokens"))
    output_tokens = _first_int(payload.get("output_tokens"), payload.get("completion_tokens"))
    reasoning_tokens = _first_int(
        payload.get("reasoning_tokens"),
        output_details.get("reasoning_tokens"),
        completion_details.get("reasoning_tokens"),
    )

    direct_hit = _non_negative_int(payload.get("prompt_cache_hit_tokens"))
    detail_hit = _first_int(
        input_details.get("cached_tokens"),
        prompt_details.get("cached_tokens"),
    )
    cache_read = direct_hit if direct_hit is not None else detail_hit
    reported_miss = _non_negative_int(payload.get("prompt_cache_miss_tokens"))
    if reported_miss is not None:
        cache_miss = reported_miss
        cache_miss_source = "reported"
    elif input_tokens is not None and cache_read is not None:
        cache_miss = max(0, input_tokens - cache_read)
        cache_miss_source = "derived"
    else:
        cache_miss = None
        cache_miss_source = "unknown"

    cache_write = _first_int(
        payload.get("cache_write_tokens"),
        payload.get("prompt_cache_write_tokens"),
        payload.get("cache_creation_input_tokens"),
        input_details.get("cache_write_tokens"),
        prompt_details.get("cache_write_tokens"),
    )
    if direct_hit is not None or reported_miss is not None:
        usage_schema = "deepseek"
    elif input_details or "input_tokens" in payload:
        usage_schema = "responses"
    elif prompt_details or "prompt_tokens" in payload:
        usage_schema = "chat_completions"
    else:
        usage_schema = "unknown"

    amount_raw = _raw_scalar(
        payload.get(
            "provider_amount_raw",
            payload.get(
                "provider_amount",
                usage_metadata.get("amount", amount_metadata.get("value")),
            ),
        )
    )
    if amount_raw is None and amount_metadata:
        amount_raw = _raw_scalar(amount_metadata.get("value"))
    provider_amount_unit = _raw_scalar(
        payload.get(
            "provider_amount_unit",
            usage_metadata.get("unit", amount_metadata.get("unit")),
        )
    )
    return NormalizedModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cache_read_tokens=cache_read,
        cache_miss_tokens=cache_miss,
        cache_write_tokens=cache_write,
        cache_miss_source=cache_miss_source,
        usage_schema=usage_schema,
        amount_raw=amount_raw,
        provider_amount_unit=provider_amount_unit,
    )


def provider_usage_from_response(response: Any) -> dict[str, Any]:
    """Extract usage from raw Provider JSON or a LangChain/OpenAI response object.

    This helper deliberately returns the Provider fields without normalizing them;
    :func:`finish_attempt` remains the only normalization boundary.
    """
    payload = _as_mapping(response)
    direct_usage = _as_mapping(payload.get("usage"))
    if direct_usage:
        sibling_metadata = _as_mapping(payload.get("usage_metadata"))
        if not sibling_metadata:
            sibling_metadata = _as_mapping(getattr(response, "usage_metadata", None))
        merged = dict(direct_usage)
        if sibling_metadata and "usage_metadata" not in merged:
            merged["usage_metadata"] = sibling_metadata
        return merged
    for attribute in ("usage_metadata", "usage"):
        usage = _as_mapping(getattr(response, attribute, None))
        if usage:
            return usage
    metadata = _as_mapping(getattr(response, "response_metadata", None))
    for key in ("token_usage", "usage"):
        usage = _as_mapping(metadata.get(key))
        if usage:
            return usage
    return {}


def provider_response_id(response: Any) -> str:
    """Return an explicit Provider response id when one is available."""
    payload = _as_mapping(response)
    value = payload.get("id")
    if not value:
        metadata = _as_mapping(getattr(response, "response_metadata", None))
        value = metadata.get("id") or metadata.get("response_id")
    return str(value or "")[:128]


def _audit_helpers():
    # Lazy import avoids making the legacy module import this service at module load.
    from app.services.chat import model_input_audit

    return model_input_audit


def _canonical_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_text(value).encode("utf-8")).hexdigest()


def _sanitize_payload(value: Any) -> dict[str, Any]:
    sanitized = _audit_helpers()._sanitize(value)
    return sanitized if isinstance(sanitized, dict) else {}


_PUBLIC_CONTROL_KEYS = frozenset({
    "stream",
    "max_tokens",
    "max_output_tokens",
    "temperature",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "parallel_tool_calls",
    "store",
    "reasoning",
    "thinking",
    "tool_choice",
    "response_format",
})


def _safe_control_value(key: str, value: Any) -> Any:
    """Keep only public, bounded request switches; never arbitrary prompt strings."""

    if key == "reasoning":
        payload = _as_mapping(value)
        return {
            name: str(payload[name])[:32]
            for name in ("effort", "summary")
            if payload.get(name) is not None
        }
    if key == "thinking":
        payload = _as_mapping(value)
        return {"type": str(payload.get("type"))[:32]} if payload.get("type") is not None else {}
    if key == "tool_choice":
        if isinstance(value, str):
            return value[:64]
        payload = _as_mapping(value)
        function = _as_mapping(payload.get("function"))
        return {
            "type": str(payload.get("type") or "")[:32],
            "function_name": str(function.get("name") or "")[:128],
        }
    if key == "response_format":
        payload = _as_mapping(value)
        return {"type": str(payload.get("type") or "")[:64]}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:64]


def _item_kind(item: Mapping[str, Any]) -> str:
    item_type = str(item.get("type") or "").strip().lower()
    role = str(item.get("role") or "").strip().lower()
    if item_type == "function_call_output":
        return "tool_result"
    if item_type == "function_call":
        return "tool_call"
    if role in {"system", "developer"}:
        return "system_prompt"
    if role == "tool":
        return "tool_result"
    if role == "assistant" and item.get("tool_calls"):
        return "tool_call"
    return item_type or role or "message"


def _fingerprint_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Create a content-free structural projection for the general usage ledger."""

    normalized = dict(payload or {})
    items = _model_items(normalized)
    item_rows: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        canonical = _canonical_text(item)
        item_rows.append({
            "index": index,
            "kind": _item_kind(item),
            "role": str(item.get("role") or "")[:32],
            "type": str(item.get("type") or "")[:64],
            "source_id_hash": hashlib.sha256(str(
                item.get("tool_call_id")
                or item.get("call_id")
                or item.get("id")
                or index
            ).encode("utf-8")).hexdigest(),
            "content_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "canonical_chars": len(canonical),
        })

    tools: list[dict[str, Any]] = []
    for index, raw_tool in enumerate(normalized.get("tools") or []):
        if not isinstance(raw_tool, Mapping):
            continue
        tool = dict(raw_tool)
        function = _as_mapping(tool.get("function")) or tool
        canonical = _canonical_text(tool)
        tools.append({
            "index": index,
            "name": str(function.get("name") or "")[:128],
            "schema_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "canonical_chars": len(canonical),
        })

    item_chars = 2 + sum(row["canonical_chars"] for row in item_rows)
    if item_rows:
        item_chars += len(item_rows) - 1
    controls = {
        key: _safe_control_value(key, normalized[key])
        for key in sorted(_PUBLIC_CONTROL_KEYS)
        if key in normalized
    }
    return {
        "schema": "provider_payload_fingerprint_v1",
        "controls": controls,
        "items": item_rows,
        "tools": tools,
        "item_count": len(item_rows),
        "item_canonical_chars": item_chars,
        "tool_schema_hash": _canonical_hash(tools),
    }


def _fingerprint_items(value: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    if value.get("schema") in {"provider_payload_fingerprint_v1", "legacy_manifest_v1"}:
        raw = value.get("items")
        return [
            dict(item)
            for item in (raw or [])
            if isinstance(item, Mapping) and str(item.get("kind") or "") != "tool_schema"
        ]
    return list(_fingerprint_payload(value).get("items") or [])


def _fingerprint_prefix_diagnostics(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    *,
    previous_request_id: str = "",
) -> dict[str, Any]:
    if not isinstance(previous, Mapping):
        return {"available": False}
    previous_items = _fingerprint_items(previous)
    current_items = _fingerprint_items(current)
    item_lcp = 0
    for before, after in zip(previous_items, current_items):
        if str(before.get("content_hash") or "") != str(after.get("content_hash") or ""):
            break
        item_lcp += 1

    def _item_chars(payload: Mapping[str, Any], items: list[dict[str, Any]]) -> int:
        explicit = _non_negative_int(payload.get("item_canonical_chars"))
        if explicit is not None:
            return explicit
        lengths = [_non_negative_int(item.get("canonical_chars")) or 0 for item in items]
        return 2 + sum(lengths) + max(0, len(lengths) - 1)

    previous_chars = _item_chars(previous, previous_items)
    current_chars = _item_chars(current, current_items)
    exact_char_lcp = item_lcp == min(len(previous_items), len(current_items))
    if exact_char_lcp:
        if len(previous_items) == len(current_items):
            char_lcp = min(previous_chars, current_chars)
        else:
            shorter_chars = previous_chars if len(previous_items) < len(current_items) else current_chars
            char_lcp = max(0, shorter_chars - 1)
    else:
        equal_chars = sum(
            _non_negative_int(item.get("canonical_chars")) or 0
            for item in current_items[:item_lcp]
        )
        # Opening bracket plus the comma following every complete equal item.
        char_lcp = 1 + equal_chars + item_lcp

    divergence = current_items[item_lcp] if item_lcp < len(current_items) else {}
    previous_tool_hash = str(previous.get("tool_schema_hash") or "")
    current_tool_hash = str(current.get("tool_schema_hash") or "")
    return {
        "available": True,
        "previous_request_id": str(previous_request_id or ""),
        "previous_item_count": len(previous_items),
        "current_item_count": len(current_items),
        "lcp_item_count": item_lcp,
        "previous_canonical_chars": previous_chars,
        "current_canonical_chars": current_chars,
        "lcp_canonical_chars": char_lcp,
        "lcp_canonical_chars_exact": exact_char_lcp,
        "first_divergence_index": item_lcp,
        "first_divergence_kind": str(divergence.get("kind") or "end_of_input"),
        "tool_schema_equal": previous_tool_hash == current_tool_hash,
    }


def _model_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("input") if isinstance(payload.get("input"), list) else payload.get("messages")
    return [item for item in (raw or []) if isinstance(item, dict)]


def _payload_shape(payload: dict[str, Any]) -> tuple[int, int]:
    items = _model_items(payload)
    return len(items), len(_canonical_text(items))


def _context_fields(context_metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    metadata = dict(context_metadata or {})
    return {
        "context_epoch": max(0, int(metadata.get("context_epoch") or 0)),
        "epoch_reason": str(metadata.get("epoch_reason") or "initial")[:64],
        "base_prompt_hash": str(metadata.get("base_prompt_hash") or "")[:64],
        "tool_schema_hash": str(metadata.get("tool_schema_hash") or "")[:64],
        "state_snapshot_hash": str(metadata.get("state_snapshot_hash") or "")[:64],
    }


def _validated_purpose(purpose: str) -> str | None:
    raw = purpose.value if isinstance(purpose, ProviderCallPurpose) else purpose
    normalized = str(raw or "main_loop").strip()
    return normalized if normalized in MODEL_CALL_PURPOSES else None


def _default_scope_key(
    *, purpose: str, model: str, transport: str, context_epoch: int
) -> str:
    return f"{purpose}|{model}|{transport}|{context_epoch}"[:255]


def provider_key_fingerprint(api_key: str) -> str:
    """Return a stable non-reversible key identifier; the raw secret is never persisted."""

    value = str(api_key or "")
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def _default_endpoint_family(transport: str) -> str:
    normalized = str(transport or "").strip().lower()
    if "response" in normalized:
        return "responses"
    if "chat" in normalized or "anthropic" in normalized:
        return "chat_completions"
    return normalized[:64]


def _safe_error_detail(value: str) -> str | None:
    """Store diagnostic identity without copying Provider bodies or model text."""

    raw = str(value or "")
    if not raw:
        return None
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"sha256:{digest};chars:{len(raw)}"


def _audit_write_failed(stage: str, **identifiers: Any) -> None:
    safe = " ".join(
        f"{key}={str(value or '')[:128]}"
        for key, value in sorted(identifiers.items())
    )
    # 超时是「库慢了」这一种已知情况，一行说清就够；别的异常才带堆栈。
    exc = sys.exc_info()[1]
    if isinstance(exc, asyncio.TimeoutError):
        logger.warning("audit_write_failed stage=%s reason=timeout(%.2fs) %s", stage, AUDIT_TIMEOUT_SECONDS, safe)
        return
    logger.warning("audit_write_failed stage=%s %s", stage, safe, exc_info=True)


async def _resolve_run_context(
    session: Any,
    run_id: str,
    supplied_root: str,
    supplied_parent_tool_call_id: str,
) -> tuple[str, str]:
    try:
        result = await session.execute(
            select(AgentRun.root_run_id, AgentRun.parent_tool_call_id).where(AgentRun.id == run_id)
        )
        row = result.first()
        stored_root = str(row[0] or "") if row is not None else ""
        stored_parent_tool = str(row[1] or "") if row is not None else ""
        return (
            str(supplied_root or stored_root or run_id)[:64],
            str(supplied_parent_tool_call_id or stored_parent_tool)[:64],
        )
    except Exception:  # noqa: BLE001
        logger.debug("model usage audit root lookup skipped run_id=%s", run_id, exc_info=True)
        return (
            str(supplied_root or run_id)[:64],
            str(supplied_parent_tool_call_id or "")[:64],
        )


async def _allocate_run_sequence(session: Any, run_id: str) -> int:
    result = await session.execute(
        text(
            """INSERT INTO agent_model_run_counters (run_id, last_sequence, updated_at)
            VALUES (:run_id, 1, NOW())
            ON CONFLICT (run_id) DO UPDATE
            SET last_sequence = agent_model_run_counters.last_sequence + 1,
                updated_at = NOW()
            RETURNING last_sequence"""
        ),
        {"run_id": run_id},
    )
    return int(result.scalar_one())


async def _allocate_attempt_index(
    session: Any, *, logical_call_id: str, run_sequence: int
) -> int:
    result = await session.execute(
        text(
            """UPDATE agent_model_logical_calls
            SET attempt_count = attempt_count + 1,
                status = 'running',
                first_run_sequence = COALESCE(first_run_sequence, :run_sequence),
                last_run_sequence = :run_sequence
            WHERE id = :logical_call_id
            RETURNING attempt_count"""
        ),
        {"logical_call_id": logical_call_id, "run_sequence": run_sequence},
    )
    value = result.scalar_one_or_none()
    if value is None:
        raise LookupError(f"logical call not found: {logical_call_id}")
    return int(value)


async def begin_logical_call(
    *,
    run_id: str,
    thread_id: str = "",
    root_run_id: str = "",
    parent_logical_call_id: str = "",
    parent_tool_call_id: str = "",
    model: str,
    transport: str,
    purpose: str = "main_loop",
    purpose_detail: str = "",
    endpoint_family: str = "",
    fallback_reason: str = "",
    provider_api_key: str = "",
    provider_key_id: str = "",
    context_metadata: Mapping[str, Any] | None = None,
    scope_key: str = "",
    call_scope_id: str = "",
    external_attribution: ExternalAttribution | None = None,
) -> ModelLogicalCallHandle | None:
    """Create a logical call. Invalid purpose and all persistence failures fail open."""
    AUDIT_METRICS["logical_started"] += 1
    if not parent_logical_call_id or not parent_tool_call_id or not root_run_id:
        try:
            from app.services.chat.tools.base import current_tool_context

            tool_context = current_tool_context()
        except Exception:  # noqa: BLE001 - context enrichment is fail-open
            tool_context = None
        if tool_context is not None:
            parent_logical_call_id = (
                parent_logical_call_id
                or str(tool_context.parent_logical_call_id or "")
            )
            parent_tool_call_id = parent_tool_call_id or str(tool_context.call_id or "")
            root_run_id = root_run_id or str(tool_context.root_run_id or "")
    run_id = str(run_id or "")[:64]
    normalized_purpose = _validated_purpose(purpose)
    factory = runtime_session()
    if not run_id or normalized_purpose is None or factory is None:
        AUDIT_METRICS["logical_failed"] += 1
        if normalized_purpose is None:
            logger.warning("model usage audit rejected unknown purpose=%r", purpose)
        return None
    context = _context_fields(context_metadata)
    logical_call_id = f"lg_{uuid.uuid4().hex}"
    resolved_endpoint = str(endpoint_family or _default_endpoint_family(transport))[:64]
    resolved_key_fingerprint = str(
        provider_key_id or provider_key_fingerprint(provider_api_key)
    )[:64]

    try:
        async def _write() -> ModelLogicalCallHandle:
            async with factory() as session:
                resolved_root, resolved_parent_tool = await _resolve_run_context(
                    session,
                    run_id,
                    root_run_id,
                    parent_tool_call_id,
                )
                resolved_scope = str(call_scope_id or scope_key or _default_scope_key(
                    purpose=normalized_purpose,
                    model=str(model or ""),
                    transport=str(transport or ""),
                    context_epoch=context["context_epoch"],
                ))[:255]
                session.add(AgentModelLogicalCall(
                    id=logical_call_id,
                    run_id=run_id,
                    root_run_id=resolved_root,
                    thread_id=str(thread_id or "")[:64] or None,
                    parent_logical_call_id=str(parent_logical_call_id or "")[:64] or None,
                    parent_tool_call_id=resolved_parent_tool or None,
                    model=str(model or "")[:255],
                    transport=str(transport or "")[:32],
                    endpoint_family=resolved_endpoint,
                    provider_key_fingerprint=resolved_key_fingerprint or None,
                    external_invocation_id=(external_attribution.invocation_id[:64] if external_attribution else None),
                    external_key_id=(external_attribution.key_id[:64] if external_attribution else None),
                    external_app_id=(external_attribution.app_id[:64] if external_attribution else None),
                    external_owner_user_id=(external_attribution.owner_user_id[:64] if external_attribution else None),
                    external_session_id=(external_attribution.external_session_id[:64] if external_attribution else None),
                    purpose=normalized_purpose,
                    purpose_detail=str(purpose_detail or "") or None,
                    fallback_reason=str(fallback_reason or "")[:128] or None,
                    scope_key=resolved_scope,
                    status="started",
                    **context,
                ))
                await session.commit()
                return ModelLogicalCallHandle(
                    logical_call_id=logical_call_id,
                    run_id=run_id,
                    root_run_id=resolved_root,
                    thread_id=str(thread_id or "")[:64],
                    parent_logical_call_id=str(parent_logical_call_id or "")[:64],
                    model=str(model or "")[:255],
                    transport=str(transport or "")[:32],
                    purpose=normalized_purpose,
                    parent_tool_call_id=resolved_parent_tool,
                    endpoint_family=resolved_endpoint,
                    provider_key_fingerprint=resolved_key_fingerprint,
                    purpose_detail=str(purpose_detail or ""),
                    fallback_reason=str(fallback_reason or "")[:128],
                    scope_key=resolved_scope,
                    external_attribution=external_attribution,
                    **context,
                )

        handle = await asyncio.wait_for(_write(), timeout=AUDIT_TIMEOUT_SECONDS)
        AUDIT_METRICS["logical_persisted"] += 1
        return handle
    except Exception:  # noqa: BLE001
        AUDIT_METRICS["logical_failed"] += 1
        _audit_write_failed("logical_start", run_id=run_id, purpose=normalized_purpose)
        return None


async def begin_attempt(
    logical_call: ModelLogicalCallHandle | None,
    *,
    wire_payload: Mapping[str, Any],
    logical_payload: Mapping[str, Any] | None = None,
    shadow_payload: Mapping[str, Any] | None = None,
    attempt_kind: str = "initial",
    retry_of_attempt_id: str = "",
    previous_response_id: str = "",
    request_id: str = "",
    execution_segment: str = "",
    legacy_compatible: bool | None = None,
) -> ModelAttemptAuditHandle | None:
    """Allocate and persist one physical Provider attempt in a single transaction."""
    AUDIT_METRICS["attempt_started"] += 1
    factory = runtime_session()
    if logical_call is None or factory is None:
        AUDIT_METRICS["attempt_failed"] += 1
        return None

    # runtime_0015 is the historical main-loop cache fact source.  Auxiliary calls belong only
    # in the v4 total ledger; otherwise a title/router/embedding request silently contaminates
    # the old "main loop cache hit" denominator.  Explicit callers may still override this for
    # backfill/compatibility tests.
    resolved_legacy_compatible = (
        logical_call.purpose == ProviderCallPurpose.MAIN_LOOP.value
        if legacy_compatible is None
        else bool(legacy_compatible)
    )

    # Hash and fingerprint the actual Provider-visible payload.  The complete payload only lives
    # in memory: the durable rows below receive content-free fingerprints.  Sanitizing before
    # hashing would make two physical requests with different assistant reasoning/encrypted
    # continuation items look identical, which is incorrect for retry and prefix attribution.
    wire = dict(wire_payload or {})
    logical = dict(logical_payload or wire_payload or {})
    shadow = dict(shadow_payload or wire_payload or {})
    logical_hash = _canonical_hash(logical)
    wire_hash = _canonical_hash(wire)
    shadow_hash = _canonical_hash(shadow)
    wire_fingerprint = _fingerprint_payload(wire)
    logical_fingerprint = _fingerprint_payload(logical)
    match_status = "match" if wire_hash == shadow_hash else "mismatch"
    mismatch_detail = None if match_status == "match" else {
        "wire_keys": sorted(wire),
        "shadow_keys": sorted(shadow),
    }
    logical_count, logical_chars = _payload_shape(logical)
    wire_count, wire_chars = _payload_shape(wire)
    attempt_id = f"at_{uuid.uuid4().hex}"
    request_id = str(request_id or uuid.uuid4().hex)[:64]
    if not execution_segment:
        try:
            from app.services.chat.tools.base import current_tool_context

            tool_context = current_tool_context()
            execution_segment = (
                str(tool_context.execution_segment or "")
                if tool_context is not None
                else ""
            )
        except Exception:  # noqa: BLE001 - context enrichment is fail-open
            execution_segment = ""
    resolved_segment = str(execution_segment or logical_call.logical_call_id)[:64]
    started_monotonic = time.monotonic()

    try:
        async def _write() -> ModelAttemptAuditHandle:
            async with factory() as session:
                run_sequence = await _allocate_run_sequence(session, logical_call.run_id)
                attempt_index = await _allocate_attempt_index(
                    session,
                    logical_call_id=logical_call.logical_call_id,
                    run_sequence=run_sequence,
                )
                previous_result = await session.execute(
                    select(AgentModelAttemptAudit)
                    .where(
                        AgentModelAttemptAudit.run_id == logical_call.run_id,
                        AgentModelAttemptAudit.scope_key == logical_call.scope_key,
                        AgentModelAttemptAudit.model == logical_call.model,
                        AgentModelAttemptAudit.transport == logical_call.transport,
                        AgentModelAttemptAudit.run_sequence < run_sequence,
                    )
                    .order_by(AgentModelAttemptAudit.run_sequence.desc())
                    .limit(1)
                )
                previous = previous_result.scalars().first()
                prefix = _fingerprint_prefix_diagnostics(
                    previous.wire_payload if previous is not None else None,
                    wire_fingerprint,
                    previous_request_id=(str(previous.request_id or "") if previous else ""),
                )
                row = AgentModelAttemptAudit(
                    id=attempt_id,
                    logical_call_id=logical_call.logical_call_id,
                    retry_of_attempt_id=str(retry_of_attempt_id or "")[:64] or None,
                    prefix_predecessor_attempt_id=(str(previous.id) if previous else None),
                    run_id=logical_call.run_id,
                    root_run_id=logical_call.root_run_id,
                    thread_id=logical_call.thread_id or None,
                    request_id=request_id,
                    run_sequence=run_sequence,
                    legacy_request_sequence=(run_sequence if resolved_legacy_compatible else None),
                    attempt_index=attempt_index,
                    attempt_kind=str(attempt_kind or "initial")[:64],
                    execution_segment=resolved_segment,
                    model=logical_call.model,
                    transport=logical_call.transport,
                    endpoint_family=logical_call.endpoint_family,
                    provider_key_fingerprint=(logical_call.provider_key_fingerprint or None),
                    external_invocation_id=(logical_call.external_attribution.invocation_id[:64] if logical_call.external_attribution else None),
                    external_key_id=(logical_call.external_attribution.key_id[:64] if logical_call.external_attribution else None),
                    external_app_id=(logical_call.external_attribution.app_id[:64] if logical_call.external_attribution else None),
                    external_owner_user_id=(logical_call.external_attribution.owner_user_id[:64] if logical_call.external_attribution else None),
                    external_session_id=(logical_call.external_attribution.external_session_id[:64] if logical_call.external_attribution else None),
                    purpose=logical_call.purpose,
                    purpose_detail=logical_call.purpose_detail or None,
                    scope_key=logical_call.scope_key,
                    context_epoch=logical_call.context_epoch,
                    epoch_reason=logical_call.epoch_reason,
                    base_prompt_hash=logical_call.base_prompt_hash,
                    tool_schema_hash=logical_call.tool_schema_hash,
                    state_snapshot_hash=logical_call.state_snapshot_hash,
                    source_manifest=_audit_helpers()._manifest(wire),
                    logical_payload=logical_fingerprint,
                    wire_payload=wire_fingerprint,
                    logical_payload_hash=logical_hash,
                    wire_payload_hash=wire_hash,
                    shadow_hash=shadow_hash,
                    match_status=match_status,
                    mismatch_detail=mismatch_detail,
                    prefix_diagnostics=prefix,
                    logical_item_count=logical_count,
                    wire_item_count=wire_count,
                    logical_canonical_chars=logical_chars,
                    wire_canonical_chars=wire_chars,
                    previous_response_id=str(previous_response_id or "")[:128] or None,
                    terminal_status="started",
                )
                session.add(row)
                if resolved_legacy_compatible:
                    session.add(AgentModelInputAudit(
                        id=uuid.uuid4().hex,
                        run_id=logical_call.run_id,
                        thread_id=logical_call.thread_id or None,
                        request_id=request_id,
                        request_sequence=run_sequence,
                        model=logical_call.model,
                        source_manifest=_audit_helpers()._manifest(wire),
                        visible_payload=wire_fingerprint,
                        payload_hash=wire_hash,
                        shadow_hash=shadow_hash,
                        match_status=match_status,
                        mismatch_detail=mismatch_detail,
                    ))
                await session.commit()
                return ModelAttemptAuditHandle(
                    attempt_id=attempt_id,
                    logical_call_id=logical_call.logical_call_id,
                    request_id=request_id,
                    run_id=logical_call.run_id,
                    root_run_id=logical_call.root_run_id,
                    thread_id=logical_call.thread_id,
                    run_sequence=run_sequence,
                    attempt_index=attempt_index,
                    execution_segment=resolved_segment,
                    model=logical_call.model,
                    transport=logical_call.transport,
                    purpose=logical_call.purpose,
                    endpoint_family=logical_call.endpoint_family,
                    provider_key_fingerprint=logical_call.provider_key_fingerprint,
                    purpose_detail=logical_call.purpose_detail,
                    scope_key=logical_call.scope_key,
                    context_epoch=logical_call.context_epoch,
                    epoch_reason=logical_call.epoch_reason,
                    base_prompt_hash=logical_call.base_prompt_hash,
                    tool_schema_hash=logical_call.tool_schema_hash,
                    state_snapshot_hash=logical_call.state_snapshot_hash,
                    external_attribution=logical_call.external_attribution,
                    semantic_payload_hash=logical_hash,
                    wire_payload_hash=wire_hash,
                    prefix_diagnostics=prefix,
                    legacy_compatible=resolved_legacy_compatible,
                    started_monotonic=started_monotonic,
                )

        handle = await asyncio.wait_for(_write(), timeout=AUDIT_TIMEOUT_SECONDS)
        AUDIT_METRICS["attempt_persisted"] += 1
        return handle
    except Exception:  # noqa: BLE001
        AUDIT_METRICS["attempt_failed"] += 1
        _audit_write_failed(
            "attempt_start",
            logical_call_id=logical_call.logical_call_id,
            run_id=logical_call.run_id,
        )
        return None


_KNOWN_NO_CHARGE_STATUSES = frozenset(
    {"not_sent", "cancelled_before_send", "validation_failed", "local_rejected"}
)


async def finish_attempt(
    handle: ModelAttemptAuditHandle | None,
    *,
    terminal_status: str,
    usage: Any = None,
    response_id: str = "",
    previous_response_id: str = "",
    provider_event_seen: bool = False,
    partial_text_seen: bool = False,
    terminal_seen: bool = False,
    trusted_usage: bool | None = None,
    http_status: int | None = None,
    error_code: str = "",
    error_detail: str = "",
    unknown_provider_charge: bool | None = None,
    billed_but_not_committed: bool | None = None,
    committed: bool | None = None,
) -> bool:
    """Finalize exactly one attempt and, when requested, write the 0015 cache row."""
    if handle is None:
        return False
    factory = runtime_session()
    if factory is None:
        AUDIT_METRICS["attempt_finish_failed"] += 1
        return False
    raw_status = terminal_status.value if isinstance(terminal_status, ProviderAttemptOutcome) else terminal_status
    status = str(raw_status or "unknown")[:32]
    normalized = normalize_model_usage(usage)
    inferred_trusted_usage = (
        bool(normalized.has_usage) if trusted_usage is None else bool(trusted_usage)
    )
    inferred_committed = (status == "completed") if committed is None else bool(committed)
    inferred_unknown = (
        (not normalized.has_usage or not inferred_trusted_usage)
        and status not in _KNOWN_NO_CHARGE_STATUSES
        if unknown_provider_charge is None
        else bool(unknown_provider_charge)
    )
    inferred_billed = (
        normalized.has_usage and not inferred_committed
        if billed_but_not_committed is None
        else bool(billed_but_not_committed)
    )
    latency_ms = (
        max(0, int((time.monotonic() - handle.started_monotonic) * 1000))
        if handle.started_monotonic > 0
        else None
    )

    try:
        async def _write() -> tuple[bool, bool]:
            async with factory() as session:
                result = await session.execute(
                    update(AgentModelAttemptAudit)
                    .where(
                        AgentModelAttemptAudit.id == handle.attempt_id,
                        AgentModelAttemptAudit.completed_at.is_(None),
                    )
                    .values(
                        response_id=str(response_id or "")[:128] or None,
                        previous_response_id=str(previous_response_id or "")[:128] or None,
                        provider_event_seen=bool(provider_event_seen),
                        partial_text_seen=bool(partial_text_seen),
                        terminal_seen=bool(terminal_seen),
                        trusted_usage=inferred_trusted_usage,
                        terminal_status=status,
                        http_status=http_status,
                        error_code=str(error_code or "")[:128] or None,
                        error_detail=_safe_error_detail(error_detail),
                        input_tokens=normalized.input_tokens,
                        output_tokens=normalized.output_tokens,
                        reasoning_tokens=normalized.reasoning_tokens,
                        cache_read_tokens=normalized.cache_read_tokens,
                        cache_miss_tokens=normalized.cache_miss_tokens,
                        cache_write_tokens=normalized.cache_write_tokens,
                        cache_miss_source=normalized.cache_miss_source,
                        usage_schema=normalized.usage_schema,
                        provider_amount_raw=normalized.amount_raw,
                        provider_amount_unit=normalized.provider_amount_unit,
                        unknown_provider_charge=inferred_unknown,
                        billed_but_not_committed=inferred_billed,
                        committed=inferred_committed,
                        latency_ms=latency_ms,
                        completed_at=text("NOW()"),
                    )
                )
                updated = bool(getattr(result, "rowcount", 0))
                legacy_inserted = False
                if (
                    updated
                    and handle.legacy_compatible
                    and inferred_trusted_usage
                    and normalized.has_token_usage
                ):
                    existing_result = await session.execute(
                        select(AgentModelCacheAudit.id).where(
                            AgentModelCacheAudit.run_id == handle.run_id,
                            AgentModelCacheAudit.request_id == handle.request_id,
                        )
                    )
                    if existing_result.scalar_one_or_none() is None:
                        session.add(AgentModelCacheAudit(
                            id=uuid.uuid4().hex,
                            run_id=handle.run_id,
                            thread_id=handle.thread_id or None,
                            request_id=handle.request_id,
                            request_sequence=handle.run_sequence,
                            model=handle.model,
                            transport=handle.transport,
                            context_epoch=handle.context_epoch,
                            epoch_reason=handle.epoch_reason,
                            base_prompt_hash=handle.base_prompt_hash,
                            tool_schema_hash=handle.tool_schema_hash,
                            state_snapshot_hash=handle.state_snapshot_hash,
                            prefix_diagnostics=dict(handle.prefix_diagnostics or {}),
                            input_tokens=normalized.input_tokens or 0,
                            output_tokens=normalized.output_tokens or 0,
                            cache_read_tokens=normalized.cache_read_tokens,
                            cache_miss_tokens=normalized.cache_miss_tokens,
                            cache_write_tokens=normalized.cache_write_tokens,
                            cache_miss_source=normalized.cache_miss_source,
                            usage_schema=normalized.usage_schema,
                        ))
                        legacy_inserted = True
                await session.commit()
                return updated, legacy_inserted

        updated, legacy_inserted = await asyncio.wait_for(
            _write(), timeout=AUDIT_TIMEOUT_SECONDS
        )
        if legacy_inserted:
            AUDIT_METRICS["legacy_cache_persisted"] += 1
        AUDIT_METRICS["attempt_finished"] += 1
        logger.info(
            "model_attempt_usage request_id=%s attempt_id=%s run_id=%s sequence=%s "
            "purpose=%s status=%s epoch=%s base=%s tools=%s state=%s "
            "input=%s output=%s reasoning=%s cache_read=%s cache_miss=%s "
            "cache_write=%s amount=%s amount_unit=%s unknown_charge=%s "
            "billed_not_committed=%s committed=%s latency_ms=%s lcp_items=%s "
            "semantic_hash=%s wire_hash=%s",
            handle.request_id,
            handle.attempt_id,
            handle.run_id,
            handle.run_sequence,
            handle.purpose,
            status,
            handle.context_epoch,
            handle.base_prompt_hash,
            handle.tool_schema_hash,
            handle.state_snapshot_hash,
            normalized.input_tokens,
            normalized.output_tokens,
            normalized.reasoning_tokens,
            normalized.cache_read_tokens,
            normalized.cache_miss_tokens,
            normalized.cache_write_tokens,
            normalized.amount_raw,
            normalized.provider_amount_unit,
            inferred_unknown,
            inferred_billed,
            inferred_committed,
            latency_ms,
            handle.prefix_diagnostics.get("lcp_item_count"),
            handle.semantic_payload_hash,
            handle.wire_payload_hash,
        )
        # An already-finalized row is an idempotent success; no second cache row is added.
        return True
    except Exception:  # noqa: BLE001
        AUDIT_METRICS["attempt_finish_failed"] += 1
        _audit_write_failed(
            "attempt_finish",
            attempt_id=handle.attempt_id,
            run_id=handle.run_id,
        )
        return False


async def finish_logical_call(
    handle: ModelLogicalCallHandle | None,
    *,
    terminal_status: str,
    selected_attempt_id: str = "",
    committed: bool | None = None,
) -> bool:
    """Finalize a semantic call after the Harness chooses (or rejects) an attempt."""
    if handle is None:
        return False
    factory = runtime_session()
    if factory is None:
        return False
    raw_status = terminal_status.value if isinstance(terminal_status, ProviderAttemptOutcome) else terminal_status
    status = str(raw_status or "unknown")[:32]
    if committed is False and status == "completed":
        status = "not_committed"
    try:
        async def _write() -> None:
            async with factory() as session:
                await session.execute(
                    update(AgentModelLogicalCall)
                    .where(AgentModelLogicalCall.id == handle.logical_call_id)
                    .values(
                        status=status,
                        selected_attempt_id=str(selected_attempt_id or "")[:64] or None,
                        completed_at=text("NOW()"),
                    )
                )
                await session.commit()

        await asyncio.wait_for(_write(), timeout=AUDIT_TIMEOUT_SECONDS)
        return True
    except Exception:  # noqa: BLE001
        _audit_write_failed(
            "logical_finish",
            logical_call_id=handle.logical_call_id,
            run_id=handle.run_id,
        )
        return False


async def has_recent_failed_logical_call(
    *,
    run_id: str,
    purpose: str,
    purpose_detail: str,
    within_seconds: float,
) -> bool:
    """Best-effort cross-worker dedupe lookup for an already-paid failed logical call."""

    factory = runtime_session()
    normalized_purpose = _validated_purpose(purpose)
    if factory is None or not run_id or normalized_purpose is None or not purpose_detail:
        return False
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        seconds=max(0.0, float(within_seconds or 0.0))
    )
    try:
        async with factory() as session:
            result = await session.execute(
                select(AgentModelLogicalCall.status)
                .where(
                    AgentModelLogicalCall.run_id == str(run_id)[:64],
                    AgentModelLogicalCall.purpose == normalized_purpose,
                    AgentModelLogicalCall.purpose_detail == str(purpose_detail),
                    AgentModelLogicalCall.completed_at.is_not(None),
                    AgentModelLogicalCall.completed_at >= cutoff,
                )
                .order_by(AgentModelLogicalCall.completed_at.desc())
                .limit(1)
            )
            return str(result.scalar_one_or_none() or "") in {
                "failed",
                "incomplete",
                "not_committed",
            }
    except Exception:  # noqa: BLE001
        _audit_write_failed(
            "logical_failure_dedupe_read",
            run_id=run_id,
            purpose=normalized_purpose,
        )
        return False


async def delete_for_thread(thread_id: str) -> None:
    """Best-effort cleanup of usage, full tool data, and projection history for a Thread."""
    factory = runtime_session()
    if factory is None or not thread_id:
        return
    try:
        from app.runtime_models import AgentThreadContextLedger, AgentToolResultBlob

        async with factory() as session:
            await session.execute(
                delete(AgentModelLogicalCall).where(
                    AgentModelLogicalCall.thread_id == str(thread_id)
                )
            )
            await session.execute(
                delete(AgentToolResultBlob).where(
                    AgentToolResultBlob.thread_id == str(thread_id)
                )
            )
            await session.execute(
                delete(AgentThreadContextLedger).where(
                    AgentThreadContextLedger.thread_id == str(thread_id)
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.warning(
            "thread model usage audit cleanup failed thread_id=%s", thread_id, exc_info=True
        )


# Compatibility spelling for callers that used the original plan's shorter name.
normalize_usage = normalize_model_usage
