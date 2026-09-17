"""Tool-result projection and externalization with explicit, durable handles."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from app.services.platform.token_estimator import estimate_tokens

from .contracts import (
    AppliedToolResultPolicy,
    ResultSizePolicy,
    ToolObservation,
    ToolSpec,
)
from .tool_result_store import DurableToolResultRef, DurableToolResultStore


SERIALIZATION_RESERVE_RATIO = 0.20


def most_restrictive_result_policy(*policies: ResultSizePolicy | None) -> ResultSizePolicy:
    """Merge tool/plugin/admin limits without allowing a later layer to widen a prior limit."""

    active = [policy for policy in policies if policy is not None]
    if not active:
        return ResultSizePolicy()
    token_limits = [
        policy.inline_token_limit
        for policy in active
        if policy.inline_token_limit is not None
    ]
    return ResultSizePolicy(
        inline_chars=min(policy.inline_chars for policy in active),
        inline_token_limit=min(token_limits) if token_limits else None,
        persist_full_result=all(policy.persist_full_result for policy in active),
    )


class ResultStore(Protocol):
    async def put(self, *, run_id: str, call_id: str, content: str) -> str: ...
    async def get(self, *, run_id: str, handle: str, offset: int, limit: int) -> str: ...


class ProjectedToolResult(BaseModel):
    """The provider-visible value plus the exact policy applied to it."""

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())

    model_content: str
    result_handle: str | None = None
    full_available: bool = False
    unavailable_reason: str | None = None
    applied_policy: AppliedToolResultPolicy


def _prefix_within_tokens(text: str, limit: int) -> str:
    """Return the longest character prefix whose estimated token count is within ``limit``."""

    if limit <= 0 or not text:
        return ""
    if estimate_tokens(text) <= limit:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_tokens(text[:middle]) <= limit:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def _without_terminal_tail(content: str, tail: str) -> str:
    if tail and content.endswith(tail):
        return content[:-len(tail)]
    return content


def _retrieval_note(ref: DurableToolResultRef) -> str:
    if ref.full_available and ref.handle:
        return (
            "\n[结果较长，已投影为有界窗口。完整结果可按 result_handle="
            f'"{ref.handle}" 分页回取；已见内容足够时不要为读完全文连续回取。]'
        )
    return "\n[结果较长，已投影为有界窗口；完整内容当前不可回取。]"


class ToolResultProjector:
    """Project actual ``model_content`` at the single provider-history boundary.

    Persistence happens before the retrieval note is rendered.  This prevents a Runtime DB outage
    or a result above the 2 MiB durable limit from producing the false statement "full result
    saved".  The caller persists ``applied_policy`` alongside the already-projected history item;
    passing it back on resume is an explicit no-op.
    """

    def __init__(
        self,
        *,
        store: DurableToolResultStore | None = None,
        serialization_reserve_ratio: float = SERIALIZATION_RESERVE_RATIO,
    ):
        self.store = store or DurableToolResultStore()
        self.serialization_reserve_ratio = min(
            0.50, max(0.0, float(serialization_reserve_ratio))
        )

    async def project(
        self,
        content: str,
        policy: ResultSizePolicy,
        *,
        run_id: str,
        thread_id: str,
        user_id: str,
        call_id: str,
        tool_name: str,
        safety_tail: str = "",
        source: str = "tool",
        trust: str = "untrusted",
        content_type: str = "text/plain",
        applied_policy: AppliedToolResultPolicy | dict | None = None,
    ) -> ProjectedToolResult:
        raw = str(content or "")
        declared_tail = str(safety_tail or "")
        # A static tool boundary may not apply to every outcome (for example an ACL rejection is
        # platform control text, not untrusted connector data).  Preserve a tail only when the
        # concrete result actually carries it; never append a safety label to an unrelated value.
        tail = declared_tail if declared_tail and raw.endswith(declared_tail) else ""

        # Checkpoint/history already owns this exact projection.  Re-applying a new deploy's
        # defaults would rewrite an old provider item and destroy both resume truth and cache LCP.
        if applied_policy is not None:
            applied = (
                applied_policy
                if isinstance(applied_policy, AppliedToolResultPolicy)
                else AppliedToolResultPolicy.model_validate(applied_policy)
            )
            return ProjectedToolResult(
                model_content=raw,
                result_handle=applied.result_handle,
                full_available=applied.full_available,
                applied_policy=applied,
            )

        body = _without_terminal_tail(raw, tail)
        candidate = body + tail
        raw_tokens = estimate_tokens(raw)
        token_limit = policy.inline_token_limit
        token_content_limit = (
            max(1, math.floor(token_limit * (1.0 - self.serialization_reserve_ratio)))
            if token_limit is not None
            else None
        )
        needs_projection = len(candidate) > policy.inline_chars or (
            token_content_limit is not None
            and estimate_tokens(candidate) > token_content_limit
        )

        encoded = raw.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        if not needs_projection:
            applied = AppliedToolResultPolicy(
                inline_chars=policy.inline_chars,
                inline_token_limit=policy.inline_token_limit,
                persist_full_result=policy.persist_full_result,
                serialization_reserve_ratio=self.serialization_reserve_ratio,
                raw_chars=len(raw),
                raw_tokens=raw_tokens,
                projected_chars=len(candidate),
                projected_tokens=estimate_tokens(candidate),
                content_hash=digest,
            )
            return ProjectedToolResult(model_content=candidate, applied_policy=applied)

        if policy.persist_full_result:
            ref = await self.store.put(
                run_id=run_id,
                thread_id=thread_id,
                user_id=user_id,
                call_id=call_id,
                tool_name=tool_name,
                content=raw,
                content_type=content_type,
                source=source,
                trust=trust,
                applied_policy={
                    "inline_chars": policy.inline_chars,
                    "inline_token_limit": policy.inline_token_limit,
                    "serialization_reserve_ratio": self.serialization_reserve_ratio,
                },
            )
        else:
            ref = DurableToolResultRef(
                content_hash=digest,
                utf8_bytes=len(encoded),
                chars=len(raw),
                estimated_tokens=raw_tokens,
                unavailable_reason="persistence_disabled_by_policy",
            )

        mandatory_suffix = _retrieval_note(ref) + tail
        full_available = bool(ref.full_available and ref.handle)
        char_budget = max(0, policy.inline_chars - len(mandatory_suffix))
        projected_body = body[:char_budget]
        if token_content_limit is not None:
            token_budget = max(
                0,
                token_content_limit - estimate_tokens(mandatory_suffix),
            )
            projected_body = _prefix_within_tokens(projected_body, token_budget)
        projected = projected_body + mandatory_suffix

        applied = AppliedToolResultPolicy(
            inline_chars=policy.inline_chars,
            inline_token_limit=policy.inline_token_limit,
            persist_full_result=policy.persist_full_result,
            serialization_reserve_ratio=self.serialization_reserve_ratio,
            raw_chars=len(raw),
            raw_tokens=raw_tokens,
            projected_chars=len(projected),
            projected_tokens=estimate_tokens(projected),
            truncated=True,
            full_available=full_available,
            result_handle=ref.handle,
            content_hash=digest,
        )
        return ProjectedToolResult(
            model_content=projected,
            result_handle=ref.handle,
            full_available=full_available,
            unavailable_reason=ref.unavailable_reason,
            applied_policy=applied,
        )


def apply_projection_to_execution_result(result, projected: ProjectedToolResult):
    """Attach a projection to the mutable concrete result without importing chat tool types."""

    result.model_content = projected.model_content
    result.result_handle = projected.result_handle
    # Compatibility for the current Observation mapper and existing checkpoints.
    result.raw_ref = projected.result_handle
    result.applied_result_policy = projected.applied_policy.model_dump(mode="json")
    return result


def apply_projection_to_observation(
    observation: dict | None,
    projected: ProjectedToolResult,
) -> dict | None:
    """Persist result identity and the frozen policy in the canonical Observation shape."""

    if not isinstance(observation, dict):
        return observation
    updated = dict(observation)
    updated["result_handle"] = projected.result_handle
    structured = dict(updated.get("structured_data") or {})
    structured["result_projection"] = projected.applied_policy.model_dump(mode="json")
    updated["structured_data"] = structured
    return updated


class ObservationCompactor:
    async def compact(
        self,
        observation: ToolObservation,
        spec: ToolSpec,
        *,
        run_id: str,
        store: ResultStore,
    ) -> ToolObservation:
        content = json.dumps(
            observation.structured_data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        limit = spec.result_size_policy.inline_chars
        if len(content) <= limit:
            return observation
        handle = await store.put(
            run_id=run_id,
            call_id=observation.call_id,
            content=content,
        )
        return observation.model_copy(update={
            "summary": observation.summary[:limit],
            "structured_data": {
                "truncated": True,
                "full_result_chars": len(content),
            },
            "result_handle": handle,
        })
