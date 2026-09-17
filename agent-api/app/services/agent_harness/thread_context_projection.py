"""Shared cross-Run projection preparation for non-tool main-chat turns."""

from __future__ import annotations

import copy
import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from app.core.config import settings

from .context import (
    ContextCompiler,
    ContextFacts,
    ContextProjectionLedger,
    canonical_hash,
)
from .thread_projection_store import thread_projection_store


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedThreadProjection:
    provider_messages: list[dict[str, Any]]
    commit_bundle: dict[str, Any] | None
    provider_reuse_active: bool
    eligibility_reason: str


def _canary_selected(mode: str, thread_id: str) -> bool:
    return mode == "on" or (
        mode == "canary"
        and int(hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:8], 16) % 10 == 0
    )


async def prepare_plain_thread_projection(
    *,
    run_id: str,
    thread_id: str,
    model: str,
    transport: str,
    stable_base: str,
    ordinary_provider_messages: list[dict[str, Any]],
    source_messages: list[dict[str, Any]],
    current_run_delta: list[dict[str, Any]],
    display_history_before_run: list[dict[str, Any]],
    display_history_current: list[dict[str, Any]],
    world_state: dict[str, Any],
    force_reset_reason: str = "",
) -> PreparedThreadProjection:
    """Prepare a shadow candidate and durably mark it before any Provider-active reuse."""

    if not run_id or not thread_id:
        return PreparedThreadProjection(
            provider_messages=copy.deepcopy(ordinary_provider_messages),
            commit_bundle=None,
            provider_reuse_active=False,
            eligibility_reason="missing_run_scope",
        )
    try:
        from .plan_store import get_plan_snapshot
        from .run_store import get_run_snapshot

        run_snapshot = await get_run_snapshot(run_id)
        if run_snapshot is None:
            raise RuntimeError("run snapshot unavailable")
        plan_snapshot = await get_plan_snapshot(run_id)
        snapshot = ContextCompiler().compile(ContextFacts(
            run=run_snapshot,
            plan=plan_snapshot,
            world_state=dict(world_state or {}),
        ))
        base_hash = canonical_hash({"system_prompt": str(stable_base or "")})
        tool_hash = canonical_hash([])
        requested = str(
            getattr(settings, "THREAD_PROJECTION_MODE", "shadow") or "shadow"
        ).strip().lower()
        effective_mode = await thread_projection_store.effective_rollout_mode(
            requested,
            model=model,
            transport=transport,
        )
        selected = _canary_selected(effective_mode, thread_id)
        persisted = await thread_projection_store.load(
            thread_id=thread_id,
            model=model,
            transport=transport,
        )
        eligibility = None
        projection_source = copy.deepcopy(source_messages)
        if persisted is not None:
            if force_reset_reason:
                ledger, eligibility = ContextProjectionLedger.from_persisted_shadow(
                    persisted,
                    forced_reset_reason=force_reset_reason,
                    thread_id=thread_id,
                    model=model,
                    source_messages=projection_source,
                    base_prompt_hash=base_hash,
                    tool_schema_hash=tool_hash,
                    transport=transport,
                    display_history=display_history_before_run,
                )
            else:
                restored = ContextProjectionLedger.restore_canonical_source(
                    persisted,
                    display_history=display_history_before_run,
                    current_run_delta=current_run_delta,
                    replacement_base_message=(
                        {"role": "system", "content": str(stable_base)}
                        if stable_base
                        else None
                    ),
                    replace_base_message=True,
                    target_transport=transport,
                )
                if restored is not None:
                    projection_source = restored
                ledger, eligibility = ContextProjectionLedger.from_persisted_shadow(
                    persisted,
                    thread_id=thread_id,
                    model=model,
                    source_messages=projection_source,
                    base_prompt_hash=base_hash,
                    tool_schema_hash=tool_hash,
                    transport=transport,
                    display_history=display_history_before_run,
                )
        else:
            ledger = None
            if not force_reset_reason:
                candidates = (
                    await thread_projection_store.load_transport_transition_candidates(
                        thread_id=thread_id,
                        model=model,
                        target_transport=transport,
                    )
                )
                for candidate in candidates:
                    transitioned = (
                        ContextProjectionLedger.from_persisted_transport_transition(
                            candidate,
                            display_history=display_history_before_run,
                            current_run_delta=current_run_delta,
                            replacement_base_message=(
                                {"role": "system", "content": str(stable_base)}
                                if stable_base
                                else None
                            ),
                            replace_base_message=True,
                            target_transport=transport,
                        )
                    )
                    if transitioned is None:
                        continue
                    ledger, projection_source, eligibility = transitioned
                    break
            if ledger is None:
                ledger = ContextProjectionLedger(
                    initial_reason=force_reset_reason or "initial"
                )
        projected = ledger.project(
            source_messages=projection_source,
            snapshot=snapshot,
            base_prompt_hash=base_hash,
            tool_schema_hash=tool_hash,
            transport=transport,
        )
        mode = "shadow"
        state = ledger.to_persisted_state(
            thread_id=thread_id,
            model=model,
            mode=mode,
            display_history=display_history_current,
        )
        corrupt_reasons = {
            "projection_source_cursor_mismatch",
            "projection_source_hash_mismatch",
            "source_not_append_only",
            "source_history_hash_mismatch",
            "source_cursor_invalid",
            "invalid_epoch_reason",
            "unsupported_mode",
            "projection_state_invalid",
            "projection_metadata_mismatch",
        }
        metrics = {
            "shadow_eligible_delta": int(bool(eligibility and eligibility.eligible)),
            "alignment_error_delta": int(
                bool(eligibility and eligibility.reason in corrupt_reasons)
            ),
            "expected_reset_delta": int(
                bool(
                    eligibility
                    and not eligibility.eligible
                    and eligibility.reason not in corrupt_reasons
                )
            ),
            "unexpected_reset_delta": int(
                bool(eligibility and eligibility.reason in corrupt_reasons)
            ),
        }
        provider_active = bool(selected and eligibility and eligibility.eligible)
        eligibility_reason = (
            eligibility.reason if eligibility is not None else "initial"
        )
        if provider_active:
            marked = await thread_projection_store.mark_canary_candidate(
                thread_id=thread_id,
                model=model,
                transport=transport,
                run_id=run_id,
                expected_revision=state.storage_revision,
            )
            if marked:
                state = state.after_successful_save().model_copy(
                    update={"mode": "canary"}
                )
            else:
                # Never change the Provider body without first persisting the root-Run marker
                # used for crash-safe terminal accounting.
                provider_active = False
                eligibility_reason = "canary_marker_unavailable"
        return PreparedThreadProjection(
            provider_messages=(
                [dict(item) for item in projected.messages]
                if provider_active
                else copy.deepcopy(ordinary_provider_messages)
            ),
            commit_bundle={
                "state": state,
                "display_history": copy.deepcopy(display_history_current),
                "metrics": metrics,
            },
            provider_reuse_active=provider_active,
            eligibility_reason=eligibility_reason,
        )
    except Exception:  # noqa: BLE001 - context projection is fail-open in shadow rollout
        logger.warning(
            "plain thread projection unavailable run=%s thread=%s",
            run_id,
            thread_id,
            exc_info=True,
        )
        return PreparedThreadProjection(
            provider_messages=copy.deepcopy(ordinary_provider_messages),
            commit_bundle=None,
            provider_reuse_active=False,
            eligibility_reason="projection_unavailable",
        )


def terminal_commit_bundle(
    prepared: PreparedThreadProjection,
    *,
    assistant_item: dict[str, Any],
) -> dict[str, Any] | None:
    if prepared.commit_bundle is None:
        return None
    return {
        **prepared.commit_bundle,
        "assistant_item": copy.deepcopy(assistant_item),
    }


async def record_projection_canary_error(
    prepared: PreparedThreadProjection,
    *,
    run_id: str,
) -> bool:
    """Mark a provider-active candidate dirty when the Run recovers via another prompt shape."""

    if not prepared.provider_reuse_active or not isinstance(prepared.commit_bundle, dict):
        return False
    state = prepared.commit_bundle.get("state")
    if state is None:
        return False
    return await thread_projection_store.record_canary_error(
        thread_id=str(state.thread_id or ""),
        model=str(state.model or ""),
        transport=str(state.transport or ""),
        run_id=str(run_id or ""),
    )


async def commit_projection_bundle(
    bundle: Any,
    *,
    answer: str,
    run_id: str,
    display_assistant_message: dict[str, Any] | None = None,
) -> bool:
    """Commit a terminal Provider item only after the public assistant row is durable."""

    if not isinstance(bundle, dict):
        return False
    state = bundle.get("state")
    assistant_item = bundle.get("assistant_item")
    display_history = list(bundle.get("display_history") or [])
    if state is None or not isinstance(assistant_item, dict):
        return False
    display_assistant = copy.deepcopy(display_assistant_message)
    if not isinstance(display_assistant, dict) or str(
        display_assistant.get("role") or ""
    ) != "assistant":
        display_assistant = {"role": "assistant", "content": str(answer or "")}
    display_history.append(display_assistant)
    committed = ContextProjectionLedger.with_committed_source_items(
        state,
        source_items=[assistant_item],
        display_history=display_history,
    )
    if committed is None:
        logger.warning("terminal projection cursor is invalid run=%s", run_id)
        return False
    metrics = dict(bundle.get("metrics") or {})
    metrics.pop("canary_candidate", None)
    saved = await thread_projection_store.save(
        committed,
        run_id=str(run_id or ""),
        **metrics,
    )
    if not saved and str(getattr(committed, "mode", "") or "") == "canary":
        # A provider-active request whose canonical cursor cannot be committed is not a clean
        # Canary Run. ``record_canary_error`` is run-id idempotent and also covers DB timeouts
        # where the failed save could not update cohort counters itself.
        await thread_projection_store.record_canary_error(
            thread_id=str(committed.thread_id or ""),
            model=str(committed.model or ""),
            transport=str(committed.transport or ""),
            run_id=str(run_id or ""),
        )
    return saved
