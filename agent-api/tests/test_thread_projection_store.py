from __future__ import annotations

import asyncio
from types import SimpleNamespace

import app.services.agent_harness.thread_projection_store as store_module
from app.services.agent_harness.context import ProjectionLedgerState


def _state() -> ProjectionLedgerState:
    return ProjectionLedgerState(
        thread_id="thread-1",
        model="deepseek-v4-flash",
        transport="responses",
        context_epoch=0,
        epoch_reason="initial",
        base_prompt_hash="base",
        tool_schema_hash="tools",
        state_snapshot_hash="state",
        source_cursor=0,
        source_history_hash="history",
        projected_items=(),
    )


def test_projection_store_fails_open_without_runtime_db(monkeypatch):
    monkeypatch.setattr(store_module, "runtime_session", lambda: None)
    store = store_module.ThreadProjectionStore(timeout_seconds=0.05)

    loaded = asyncio.run(store.load(
        thread_id="thread-1",
        model="deepseek-v4-flash",
        transport="responses",
    ))
    saved = asyncio.run(store.save(_state()))

    assert loaded is None
    assert saved is False


def test_projection_store_fails_open_when_orm_is_not_loaded(monkeypatch):
    monkeypatch.setattr(store_module, "runtime_session", lambda: object())
    monkeypatch.setattr(store_module, "_ledger_model", lambda: None)
    store = store_module.ThreadProjectionStore(timeout_seconds=0.05)

    assert asyncio.run(store.load(
        thread_id="thread-1",
        model="deepseek-v4-flash",
        transport="responses",
    )) is None
    assert asyncio.run(store.save(_state())) is False


def test_projection_store_fails_open_when_runtime_bootstrap_raises(monkeypatch):
    def _raise_runtime_error():
        raise RuntimeError("runtime schema is unavailable")

    monkeypatch.setattr(store_module, "runtime_session", _raise_runtime_error)
    store = store_module.ThreadProjectionStore(timeout_seconds=0.05)

    assert asyncio.run(store.load(
        thread_id="thread-1",
        model="deepseek-v4-flash",
        transport="responses",
    )) is None
    assert asyncio.run(store.save(_state())) is False


def test_rollout_mode_uses_clean_model_transport_cohort_gates(monkeypatch):
    store = store_module.ThreadProjectionStore(timeout_seconds=0.05)

    async def _shadow_below_gate(**_kwargs):
        return {
            "shadow_eligible_pairs": 99,
            "alignment_error_count": 0,
            "canary_completed_runs": 100,
            "canary_error_count": 0,
        }

    monkeypatch.setattr(store, "rollout_stats", _shadow_below_gate)
    assert asyncio.run(store.effective_rollout_mode(
        "on", model="m", transport="responses"
    )) == "shadow"

    async def _shadow_has_alignment_error(**_kwargs):
        return {
            "shadow_eligible_pairs": 0,
            "alignment_error_count": 1,
            "canary_completed_runs": 100,
            "canary_error_count": 0,
        }

    monkeypatch.setattr(store, "rollout_stats", _shadow_has_alignment_error)
    assert asyncio.run(store.effective_rollout_mode(
        "on", model="m", transport="responses"
    )) == "shadow"
    assert asyncio.run(store.effective_rollout_mode(
        "canary", model="m", transport="responses"
    )) == "shadow"

    async def _canary_below_gate(**_kwargs):
        return {
            "shadow_eligible_pairs": 100,
            "alignment_error_count": 0,
            "canary_completed_runs": 99,
            "canary_error_count": 0,
        }

    monkeypatch.setattr(store, "rollout_stats", _canary_below_gate)
    assert asyncio.run(store.effective_rollout_mode(
        "on", model="m", transport="responses"
    )) == "canary"

    async def _unexpected_reset(**_kwargs):
        return {
            "shadow_eligible_pairs": 0,
            "alignment_error_count": 0,
            "unexpected_reset_count": 1,
            "canary_completed_runs": 100,
            "canary_error_count": 0,
        }

    monkeypatch.setattr(store, "rollout_stats", _unexpected_reset)
    assert asyncio.run(store.effective_rollout_mode(
        "on", model="m", transport="responses"
    )) == "shadow"

    async def _canary_has_error(**_kwargs):
        return {
            "shadow_eligible_pairs": 100,
            "alignment_error_count": 0,
            "canary_completed_runs": 0,
            "canary_error_count": 1,
        }

    monkeypatch.setattr(store, "rollout_stats", _canary_has_error)
    assert asyncio.run(store.effective_rollout_mode(
        "on", model="m", transport="responses"
    )) == "canary"

    async def _on_ready(**_kwargs):
        return {
            "shadow_eligible_pairs": 100,
            "alignment_error_count": 0,
            "canary_completed_runs": 100,
            "canary_error_count": 0,
        }

    monkeypatch.setattr(store, "rollout_stats", _on_ready)
    assert asyncio.run(store.effective_rollout_mode(
        "canary", model="m", transport="responses"
    )) == "canary"
    assert asyncio.run(store.effective_rollout_mode(
        "on", model="m", transport="responses"
    )) == "on"


def test_projection_save_rejects_stale_revision_without_overwriting(monkeypatch):
    row = SimpleNamespace(
        storage_revision=2,
        source_history_hash="newer-history",
        projected_items=[{"role": "user", "content": "newer"}],
        alignment_error_count=0,
        shadow_eligible_pairs=12,
        canary_error_count=0,
        canary_completed_runs=0,
    )

    class Result:
        class Scalars:
            @staticmethod
            def first():
                return row

        def scalars(self):
            return self.Scalars()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, _statement, _params=None):
            return Result()

        async def commit(self):
            return None

    cohort_events = []

    async def _cohort(_session, **kwargs):
        cohort_events.append(kwargs)

    monkeypatch.setattr(store_module, "runtime_session", lambda: Session)
    monkeypatch.setattr(store_module, "_apply_cohort_event", _cohort)
    stale = _state().model_copy(update={
        "storage_revision": 1,
        "source_history_hash": "stale-history",
        "projected_items": ({"role": "user", "content": "stale"},),
    })
    saved = asyncio.run(store_module.ThreadProjectionStore().save(stale, run_id="run-old"))
    assert saved is False
    assert row.source_history_hash == "newer-history"
    assert row.projected_items == [{"role": "user", "content": "newer"}]
    assert row.alignment_error_count == 1
    assert row.shadow_eligible_pairs == 0
    assert cohort_events[0]["alignment_error_delta"] == 1

    row.last_canary_error_run_id = None
    row.canary_error_count = 0
    row.canary_completed_runs = 7
    stale_canary = stale.model_copy(update={"mode": "canary"})
    saved_canary = asyncio.run(
        store_module.ThreadProjectionStore().save(
            stale_canary,
            run_id="run-canary-conflict",
        )
    )
    assert saved_canary is False
    assert row.last_canary_error_run_id == "run-canary-conflict"
    assert row.canary_error_count == 1
    assert row.canary_completed_runs == 0
    assert cohort_events[-1]["canary_error_delta"] == 1


def test_canary_candidate_mode_is_durable_before_the_provider_request(monkeypatch):
    row = SimpleNamespace(
        mode="shadow",
        storage_revision=1,
        last_canary_candidate_run_id=None,
    )

    class Result:
        class Scalars:
            @staticmethod
            def first():
                return row

        def scalars(self):
            return self.Scalars()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, _statement, _params=None):
            return Result()

        async def commit(self):
            return None

    monkeypatch.setattr(store_module, "runtime_session", lambda: Session)
    marked = asyncio.run(store_module.ThreadProjectionStore().mark_canary_candidate(
        thread_id="thread-1",
        model="m",
        transport="responses",
        run_id="run-canary",
        expected_revision=1,
    ))

    assert marked is True
    assert row.mode == "canary"
    assert row.last_canary_candidate_run_id == "run-canary"
    assert row.storage_revision == 2

    # A second worker may have loaded revision 1 before the first marker committed. The marker
    # itself must advance the CAS token so that stale worker cannot replace root-Run ownership.
    marked_again = asyncio.run(
        store_module.ThreadProjectionStore().mark_canary_candidate(
            thread_id="thread-1",
            model="m",
            transport="responses",
            run_id="run-racing",
            expected_revision=1,
        )
    )
    assert marked_again is False
    assert row.last_canary_candidate_run_id == "run-canary"
    assert row.storage_revision == 2
    assert row.alignment_error_count == 1
    assert row.unexpected_reset_count == 1
    assert row.shadow_eligible_pairs == 0


def test_canary_candidate_rejects_a_stale_projection_revision(monkeypatch):
    row = SimpleNamespace(
        mode="shadow",
        storage_revision=2,
        last_canary_candidate_run_id=None,
    )

    class Result:
        class Scalars:
            @staticmethod
            def first():
                return row

        def scalars(self):
            return self.Scalars()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, _statement, _params=None):
            return Result()

        async def commit(self):
            return None

    monkeypatch.setattr(store_module, "runtime_session", lambda: Session)
    marked = asyncio.run(store_module.ThreadProjectionStore().mark_canary_candidate(
        thread_id="thread-1",
        model="m",
        transport="responses",
        run_id="run-stale",
        expected_revision=1,
    ))

    assert marked is False
    assert row.mode == "shadow"
    assert row.last_canary_candidate_run_id is None
    assert row.alignment_error_count == 1
    assert row.unexpected_reset_count == 1


def test_canary_root_terminal_survives_later_shadow_reset_and_is_idempotent(monkeypatch):
    row = SimpleNamespace(
        model="m",
        transport="responses",
        # A compaction/protocol reset may persist a new shadow baseline after this Run already
        # sent a marked canary request. The marker, not the row's latest mode, is terminal truth.
        mode="shadow",
        last_canary_candidate_run_id="run-1",
        last_canary_accounted_run_id=None,
        last_canary_error_run_id=None,
        canary_completed_runs=0,
        canary_error_count=0,
    )
    run = SimpleNamespace(id="run-1", root_run_id=None)
    statements = []

    class Result:
        class Scalars:
            @staticmethod
            def all():
                return [row]

        def scalars(self):
            return self.Scalars()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _model, _key):
            return run

        async def execute(self, _statement, _params=None):
            statements.append(_statement)
            return Result()

        async def commit(self):
            return None

    events = []

    async def _cohort(_session, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(store_module, "runtime_session", lambda: Session)
    monkeypatch.setattr(store_module, "_apply_cohort_event", _cohort)
    store = store_module.ThreadProjectionStore()
    assert asyncio.run(store.record_root_run_terminal(run_id="run-1", succeeded=True)) is True
    assert asyncio.run(store.record_root_run_terminal(run_id="run-1", succeeded=True)) is False
    assert row.canary_completed_runs == 1
    assert row.last_canary_accounted_run_id == "run-1"
    assert [event["canary_clean_delta"] for event in events] == [1]
    assert all(".mode" not in str(statement.whereclause) for statement in statements)
