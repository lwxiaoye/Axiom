"""Fail-open persistence for cross-Run context projection shadow state."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import select, text

from app.core.runtime_db import runtime_session

from .context import ProjectionLedgerState


logger = logging.getLogger(__name__)
SHADOW_PAIR_GATE = 100
CANARY_RUN_GATE = 100


def _ledger_model() -> Any | None:
    """Resolve the optional ORM lazily so staggered Runtime migrations stay fail-open."""
    try:
        from app import runtime_models

        return getattr(runtime_models, "AgentThreadContextLedger", None)
    except Exception:  # noqa: BLE001 - optional Runtime capability
        return None


def _cohort_model() -> Any | None:
    try:
        from app import runtime_models

        return getattr(runtime_models, "AgentThreadProjectionRolloutCohort", None)
    except Exception:  # noqa: BLE001 - optional Runtime capability
        return None


async def _apply_cohort_event(
    session: Any,
    *,
    model: str,
    transport: str,
    shadow_clean_delta: int = 0,
    alignment_error_delta: int = 0,
    expected_reset_delta: int = 0,
    unexpected_reset_delta: int = 0,
    canary_clean_delta: int = 0,
    canary_error_delta: int = 0,
) -> None:
    """Atomically advance or reset one rollout cohort across all workers."""

    await session.execute(
        text(
            """INSERT INTO agent_thread_projection_rollout_cohorts (
                model, transport, shadow_clean_pairs, alignment_error_count,
                expected_reset_count, unexpected_reset_count,
                canary_clean_runs, canary_error_count, updated_at
            ) VALUES (
                :model, :transport,
                CASE WHEN :alignment_error_delta > 0 THEN 0 ELSE :shadow_clean_delta END,
                :alignment_error_delta,
                :expected_reset_delta, :unexpected_reset_delta,
                CASE WHEN :canary_error_delta > 0 THEN 0 ELSE :canary_clean_delta END,
                :canary_error_delta,
                NOW()
            )
            ON CONFLICT (model, transport) DO UPDATE SET
                shadow_clean_pairs = CASE
                    WHEN :alignment_error_delta > 0 THEN 0
                    ELSE agent_thread_projection_rollout_cohorts.shadow_clean_pairs
                         + :shadow_clean_delta
                END,
                alignment_error_count =
                    agent_thread_projection_rollout_cohorts.alignment_error_count
                    + :alignment_error_delta,
                expected_reset_count =
                    agent_thread_projection_rollout_cohorts.expected_reset_count
                    + :expected_reset_delta,
                unexpected_reset_count =
                    agent_thread_projection_rollout_cohorts.unexpected_reset_count
                    + :unexpected_reset_delta,
                canary_clean_runs = CASE
                    WHEN :canary_error_delta > 0 THEN 0
                    ELSE agent_thread_projection_rollout_cohorts.canary_clean_runs
                         + :canary_clean_delta
                END,
                canary_error_count =
                    agent_thread_projection_rollout_cohorts.canary_error_count
                    + :canary_error_delta,
                updated_at = NOW()"""
        ),
        {
            "model": str(model or "")[:255],
            "transport": str(transport or "")[:32],
            "shadow_clean_delta": max(0, int(shadow_clean_delta)),
            "alignment_error_delta": max(0, int(alignment_error_delta)),
            "expected_reset_delta": max(0, int(expected_reset_delta)),
            "unexpected_reset_delta": max(0, int(unexpected_reset_delta)),
            "canary_clean_delta": max(0, int(canary_clean_delta)),
            "canary_error_delta": max(0, int(canary_error_delta)),
        },
    )


class ThreadProjectionStore:
    """Store one latest projection per ``(thread, model, transport)``.

    Persistence never grants provider reuse. The row's default ``mode=shadow`` is interpreted by
    :class:`ContextProjectionLedger`; only an explicitly promoted canary can become provider-active.
    """

    def __init__(self, *, timeout_seconds: float = 0.75) -> None:
        self._timeout_seconds = max(0.05, float(timeout_seconds))

    @staticmethod
    def _state_from_row(row: Any) -> ProjectionLedgerState:
        return ProjectionLedgerState(
            version=int(getattr(row, "version", 1) or 1),
            storage_revision=max(
                0, int(getattr(row, "storage_revision", 0) or 0)
            ),
            mode=str(getattr(row, "mode", "shadow") or "shadow"),
            thread_id=str(row.thread_id or ""),
            model=str(row.model or ""),
            transport=str(row.transport or ""),
            context_epoch=max(0, int(row.context_epoch or 0)),
            epoch_reason=str(row.epoch_reason or "initial"),
            base_prompt_hash=str(row.base_prompt_hash or ""),
            tool_schema_hash=str(row.tool_schema_hash or ""),
            state_snapshot_hash=str(row.state_snapshot_hash or ""),
            source_cursor=max(0, int(row.source_cursor or 0)),
            source_history_hash=str(row.source_history_hash or ""),
            display_history_count=max(
                0, int(getattr(row, "display_history_count", 0) or 0)
            ),
            display_history_hash=str(
                getattr(row, "display_history_hash", "") or ""
            ),
            projected_items=tuple(
                item
                for item in (row.projected_items or [])
                if isinstance(item, dict)
            ),
        )

    async def load(
        self,
        *,
        thread_id: str,
        model: str,
        transport: str,
    ) -> ProjectionLedgerState | None:
        try:
            factory = runtime_session()
            orm = _ledger_model()
        except Exception:  # noqa: BLE001 - Runtime bootstrap is optional here
            logger.debug("thread projection shadow store is unavailable", exc_info=True)
            return None
        if factory is None or orm is None or not thread_id or not model or not transport:
            return None

        async def _read() -> ProjectionLedgerState | None:
            async with factory() as session:
                result = await session.execute(
                    select(orm).where(
                        orm.thread_id == str(thread_id),
                        orm.model == str(model),
                        orm.transport == str(transport),
                    ).limit(1)
                )
                row = result.scalars().first()
                if row is None:
                    return None
                return self._state_from_row(row)

        try:
            return await asyncio.wait_for(_read(), timeout=self._timeout_seconds)
        except Exception:  # noqa: BLE001 - projection shadow must never block a model request
            logger.debug(
                "thread projection shadow load skipped thread=%s model=%s transport=%s",
                thread_id,
                model,
                transport,
                exc_info=True,
            )
            return None

    async def load_transport_transition_candidates(
        self,
        *,
        thread_id: str,
        model: str,
        target_transport: str,
    ) -> list[ProjectionLedgerState]:
        """Load recent alternate-transport rows; callers still verify the public fingerprint."""

        try:
            factory = runtime_session()
            orm = _ledger_model()
        except Exception:  # noqa: BLE001 - optional Runtime capability
            return []
        if factory is None or orm is None or not thread_id or not model:
            return []

        async def _read() -> list[ProjectionLedgerState]:
            async with factory() as session:
                result = await session.execute(
                    select(orm).where(
                        orm.thread_id == str(thread_id),
                        orm.model == str(model),
                        orm.transport != str(target_transport),
                    ).order_by(
                        orm.updated_at.desc(),
                        orm.transport.asc(),
                    ).limit(4)
                )
                return [self._state_from_row(row) for row in result.scalars().all()]

        try:
            return await asyncio.wait_for(_read(), timeout=self._timeout_seconds)
        except Exception:  # noqa: BLE001 - transition reuse is an optional optimization
            logger.debug(
                "thread projection transport transition load skipped "
                "thread=%s model=%s target=%s",
                thread_id,
                model,
                target_transport,
                exc_info=True,
            )
            return []

    async def save(
        self,
        state: ProjectionLedgerState,
        *,
        run_id: str = "",
        shadow_eligible_delta: int = 0,
        alignment_error_delta: int = 0,
        expected_reset_delta: int = 0,
        unexpected_reset_delta: int = 0,
        canary_completed_delta: int = 0,
        canary_error_delta: int = 0,
    ) -> bool:
        try:
            factory = runtime_session()
            orm = _ledger_model()
        except Exception:  # noqa: BLE001 - Runtime bootstrap is optional here
            logger.debug("thread projection shadow store is unavailable", exc_info=True)
            return False
        if (
            factory is None
            or orm is None
            or not state.thread_id
            or not state.model
            or not state.transport
        ):
            return False

        async def _write() -> bool:
            async with factory() as session:
                result = await session.execute(
                    select(orm).where(
                        orm.thread_id == state.thread_id,
                        orm.model == state.model,
                        orm.transport == state.transport,
                    ).with_for_update()
                )
                row = result.scalars().first()
                values = {
                    "context_epoch": state.context_epoch,
                    "epoch_reason": state.epoch_reason,
                    "base_prompt_hash": state.base_prompt_hash,
                    "tool_schema_hash": state.tool_schema_hash,
                    "state_snapshot_hash": state.state_snapshot_hash,
                    "source_cursor": state.source_cursor,
                    "source_history_hash": state.source_history_hash,
                    "display_history_count": state.display_history_count,
                    "display_history_hash": state.display_history_hash,
                    "projected_items": list(state.projected_items),
                    "mode": state.mode,
                    "version": state.version,
                    "last_projected_run_id": str(run_id or "")[:64] or None,
                }
                expected_revision = max(0, int(state.storage_revision or 0))
                if row is None:
                    if expected_revision != 0:
                        canary_cas_error = int(
                            state.mode == "canary" and bool(str(run_id or ""))
                        )
                        await _apply_cohort_event(
                            session,
                            model=state.model,
                            transport=state.transport,
                            alignment_error_delta=1,
                            unexpected_reset_delta=1,
                            canary_error_delta=canary_cas_error,
                        )
                        await session.commit()
                        return False
                    row = orm(
                        id=uuid.uuid4().hex,
                        thread_id=state.thread_id,
                        model=state.model,
                        transport=state.transport,
                        shadow_eligible_pairs=(
                            0 if alignment_error_delta else max(0, int(shadow_eligible_delta))
                        ),
                        alignment_error_count=max(0, int(alignment_error_delta)),
                        expected_reset_count=max(0, int(expected_reset_delta)),
                        unexpected_reset_count=max(0, int(unexpected_reset_delta)),
                        canary_completed_runs=(
                            0 if canary_error_delta else max(0, int(canary_completed_delta))
                        ),
                        canary_error_count=max(0, int(canary_error_delta)),
                        storage_revision=1,
                        **values,
                    )
                    session.add(row)
                else:
                    current_revision = max(0, int(row.storage_revision or 0))
                    if current_revision != expected_revision:
                        canary_cas_error = int(
                            state.mode == "canary"
                            and bool(str(run_id or ""))
                            and str(
                                getattr(row, "last_canary_error_run_id", "") or ""
                            )
                            != str(run_id)
                        )
                        row.alignment_error_count = int(row.alignment_error_count or 0) + 1
                        row.unexpected_reset_count = int(
                            getattr(row, "unexpected_reset_count", 0) or 0
                        ) + 1
                        row.shadow_eligible_pairs = 0
                        if canary_cas_error:
                            row.last_canary_error_run_id = str(run_id)[:64]
                            row.canary_error_count = int(
                                getattr(row, "canary_error_count", 0) or 0
                            ) + 1
                            row.canary_completed_runs = 0
                        await _apply_cohort_event(
                            session,
                            model=state.model,
                            transport=state.transport,
                            alignment_error_delta=1,
                            unexpected_reset_delta=1,
                            canary_error_delta=canary_cas_error,
                        )
                        await session.commit()
                        logger.warning(
                            "thread_projection_cas_conflict thread=%s model=%s transport=%s "
                            "expected=%s actual=%s",
                            state.thread_id,
                            state.model,
                            state.transport,
                            expected_revision,
                            current_revision,
                        )
                        return False
                    for key, value in values.items():
                        setattr(row, key, value)
                    row.storage_revision = current_revision + 1
                    if alignment_error_delta:
                        row.alignment_error_count = int(row.alignment_error_count or 0) + int(
                            alignment_error_delta
                        )
                        row.shadow_eligible_pairs = 0
                    else:
                        row.shadow_eligible_pairs = int(row.shadow_eligible_pairs or 0) + max(
                            0, int(shadow_eligible_delta)
                        )
                    row.expected_reset_count = int(
                        getattr(row, "expected_reset_count", 0) or 0
                    ) + max(0, int(expected_reset_delta))
                    row.unexpected_reset_count = int(
                        getattr(row, "unexpected_reset_count", 0) or 0
                    ) + max(0, int(unexpected_reset_delta))
                    if canary_error_delta:
                        row.canary_error_count = int(row.canary_error_count or 0) + int(
                            canary_error_delta
                        )
                        row.canary_completed_runs = 0
                    else:
                        row.canary_completed_runs = int(row.canary_completed_runs or 0) + max(
                            0, int(canary_completed_delta)
                        )
                await _apply_cohort_event(
                    session,
                    model=state.model,
                    transport=state.transport,
                    shadow_clean_delta=shadow_eligible_delta,
                    alignment_error_delta=alignment_error_delta,
                    expected_reset_delta=expected_reset_delta,
                    unexpected_reset_delta=unexpected_reset_delta,
                    canary_clean_delta=canary_completed_delta,
                    canary_error_delta=canary_error_delta,
                )
                await session.commit()
                return True

        try:
            return await asyncio.wait_for(_write(), timeout=self._timeout_seconds)
        except Exception:  # noqa: BLE001 - shadow persistence is observability, not correctness
            logger.warning(
                "thread projection shadow save failed (fail-open) thread=%s model=%s transport=%s",
                state.thread_id,
                state.model,
                state.transport,
                exc_info=True,
            )
            return False

    async def rollout_stats(
        self,
        *,
        model: str,
        transport: str,
    ) -> dict[str, int] | None:
        """Return one model/transport cohort's clean-window counters."""

        try:
            factory = runtime_session()
            orm = _cohort_model()
        except Exception:  # noqa: BLE001
            return None
        if factory is None or orm is None:
            return None

        async def _read() -> dict[str, int]:
            async with factory() as session:
                result = await session.execute(
                    select(orm).where(
                        orm.model == str(model),
                        orm.transport == str(transport),
                    ).limit(1)
                )
                row = result.scalars().first()
                return {
                    "shadow_eligible_pairs": int(
                        getattr(row, "shadow_clean_pairs", 0) or 0
                    ),
                    "alignment_error_count": int(
                        getattr(row, "alignment_error_count", 0) or 0
                    ),
                    "expected_reset_count": int(
                        getattr(row, "expected_reset_count", 0) or 0
                    ),
                    "unexpected_reset_count": int(
                        getattr(row, "unexpected_reset_count", 0) or 0
                    ),
                    "canary_completed_runs": int(
                        getattr(row, "canary_clean_runs", 0) or 0
                    ),
                    "canary_error_count": int(
                        getattr(row, "canary_error_count", 0) or 0
                    ),
                }

        try:
            return await asyncio.wait_for(_read(), timeout=self._timeout_seconds)
        except Exception:  # noqa: BLE001
            logger.debug("thread projection rollout stats unavailable", exc_info=True)
            return None

    async def mark_canary_candidate(
        self,
        *,
        thread_id: str,
        model: str,
        transport: str,
        run_id: str,
        expected_revision: int | None = None,
    ) -> bool:
        """Mark a root Run as having actually received a canary projection."""

        try:
            factory = runtime_session()
            orm = _ledger_model()
        except Exception:  # noqa: BLE001
            return False
        if factory is None or orm is None or not run_id:
            return False

        async def _write() -> bool:
            async with factory() as session:
                result = await session.execute(
                    select(orm).where(
                        orm.thread_id == str(thread_id),
                        orm.model == str(model),
                        orm.transport == str(transport),
                    ).with_for_update()
                )
                row = result.scalars().first()
                if row is None:
                    return False
                current_revision = max(
                    0, int(getattr(row, "storage_revision", 0) or 0)
                )
                if expected_revision is not None and current_revision != max(
                    0, int(expected_revision or 0)
                ):
                    row.alignment_error_count = int(
                        getattr(row, "alignment_error_count", 0) or 0
                    ) + 1
                    row.unexpected_reset_count = int(
                        getattr(row, "unexpected_reset_count", 0) or 0
                    ) + 1
                    row.shadow_eligible_pairs = 0
                    await _apply_cohort_event(
                        session,
                        model=str(model),
                        transport=str(transport),
                        alignment_error_delta=1,
                        unexpected_reset_delta=1,
                    )
                    await session.commit()
                    logger.warning(
                        "thread projection canary marker CAS conflict "
                        "thread=%s model=%s transport=%s expected=%s actual=%s",
                        thread_id,
                        model,
                        transport,
                        max(0, int(expected_revision or 0)),
                        current_revision,
                    )
                    return False
                # Candidate selection changes the live Provider body. Persist the mode before the
                # request so a worker crash cannot hide the failed canary from terminal accounting.
                # It is also a real CAS write: advancing the revision prevents a second request
                # that loaded the same old row from overwriting this Run's marker.
                row.mode = "canary"
                row.last_canary_candidate_run_id = str(run_id)[:64]
                row.storage_revision = current_revision + 1
                await session.commit()
                return True

        try:
            return await asyncio.wait_for(_write(), timeout=self._timeout_seconds)
        except Exception:  # noqa: BLE001
            logger.warning("thread projection canary candidate audit failed", exc_info=True)
            return False

    async def record_canary_error(
        self,
        *,
        thread_id: str,
        model: str,
        transport: str,
        run_id: str,
    ) -> bool:
        """Mark one canary Run dirty and reset its cohort's clean Run window once."""

        try:
            factory = runtime_session()
            orm = _ledger_model()
        except Exception:  # noqa: BLE001
            return False
        if factory is None or orm is None or not run_id:
            return False

        async def _write() -> bool:
            async with factory() as session:
                result = await session.execute(
                    select(orm).where(
                        orm.thread_id == str(thread_id),
                        orm.model == str(model),
                        orm.transport == str(transport),
                    ).with_for_update()
                )
                row = result.scalars().first()
                if row is None:
                    return False
                if str(row.last_canary_error_run_id or "") == str(run_id):
                    return True
                row.last_canary_error_run_id = str(run_id)[:64]
                row.canary_error_count = int(row.canary_error_count or 0) + 1
                row.canary_completed_runs = 0
                await _apply_cohort_event(
                    session,
                    model=model,
                    transport=transport,
                    canary_error_delta=1,
                )
                await session.commit()
                return True

        try:
            return await asyncio.wait_for(_write(), timeout=self._timeout_seconds)
        except Exception:  # noqa: BLE001
            logger.warning(
                "thread projection canary error audit failed thread=%s model=%s transport=%s",
                thread_id,
                model,
                transport,
                exc_info=True,
            )
            return False

    async def record_root_run_terminal(self, *, run_id: str, succeeded: bool) -> bool:
        """Count a canary root Run once, after its authoritative Runtime terminal CAS."""

        try:
            factory = runtime_session()
            orm = _ledger_model()
            from app.runtime_models import AgentRun
        except Exception:  # noqa: BLE001
            return False
        if factory is None or orm is None or not run_id:
            return False

        async def _write() -> bool:
            async with factory() as session:
                run = await session.get(AgentRun, str(run_id))
                if run is None or str(run.root_run_id or run.id) != str(run.id):
                    return False
                result = await session.execute(
                    select(orm).where(
                        orm.last_canary_candidate_run_id == str(run_id),
                    ).with_for_update()
                )
                rows = [
                    row for row in result.scalars().all()
                    if str(row.last_canary_accounted_run_id or "") != str(run_id)
                ]
                if not rows:
                    return False
                grouped: dict[tuple[str, str], list[Any]] = {}
                for row in rows:
                    grouped.setdefault((str(row.model), str(row.transport)), []).append(row)
                for (model_name, transport_name), cohort_rows in grouped.items():
                    dirty_already_recorded = any(
                        str(row.last_canary_error_run_id or "") == str(run_id)
                        for row in cohort_rows
                    )
                    clean = bool(succeeded) and not dirty_already_recorded
                    for row in cohort_rows:
                        row.last_canary_accounted_run_id = str(run_id)[:64]
                    representative = cohort_rows[0]
                    if clean:
                        representative.canary_completed_runs = int(
                            representative.canary_completed_runs or 0
                        ) + 1
                        await _apply_cohort_event(
                            session,
                            model=model_name,
                            transport=transport_name,
                            canary_clean_delta=1,
                        )
                    elif not dirty_already_recorded:
                        representative.canary_error_count = int(
                            representative.canary_error_count or 0
                        ) + 1
                        representative.canary_completed_runs = 0
                        await _apply_cohort_event(
                            session,
                            model=model_name,
                            transport=transport_name,
                            canary_error_delta=1,
                        )
                await session.commit()
                return True

        try:
            return await asyncio.wait_for(_write(), timeout=self._timeout_seconds)
        except Exception:  # noqa: BLE001
            logger.warning(
                "thread projection root terminal audit failed run=%s", run_id, exc_info=True
            )
            return False

    async def effective_rollout_mode(
        self,
        requested: str,
        *,
        model: str,
        transport: str,
    ) -> str:
        """Enforce shadow -> 10% canary -> on gates for one validated cohort."""

        normalized = str(requested or "shadow").strip().lower()
        if normalized not in {"shadow", "canary", "on"} or normalized == "shadow":
            return "shadow"
        stats = await self.rollout_stats(model=model, transport=transport)
        if (
            stats is None
            or stats["shadow_eligible_pairs"] < SHADOW_PAIR_GATE
        ):
            return "shadow"
        if normalized == "canary":
            return "canary"
        if (
            stats["canary_completed_runs"] < CANARY_RUN_GATE
        ):
            return "canary"
        return "on"


thread_projection_store = ThreadProjectionStore()
