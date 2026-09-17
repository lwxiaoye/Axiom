"""Durable Run Store for the AXIOM Agent Harness.

This module deliberately owns persisted control state only.  Model execution, UI projection and
tool implementations consume its snapshots rather than reconstructing state from chat messages.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_db import runtime_session

from .contracts import AgentMode, RunPhase, RunSnapshot, TERMINAL_RUN_PHASES
from .profiles import get_profile
from .public_errors import SENSITIVE_WORDS_REJECTION_MESSAGE, public_terminal_reason


HARNESS_STATE_SCHEMA_VERSION = 1
TERMINAL_PHASES = frozenset(phase.value for phase in TERMINAL_RUN_PHASES)
VALID_PHASES = frozenset(phase.value for phase in RunPhase)
# Older workers select queued/leased without checking credential compatibility. Keep scoped
# jobs outside that predicate, including expired leases and recovery requeues during rollout.
SCOPED_JOB_QUEUED = "queued_scoped"
SCOPED_JOB_LEASED = "leased_scoped"
QUEUED_JOB_STATUSES = frozenset({"queued", SCOPED_JOB_QUEUED})
LEASED_JOB_STATUSES = frozenset({"leased", SCOPED_JOB_LEASED})
VALID_JOB_STATUSES = QUEUED_JOB_STATUSES | LEASED_JOB_STATUSES | {"waiting", "completed", "dead"}
_EXECUTION_CONTROL_DEFAULTS: dict[str, Any] = {
    "segment_index": 0,
    "checkpoint_sequence": 0,
    "recovery_reason": None,
    "last_progress_at": None,
    "recovery_count": 0,
}
LOOP_CHECKPOINT_MAX_CHARS = 180_000
_RECOVERY_OBSERVATION_MARK = "【恢复观察】"


def new_run_state(
    *,
    agent_mode: AgentMode | str = AgentMode.STANDARD,
    decision_route: str = "agent",
    capability_scope: str = "default",
    client_network_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Create the canonical persisted state for a new Harness Run.

    ``decision_route`` and ``capability_scope`` remain execution metadata during migration. They
    do not select a different loop; only ``agent_mode`` selects a data-driven Profile.
    """
    mode = AgentMode(agent_mode)
    profile = get_profile(mode)
    return {
        "schema_version": HARNESS_STATE_SCHEMA_VERSION,
        "agent_mode": mode.value,
        "phase": profile.initial_phase.value,
        "goal_revision": 0,
        "plan_version": 0,
        "event_cursor": 0,
        "active_tool_calls": [],
        "cancel_requested": False,
        "terminal_reason": None,
        "decision_route": decision_route,
        "capability_scope": capability_scope,
        "active_capabilities": [],
        "disabled_capabilities": [],
        "plan_id": None,
        "working_context_version": 0,
        "input_cursor": 0,
        "pending_input": None,
        # Internal-only encrypted request-peer context for get_user_location. It is intentionally
        # omitted from RunSnapshot and cleared with other recoverable request secrets at terminal.
        "client_network_context": dict(client_network_context or {}) or None,
        "artifact_review": None,
        "execution_profile": None,
        "last_checkpoint_sequence": 0,
        "execution_control": dict(_EXECUTION_CONTROL_DEFAULTS),
        "completion_observation": None,
        "goal_contract": None,
        "loop_safety": None,
        "approved_plan_version": None,
        "approved_at": None,
        "pending_plan_revision": None,
        "loop_checkpoint": None,
        # Per-Run protocol lock. Capability is re-evaluated on a new Run, while crash/HITL
        # recovery keeps the already selected cursor protocol for this Run.
        "model_transport": None,
    }


def get_execution_control(state: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Read execution-segment facts without requiring them in legacy RunState JSON.

    Older states remain readable and are not mutated by this projection.  The returned mapping is
    always safe to persist as JSON, so recovery code can use it as the base for a CAS patch.
    """
    raw = state.get("execution_control") if isinstance(state, dict) else None
    raw = raw if isinstance(raw, dict) else {}

    def non_negative_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def optional_text(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        text_value = str(value).strip()
        return text_value or None

    return {
        "segment_index": non_negative_int(raw.get("segment_index")),
        "checkpoint_sequence": non_negative_int(raw.get("checkpoint_sequence")),
        "recovery_reason": optional_text(raw.get("recovery_reason")),
        "last_progress_at": optional_text(raw.get("last_progress_at")),
        "recovery_count": non_negative_int(raw.get("recovery_count")),
    }


def _progress_timestamp(value: Any = None) -> str:
    if value is None:
        return datetime.utcnow().isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    text_value = str(value).strip()
    if not text_value:
        raise ValueError("progress_at must not be empty")
    return text_value


async def record_recovery_checkpoint(
    run_id: str,
    *,
    recovery_reason: str,
    progress_at: Any = None,
) -> Optional[dict[str, Any]]:
    """CAS a recovery checkpoint into the same RunState and advance its segment facts.

    This is deliberately a small RunState operation.  It does not change the public Run phase or
    enqueue a second loop; the caller decides when the saved Run should be scheduled again.
    """
    reason = str(recovery_reason or "").strip()
    if not reason:
        raise ValueError("recovery_reason must not be empty")
    progress = _progress_timestamp(progress_at)

    for _ in range(3):
        snapshot = await get_run_state(run_id)
        if not snapshot:
            return None
        current = get_execution_control(snapshot.get("state"))
        patch = {
            "execution_control": {
                "segment_index": current["segment_index"] + 1,
                "checkpoint_sequence": current["checkpoint_sequence"] + 1,
                "recovery_reason": reason,
                "last_progress_at": progress,
                "recovery_count": current["recovery_count"] + 1,
            },
        }
        updated = await transition_run_state(
            run_id,
            expected_version=int(snapshot["version"]),
            patch=patch,
        )
        if updated is not None:
            return updated
    return None


def get_loop_checkpoint(state: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Return the persisted drive_model cursor, or an empty mapping."""
    raw = state.get("loop_checkpoint") if isinstance(state, dict) else None
    return dict(raw) if isinstance(raw, dict) else {}


def loop_checkpoint_messages(state: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = get_loop_checkpoint(state)
    rows = payload.get("messages")
    if not isinstance(rows, list):
        return []
    return [item for item in rows if isinstance(item, dict)]


def _strip_checkpoint_content(messages: Iterable[Any]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        content = row.get("content")
        if isinstance(content, list):
            preserved: list[dict[str, Any]] = []
            for part in content:
                if isinstance(part, str):
                    if part:
                        preserved.append({"type": "text", "text": part})
                    continue
                if not isinstance(part, dict):
                    continue
                kind = str(part.get("type") or "")
                if kind not in {"image", "image_url", "input_image"}:
                    preserved.append(dict(part))
                    continue
                image = part.get("image_url")
                source = part.get("source")
                url = str(
                    (image.get("url") if isinstance(image, dict) else image)
                    or (source.get("url") if isinstance(source, dict) else "")
                    or ""
                )
                inline_source = bool(
                    isinstance(source, dict)
                    and str(source.get("type") or "").lower() in {"base64", "data"}
                )
                if url and not url.lower().startswith("data:") and not inline_source:
                    preserved.append(dict(part))
                else:
                    preserved.append({
                        "type": "text",
                        "text": "（原轮次包含内嵌图片；恢复时需从持久化附件重建）",
                    })
            row["content"] = preserved or "（原轮次包含无法内联恢复的媒体）"
        cleaned.append(row)
    return cleaned


def trim_loop_checkpoint_messages(
    messages: Iterable[Any],
    *,
    max_chars: int = LOOP_CHECKPOINT_MAX_CHARS,
) -> list[dict[str, Any]]:
    """Return a recovery-safe semantic cursor without silently deleting old messages.

    ``max_chars`` is retained as a compatibility/telemetry soft limit. Context compaction owns
    semantic replacement; persistence must not independently create an unannounced third history.
    """
    cleaned = _strip_checkpoint_content(messages)
    return cleaned


def with_recovery_observation(messages: Iterable[Any]) -> list[dict[str, Any]]:
    """Append a one-shot recovery fact. Do not stack duplicates across Worker restarts."""
    rows = [dict(item) for item in messages or [] if isinstance(item, dict)]
    if rows:
        last = str(rows[-1].get("content") or "")
        if _RECOVERY_OBSERVATION_MARK in last:
            return rows
    rows.append({
        "role": "user",
        "content": (
            f"{_RECOVERY_OBSERVATION_MARK}历史现场、工具回执和工作区清单已恢复。"
            "请依据当前目标与证据自行决定继续、核对、修改或回答。"
            "不要把本提示复述给用户。"
        ),
    })
    return rows


async def persist_loop_checkpoint(
    run_id: str,
    *,
    messages: Iterable[Any],
    world_state: Optional[dict[str, Any]] = None,
    goal_revision: int = 0,
    plan_version: int = 0,
    step: int = 0,
) -> Optional[dict[str, Any]]:
    """CAS the model-loop cursor after a paired tool batch or steer inject."""
    from app.services.agent_harness.context import ContextCompiler

    checkpoint_messages = trim_loop_checkpoint_messages(messages)
    checkpoint_world_state = ContextCompiler.bounded_world_state(
        dict(world_state or {})
    )
    serialized_chars = len(json.dumps(checkpoint_messages, ensure_ascii=False))
    replacement_history = any(
        "Another language model started to solve this problem" in str(
            item.get("content") or ""
        )
        for item in checkpoint_messages
    )
    payload = {
        "messages": checkpoint_messages,
        "world_state": checkpoint_world_state,
        "history_kind": "replacement" if replacement_history else "full",
        "serialized_chars": serialized_chars,
        "soft_limit_exceeded": serialized_chars > LOOP_CHECKPOINT_MAX_CHARS,
        "goal_revision": max(0, int(goal_revision or 0)),
        "plan_version": max(0, int(plan_version or 0)),
        "step": max(0, int(step or 0)),
        "saved_at": datetime.utcnow().isoformat(),
    }
    return await patch_run_state(run_id, {"loop_checkpoint": payload})


def has_fresh_job_lease(
    *,
    status: str,
    lease_owner: Any,
    lease_expires_at: Any,
    now: Optional[datetime] = None,
) -> bool:
    """Return whether a Job is currently owned and its lease has not expired."""
    if str(status or "") not in LEASED_JOB_STATUSES or not str(lease_owner or "").strip():
        return False
    if isinstance(lease_expires_at, str):
        try:
            lease_expires_at = datetime.fromisoformat(lease_expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
    if not isinstance(lease_expires_at, datetime):
        return False
    current = now or datetime.utcnow()
    if lease_expires_at.tzinfo is not None and current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    elif lease_expires_at.tzinfo is None and current.tzinfo is not None:
        lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
    try:
        return lease_expires_at > current
    except TypeError:
        return False


def _job_wake_reason(value: Any) -> Optional[str]:
    text_value = str(value or "").strip()
    return text_value[:64] or None


def _job_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _merge_job_available_at(
    current: Any,
    requested: Any,
    *,
    now: datetime,
) -> Any:
    """Merge a pending wake deadline without turning recovery backoff into an immediate run.

    ``available_at`` on a leased row is normally cleared at claim time.  The expired-value branch
    also keeps legacy leased rows safe: an old due timestamp is scheduling history, not a new
    recovery deadline.  When two pending wakeups have future deadlines, the earliest one wins so a
    later input cannot be lost behind an older, longer delay.
    """
    requested_dt = _job_datetime(requested)
    if requested_dt is None:
        return current
    current_dt = _job_datetime(current)
    now_dt = _job_datetime(now) or datetime.utcnow()
    if current_dt is None or current_dt <= now_dt:
        return requested
    return current if current_dt <= requested_dt else requested


def is_harness_state(state: Optional[dict[str, Any]]) -> bool:
    return isinstance(state, dict) and int(state.get("schema_version") or 0) == HARNESS_STATE_SCHEMA_VERSION


async def initialize_run_state(
    run_id: str,
    *,
    agent_mode: AgentMode | str = AgentMode.STANDARD,
    decision_route: str = "agent",
    capability_scope: str = "default",
    client_network_context: Optional[dict[str, Any]] = None,
) -> bool:
    """Install Harness state exactly once. Existing historical runs are never rewritten."""
    factory = runtime_session()
    if factory is None:
        return False
    from app.runtime_models import AgentRun
    async with factory() as session:
        run = await session.get(AgentRun, run_id, with_for_update=True)
        if not run:
            return False
        if is_harness_state(run.state):
            return True
        run.state = new_run_state(
            agent_mode=agent_mode,
            decision_route=decision_route,
            capability_scope=capability_scope,
            client_network_context=client_network_context,
        )
        run.state_version = int(getattr(run, "state_version", 0) or 0) + 1
        run.accepted_at = run.accepted_at or datetime.utcnow()
        await session.commit()
        return True


async def get_run_state(run_id: str) -> Optional[dict[str, Any]]:
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRun
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        if not run or not is_harness_state(run.state):
            return None
        return {"state": dict(run.state or {}), "version": int(run.state_version or 0),
                "created_at": run.created_at.isoformat() if run.created_at else None}


async def get_run_snapshot(run_id: str) -> Optional[RunSnapshot]:
    """Return the public authoritative snapshot without leaking migration-only state fields."""
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRun, AgentRunEvent
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        if not run or not is_harness_state(run.state):
            return None
        state = dict(run.state or {})
        persisted_cursor = await session.scalar(
            select(func.max(AgentRunEvent.sequence)).where(AgentRunEvent.run_id == run_id)
        )
        phase = str(state["phase"])
        status = str(run.status or "")
        if status == "completed":
            phase = "partial" if str(getattr(run, "outcome", "") or "") == "partial" else "completed"
        elif status in {"failed", "cancelled"}:
            phase = status
        execution_profile = _validated_execution_profile_from_state(state)
        return RunSnapshot(
            run_id=str(run.id),
            thread_id=str(run.thread_id),
            user_id=str(run.user_id),
            agent_mode=state["agent_mode"],
            phase=phase,
            capability_scope=str(state.get("capability_scope") or "default"),
            state_version=int(run.state_version or 0),
            goal_revision=int(state.get("goal_revision") or 0),
            plan_version=int(state.get("plan_version") or 0),
            event_cursor=max(
                int(state.get("event_cursor") or 0), int(persisted_cursor or 0),
            ),
            active_tool_calls=tuple(state.get("active_tool_calls") or ()),
            pending_input=state.get("pending_input"),
            cancel_requested=bool(state.get("cancel_requested")),
            terminal_reason=public_terminal_reason(
                state.get("terminal_reason"), phase=phase,
            ),
            execution_profile=execution_profile,
            goal_contract=(
                state.get("goal_contract")
                if isinstance(state.get("goal_contract"), dict)
                else None
            ),
            execution_control=get_execution_control(state),
            completion_observation=(
                state.get("completion_observation")
                if isinstance(state.get("completion_observation"), dict)
                else None
            ),
            approved_plan_version=(
                int(state["approved_plan_version"])
                if state.get("approved_plan_version") is not None
                and str(state.get("approved_plan_version")) != ""
                else None
            ),
        )


def _validated_execution_profile_from_state(
    state: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Project only finalized, policy-hash-valid snapshots into public Run facts."""
    from app.services.chat.execution_profile import (
        ExecutionProfileError,
        validate_profile_snapshot,
    )

    record = state.get("execution_profile")
    if not isinstance(record, dict) or record.get("status") != "finalized":
        return None
    snapshot = record.get("snapshot")
    if not isinstance(snapshot, dict):
        raise ExecutionProfileError("finalized execution profile has no snapshot")
    validated = validate_profile_snapshot(snapshot)
    skill_state = state.get("skill_state")
    skills = skill_state.get("skills") if isinstance(skill_state, dict) else skill_state
    if not isinstance(skills, list):
        return validated

    # The accepted snapshot remains immutable.  A model-loaded first-party Skill is a separate,
    # durable execution fact that changes the effective profile for this Run and its recovery
    # segments; an explicit native Skill keeps its interactive contract authoritative.
    explicit_native = any(
        isinstance(item, dict)
        and str(item.get("selection_source") or "").strip().lower() == "explicit"
        and str(item.get("status") or "").strip().lower() in {"authorized", "loaded"}
        and str(item.get("execution_profile_id") or "interactive") == "interactive"
        for item in skills
    )
    if explicit_native:
        return validated
    from app.services.chat.execution_profile import dynamic_profile_for_skill
    for item in reversed(skills):
        if not isinstance(item, dict):
            continue
        source = str(item.get("selection_source") or "").strip().lower()
        status = str(item.get("status") or "").strip().lower()
        if source not in {"model", "dynamic"} or status != "loaded":
            continue
        if str(item.get("execution_profile_id") or "") != "artifact_coding":
            continue
        return validate_profile_snapshot(dynamic_profile_for_skill(validated, item))
    return validated


async def load_latest_finalized_execution_profile_source(
    *,
    thread_id: str,
    user_id: str,
    exclude_run_id: str = "",
    preferred_run_id: str = "",
) -> Optional[dict[str, Any]]:
    """Return the one prior Run selected as the source of an explicit resume.

    Callers must use the returned ``run_id`` for every other resume fact (snapshot,
    skills and plan). Selecting those facts independently by "latest" can combine
    state from different historical Runs into a continuation that never existed.
    """
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentModelAttemptAudit, AgentRun

    async with factory() as session:
        structured_policy_attempt = (
            select(AgentModelAttemptAudit.id)
            .where(AgentModelAttemptAudit.run_id == AgentRun.id)
            .where(
                AgentModelAttemptAudit.error_code == "sensitive_words_detected"
            )
            .exists()
        )
        query = (
            select(AgentRun)
            .where(AgentRun.thread_id == str(thread_id))
            .where(AgentRun.user_id == str(user_id))
            .where(or_(
                AgentRun.error.is_(None),
                AgentRun.error != SENSITIVE_WORDS_REJECTION_MESSAGE,
            ))
            .where(~structured_policy_attempt)
            .order_by(AgentRun.created_at.desc())
            .limit(20)
        )
        if preferred_run_id:
            query = query.where(AgentRun.id == str(preferred_run_id))
        if exclude_run_id:
            query = query.where(AgentRun.id != str(exclude_run_id))
        rows = (await session.execute(query)).scalars().all()
    fallback = None
    for row in rows:
        state = dict(getattr(row, "state", None) or {})
        if not is_harness_state(state):
            continue
        if (
            str(getattr(row, "error", None) or "")
            == SENSITIVE_WORDS_REJECTION_MESSAGE
            or str(state.get("terminal_reason") or "")
            == SENSITIVE_WORDS_REJECTION_MESSAGE
            or "policy_rejection_context_quarantined" in state
        ):
            # A rejected goal is display history, never a resumable execution source.
            continue
        snapshot = _validated_execution_profile_from_state(state)
        meta = {
            "run_id": str(row.id),
            "execution_profile": snapshot,
            "agent_mode": str(getattr(row, "agent_mode", None) or "standard"),
            "status": str(getattr(row, "status", None) or ""),
        }
        if snapshot is not None:
            return meta
        if fallback is None:
            fallback = meta
    return fallback


async def load_latest_finalized_execution_profile(
    *,
    thread_id: str,
    user_id: str,
    exclude_run_id: str = "",
) -> Optional[dict[str, Any]]:
    """Compatibility projection for callers that only need the prior profile."""
    source = await load_latest_finalized_execution_profile_source(
        thread_id=thread_id,
        user_id=user_id,
        exclude_run_id=exclude_run_id,
    )
    if source is None:
        return None
    return dict(source["execution_profile"])


async def store_pending_input(
    run_id: str,
    payload: dict[str, Any],
    *,
    access_token: str = "",
) -> bool:
    """Persist the complete, recoverable input required by a Worker.

    The browser access token is encrypted with the same multi-instance Fernet key used for
    connector credentials.  Plaintext credentials never enter RunState or logs.
    """
    safe_payload = dict(payload or {})
    from app.services.connectors.crypto import credential_key_id
    safe_payload["credential_key_id"] = credential_key_id()
    attachments = safe_payload.get("attachments")
    if isinstance(attachments, list):
        referenced = []
        for attachment in attachments:
            if (
                isinstance(attachment, dict)
                and attachment.get("kind") == "image"
                and str(attachment.get("file_id") or "").strip()
                and str(attachment.get("image_url") or "").startswith("data:image/")
            ):
                # 每次 RunState / event_cursor 更新都会写回 pending_input；原图已持久化，
                # 不应再让数 MB 的 base64 随每个事件重复进出数据库。
                attachment = {**attachment, "image_url": ""}
            referenced.append(attachment)
        safe_payload["attachments"] = referenced
    if access_token:
        from app.services.connectors.crypto import encrypt_secret
        safe_payload["access_token_cipher"] = encrypt_secret(access_token)
    safe_payload.pop("access_token", None)
    return bool(await patch_run_state(run_id, {"pending_input": safe_payload}))


async def load_pending_input(run_id: str, *, decrypt_token: bool = True) -> Optional[dict[str, Any]]:
    snapshot = await get_run_state(run_id)
    if not snapshot:
        return None
    payload = dict((snapshot.get("state") or {}).get("pending_input") or {})
    if not payload:
        return None
    payload.pop("credential_key_id", None)
    cipher = str(payload.pop("access_token_cipher", "") or "")
    if cipher and decrypt_token:
        from app.services.connectors.crypto import decrypt_secret
        payload["access_token"] = decrypt_secret(cipher)
    return payload


async def clear_pending_input(run_id: str) -> bool:
    """Drop recoverable request credentials after a terminal checkpoint."""
    return bool(await patch_run_state(
        run_id,
        {"pending_input": None, "client_network_context": None},
    ))


async def store_execution_profile_evidence(run_id: str, evidence: dict[str, Any]) -> bool:
    """Persist accept-time evidence without allowing a later caller to revise it."""
    from app.services.chat.execution_profile import ExecutionProfileError

    frozen = dict(evidence or {})
    evidence_hash = str(frozen.get("evidence_hash") or "")
    resolver_version = str(frozen.get("resolver_version") or "")
    if not evidence_hash or not resolver_version:
        raise ExecutionProfileError("execution profile evidence is incomplete")

    for _ in range(3):
        current = await get_run_state(run_id)
        if not current:
            return False
        record = (current.get("state") or {}).get("execution_profile")
        if isinstance(record, dict):
            if record.get("status") == "finalized":
                raise ExecutionProfileError("execution profile is already finalized")
            old_hash = str(record.get("evidence_hash") or "")
            if old_hash == evidence_hash:
                return True
            raise ExecutionProfileError("execution profile evidence is immutable")

        record = {
            "status": "unresolved",
            "resolver_version": resolver_version,
            "evidence_hash": evidence_hash,
            "evidence": frozen,
            "snapshot": None,
        }
        updated = await transition_run_state(
            run_id,
            expected_version=int(current["version"]),
            patch={"execution_profile": record},
        )
        if updated:
            return True
    return False


async def load_finalized_execution_profile(run_id: str) -> Optional[dict[str, Any]]:
    """Return and validate the immutable profile snapshot, or ``None`` if unresolved."""
    current = await get_run_state(run_id)
    if not current:
        return None
    return _validated_execution_profile_from_state(dict(current.get("state") or {}))


async def finalize_execution_profile(run_id: str) -> dict[str, Any]:
    """Resolve frozen evidence once and CAS-persist an immutable profile snapshot.

    CAS losers only re-read durable state.  If an unrelated checkpoint advanced state_version,
    the already-resolved candidate may be retried against the same evidence hash; mutable request
    data is never consulted and the resolver is never invoked a second time.
    """
    from app.services.chat.execution_profile import (
        ExecutionProfileError,
        resolve_profile_snapshot,
        validate_profile_snapshot,
    )

    candidate: Optional[dict[str, Any]] = None
    candidate_evidence_hash = ""
    for _ in range(4):
        current = await get_run_state(run_id)
        if not current:
            raise ExecutionProfileError("Runtime Harness RunState is unavailable")
        record = (current.get("state") or {}).get("execution_profile")
        if not isinstance(record, dict):
            raise ExecutionProfileError("execution profile evidence is missing")
        if record.get("status") == "finalized":
            snapshot = record.get("snapshot")
            if not isinstance(snapshot, dict):
                raise ExecutionProfileError("finalized execution profile has no snapshot")
            return (
                _validated_execution_profile_from_state(dict(current.get("state") or {}))
                or validate_profile_snapshot(snapshot)
            )
        if record.get("status") != "unresolved":
            raise ExecutionProfileError("execution profile has invalid status")

        evidence = record.get("evidence")
        evidence_hash = str(record.get("evidence_hash") or "")
        if not isinstance(evidence, dict) or not evidence_hash:
            raise ExecutionProfileError("execution profile evidence is incomplete")
        if candidate is None:
            candidate = resolve_profile_snapshot(evidence)
            candidate_evidence_hash = evidence_hash
        elif evidence_hash != candidate_evidence_hash:
            raise ExecutionProfileError("execution profile evidence changed during finalization")

        finalized = {
            "status": "finalized",
            "resolver_version": str(record.get("resolver_version") or ""),
            "evidence_hash": candidate_evidence_hash,
            "evidence": evidence,
            "snapshot": candidate,
        }
        updated = await transition_run_state(
            run_id,
            expected_version=int(current["version"]),
            patch={"execution_profile": finalized},
        )
        if updated:
            return _validated_execution_profile_from_state(
                dict(updated.get("state") or {})
            ) or validate_profile_snapshot(candidate)

    raise ExecutionProfileError("execution profile CAS did not converge")


async def transition_run_state(
    run_id: str,
    *,
    expected_version: int,
    patch: dict[str, Any],
    phase: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Compare-and-swap a Harness RunState. Callers retry from a fresh snapshot on conflict."""
    if phase is not None and phase not in VALID_PHASES:
        raise ValueError(f"invalid Harness phase: {phase}")
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRun
    async with factory() as session:
        run = await session.get(AgentRun, run_id, with_for_update=True)
        if not run or int(run.state_version or 0) != int(expected_version):
            return None
        current = dict(run.state or {})
        if not is_harness_state(current):
            return None
        existing_profile = current.get("execution_profile")
        proposed_profile = patch.get("execution_profile") if "execution_profile" in patch else None
        if (
            isinstance(existing_profile, dict)
            and existing_profile.get("status") == "finalized"
            and "execution_profile" in patch
            and proposed_profile != existing_profile
        ):
            from app.services.chat.execution_profile import ExecutionProfileError

            raise ExecutionProfileError("finalized execution profile is immutable")
        current.update(patch)
        if phase is not None:
            current["phase"] = phase
        run.state = current
        run.state_version = int(run.state_version or 0) + 1
        if phase == RunPhase.EXECUTING.value and run.started_at is None:
            run.started_at = datetime.utcnow()
        await session.commit()
        return {"state": current, "version": int(run.state_version)}


async def patch_run_state(run_id: str, patch: dict[str, Any], *, phase: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Best-effort CAS retry wrapper for independent checkpoints."""
    for _ in range(3):
        snapshot = await get_run_state(run_id)
        if not snapshot:
            return None
        updated = await transition_run_state(
            run_id, expected_version=int(snapshot["version"]), patch=patch, phase=phase,
        )
        if updated:
            return updated
    return None


async def _queued_job_status(session: AsyncSession, run_id: str, current_status: str = "") -> str:
    if current_status in {SCOPED_JOB_QUEUED, SCOPED_JOB_LEASED}:
        return SCOPED_JOB_QUEUED
    from app.runtime_models import AgentRun
    key_scope = await session.scalar(select(
        AgentRun.state["pending_input"]["credential_key_id"].as_string(),
    ).where(AgentRun.id == run_id))
    return SCOPED_JOB_QUEUED if key_scope else "queued"


async def enqueue_job(run_id: str, *, wake_reason: str = "accepted", available_at: Optional[datetime] = None) -> bool:
    factory = runtime_session()
    if factory is None:
        return False
    from app.runtime_models import AgentRunJob
    async with factory() as session:
        # The unique run_id index prevents duplicates; the row lock makes the pending wake and
        # its deadline an atomic handoff with finish_job/claim_next_job.
        existing = await session.scalar(
            select(AgentRunJob)
            .where(AgentRunJob.run_id == run_id)
            .limit(1)
            .with_for_update()
        )
        reason = _job_wake_reason(wake_reason)
        if existing:
            now = datetime.utcnow()
            if has_fresh_job_lease(
                status=str(existing.status or ""),
                lease_owner=existing.lease_owner,
                lease_expires_at=existing.lease_expires_at,
                now=now,
            ):
                # A live worker already owns this Job.  Keep its lease and attempt counter
                # intact; persist both the wake signal and a future recovery deadline.  The
                # finisher will atomically turn this live lease back into one queued Job.
                existing.wake_reason = reason or _job_wake_reason(existing.wake_reason)
                existing.available_at = _merge_job_available_at(
                    existing.available_at, available_at, now=now,
                )
            else:
                existing.status = await _queued_job_status(session, run_id, existing.status)
                existing.available_at = _merge_job_available_at(
                    existing.available_at, available_at, now=now,
                ) or now
                existing.wake_reason = reason
                existing.lease_owner = None
                existing.lease_expires_at = None
        else:
            queued_status = await _queued_job_status(session, run_id)
            session.add(AgentRunJob(
                id=uuid.uuid4().hex, run_id=run_id, status=queued_status,
                available_at=available_at or datetime.utcnow(), wake_reason=reason,
            ))
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT pg_notify('agent_harness_jobs', :run_id)"),
                {"run_id": str(run_id)},
            )
        await session.commit()
        try:
            from app.services.tasks import runtime_event_bus
            runtime_event_bus.notify_job_local()
        except Exception:  # noqa: BLE001
            pass
        return True


def job_claim_action(
    *,
    status: str,
    lease_owner: str,
    worker_id: str,
    attempt_count: int,
    max_attempts: int,
) -> str:
    """renew_skip | backoff | claim.

    Attempt counters are operational telemetry, not a Run terminal decision.  A repeatedly
    failing recovery Job is delayed and made claimable again instead of dead-lettering its Run.
    """
    if str(status or "") in LEASED_JOB_STATUSES and str(lease_owner or "") == str(worker_id or ""):
        return "renew_skip"
    cap = int(max_attempts or 0)
    if cap > 0 and int(attempt_count or 0) >= cap:
        return "backoff"
    return "claim"


async def claim_next_job(worker_id: str, *, lease_seconds: int = 30) -> Optional[dict[str, Any]]:
    """Atomically claim one runnable job. PostgreSQL SKIP LOCKED enables safe multi-worker use."""
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRunJob, AgentRun
    from app.services.connectors.crypto import credential_key_id
    key_scope = AgentRun.state["pending_input"]["credential_key_id"].as_string()
    now = datetime.utcnow()
    async with factory() as session:
        candidate = (await session.execute(
            select(AgentRunJob, key_scope)
            .join(AgentRun, AgentRun.id == AgentRunJob.run_id)
            .where(or_(key_scope.is_(None), key_scope == credential_key_id()))
            .where(or_(
                and_(AgentRunJob.status.in_(QUEUED_JOB_STATUSES), or_(AgentRunJob.available_at.is_(None), AgentRunJob.available_at <= now)),
                and_(AgentRunJob.status.in_(LEASED_JOB_STATUSES), AgentRunJob.lease_expires_at.is_not(None), AgentRunJob.lease_expires_at <= now),
            ))
            .order_by(AgentRunJob.available_at.asc(), AgentRunJob.created_at.asc())
            .with_for_update(skip_locked=True, of=AgentRunJob)
            .limit(1)
        )).one_or_none()
        if not candidate:
            return None
        row, credential_scope = candidate
        scoped = bool(credential_scope) or row.status in {SCOPED_JOB_QUEUED, SCOPED_JOB_LEASED}
        action = job_claim_action(
            status=str(row.status or ""),
            lease_owner=str(row.lease_owner or ""),
            worker_id=str(worker_id or ""),
            attempt_count=int(row.attempt_count or 0),
            max_attempts=int(row.max_attempts or 0),
        )
        if action == "renew_skip":
            # Same worker already executing this job.  A blocked event loop can let the
            # lease expire; reclaiming it here would start a second drive_model on the
            # same Run (true-machine Curry PPT: 16–28 attempts, HTTP 400, capability deadlock).
            row.lease_expires_at = now + timedelta(seconds=max(5, lease_seconds))
            await session.commit()
            return None
        if action == "backoff":
            # Keep the Run alive.  Reset the Job attempt window after a bounded delay; a later
            # worker can retry once the transient dependency has recovered.
            row.status = SCOPED_JOB_QUEUED if scoped else "queued"
            row.last_error = "max_attempts window reached; recovery backoff"
            row.lease_owner = None
            row.lease_expires_at = None
            row.attempt_count = 0
            row.available_at = now + timedelta(seconds=5)
            await session.commit()
            return None
        claimed_wake_reason = row.wake_reason
        row.status = SCOPED_JOB_LEASED if scoped else "leased"
        row.lease_owner = worker_id
        row.lease_expires_at = now + timedelta(seconds=max(5, lease_seconds))
        row.attempt_count = int(row.attempt_count or 0) + 1
        # Once claimed, the old queue deadline has been consumed.  A recovery wake arriving while
        # this lease is live must install its own future deadline rather than comparing against an
        # already-expired timestamp from the previous claim.
        row.available_at = None
        # Consume the wake signal with the claim.  If a new recovery/input signal arrives
        # while this lease is live, enqueue_job writes a fresh wake_reason that finish_job can
        # observe before releasing the lease; a stale signal must not requeue an ordinary wait.
        row.wake_reason = None
        await session.commit()
        return {
            "id": row.id, "run_id": row.run_id, "attempt_count": row.attempt_count,
            "max_attempts": row.max_attempts, "wake_reason": claimed_wake_reason,
        }


async def heartbeat_job(job_id: str, worker_id: str, *, lease_seconds: int = 30) -> bool:
    factory = runtime_session()
    if factory is None:
        return False
    from app.runtime_models import AgentRunJob
    async with factory() as session:
        result = await session.execute(update(AgentRunJob).where(
            AgentRunJob.id == job_id,
            AgentRunJob.status.in_(LEASED_JOB_STATUSES),
            AgentRunJob.lease_owner == worker_id,
        ).values(lease_expires_at=datetime.utcnow() + timedelta(seconds=max(5, lease_seconds))))
        await session.commit()
        return bool(result.rowcount)


async def finish_job(
    job_id: str,
    worker_id: str,
    *,
    status: str,
    error: str = "",
    wake_reason: str = "",
    available_at: Optional[datetime] = None,
) -> bool:
    if status not in {"waiting", "completed", "dead", "queued"}:
        raise ValueError(f"invalid job final status: {status}")
    factory = runtime_session()
    if factory is None:
        return False
    from app.runtime_models import AgentRunJob
    async with factory() as session:
        row = await session.get(AgentRunJob, job_id, with_for_update=True)
        if not row or row.status not in LEASED_JOB_STATUSES or row.lease_owner != worker_id:
            return False
        scoped = row.status == SCOPED_JOB_LEASED
        now = datetime.utcnow()
        requested_reason = _job_wake_reason(wake_reason)
        if available_at is not None:
            row.available_at = _merge_job_available_at(
                row.available_at, available_at, now=now,
            )
        if requested_reason:
            row.wake_reason = requested_reason
        final_status = status
        # Any pending wake is a scheduling fact, not only the literal "recovery" reason.  This
        # covers input/finalizer_recovery/completion_gap and legacy non-empty wake values alike.
        if status == "waiting" and _job_wake_reason(row.wake_reason):
            final_status = "queued"
        row.status = SCOPED_JOB_QUEUED if final_status == "queued" and scoped else final_status
        row.last_error = error or None
        row.lease_owner = None
        row.lease_expires_at = None
        if final_status == "queued":
            # Preserve a recovery backoff recorded while the lease was live.  A wake without an
            # explicit deadline is still runnable immediately after releasing the lease.
            row.available_at = row.available_at or now
        else:
            row.available_at = None
            row.wake_reason = None
        await session.commit()
        return True


async def create_context_snapshot(run_id: str, *, covered_sequence: int, summary: dict[str, Any]) -> Optional[dict[str, Any]]:
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRunContextSnapshot
    async with factory() as session:
        latest = await session.scalar(
            select(AgentRunContextSnapshot)
            .where(AgentRunContextSnapshot.run_id == run_id)
            .order_by(AgentRunContextSnapshot.version.desc())
            .limit(1)
        )
        version = int(latest.version or 0) + 1 if latest else 1
        row = AgentRunContextSnapshot(
            id=uuid.uuid4().hex, run_id=run_id, version=version,
            covered_sequence=max(0, int(covered_sequence or 0)), summary=dict(summary or {}),
        )
        session.add(row)
        await session.commit()
        await patch_run_state(run_id, {"working_context_version": version, "last_checkpoint_sequence": row.covered_sequence})
        return {"id": row.id, "version": version, "covered_sequence": row.covered_sequence, "summary": row.summary}


async def get_latest_context_snapshot(run_id: str) -> Optional[dict[str, Any]]:
    factory = runtime_session()
    if factory is None:
        return None
    from app.runtime_models import AgentRunContextSnapshot
    async with factory() as session:
        row = await session.scalar(
            select(AgentRunContextSnapshot)
            .where(AgentRunContextSnapshot.run_id == run_id)
            .order_by(AgentRunContextSnapshot.version.desc())
            .limit(1)
        )
        if not row:
            return None
        return {"id": row.id, "version": row.version, "covered_sequence": row.covered_sequence, "summary": row.summary}


def compact_observations(observations: Iterable[dict[str, Any]], *, limit: int = 20) -> list[dict[str, Any]]:
    """Safe model-view projection: retain facts and refs, never raw command output."""
    out: list[dict[str, Any]] = []
    for item in list(observations or [])[-limit:]:
        if not isinstance(item, dict):
            continue
        out.append({
            "status": item.get("status"),
            "model_content": str(item.get("model_content") or "")[:1200],
            "artifacts": list(item.get("artifacts") or [])[:10],
            "citations": list(item.get("citations") or [])[:10],
            "raw_ref": item.get("raw_ref"),
        })
    return out
