"""最小 RunState execution-control 与 Job lease 回归。"""

import asyncio
from datetime import datetime, timedelta

from app.runtime_models import AgentRunJob
from app.services.agent_harness import run_store


def test_new_run_state_contains_execution_control_defaults():
    state = run_store.new_run_state()

    assert state["execution_control"] == {
        "segment_index": 0,
        "checkpoint_sequence": 0,
        "recovery_reason": None,
        "last_progress_at": None,
        "recovery_count": 0,
    }
    assert state["loop_checkpoint"] is None


def test_legacy_state_is_read_with_defaults_without_mutation():
    legacy = {"schema_version": 1, "phase": "executing"}

    control = run_store.get_execution_control(legacy)

    assert control == {
        "segment_index": 0,
        "checkpoint_sequence": 0,
        "recovery_reason": None,
        "last_progress_at": None,
        "recovery_count": 0,
    }
    assert "execution_control" not in legacy


def test_record_recovery_checkpoint_uses_run_state_cas(monkeypatch):
    seen = []

    async def fake_get_run_state(run_id):
        assert run_id == "run-1"
        return {
            "state": {
                "schema_version": 1,
                "execution_control": {
                    "segment_index": 2,
                    "checkpoint_sequence": 8,
                    "recovery_count": 3,
                },
            },
            "version": 11,
        }

    async def fake_transition(run_id, *, expected_version, patch, phase=None):
        seen.append((run_id, expected_version, patch, phase))
        return {"state": patch, "version": expected_version + 1}

    monkeypatch.setattr(run_store, "get_run_state", fake_get_run_state)
    monkeypatch.setattr(run_store, "transition_run_state", fake_transition)

    result = asyncio.run(run_store.record_recovery_checkpoint(
        "run-1",
        recovery_reason="worker_restart",
        progress_at="2026-08-19T12:00:00",
    ))

    assert result == {
        "state": {
            "execution_control": {
                "segment_index": 3,
                "checkpoint_sequence": 9,
                "recovery_reason": "worker_restart",
                "last_progress_at": "2026-08-19T12:00:00",
                "recovery_count": 4,
            },
        },
        "version": 12,
    }
    assert seen == [(
        "run-1",
        11,
        {"execution_control": {
            "segment_index": 3,
            "checkpoint_sequence": 9,
            "recovery_reason": "worker_restart",
            "last_progress_at": "2026-08-19T12:00:00",
            "recovery_count": 4,
        }},
        None,
    )]


def test_enqueue_job_keeps_a_fresh_lease(monkeypatch):
    existing = AgentRunJob(
        id="job-1",
        run_id="run-1",
        status="leased",
        lease_owner="worker-1",
        lease_expires_at=datetime.utcnow() + timedelta(minutes=1),
        attempt_count=2,
        wake_reason="accepted",
    )

    class FakeSession:
        bind = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def scalar(self, statement):  # noqa: ARG002
            return existing

        async def commit(self):
            return None

    monkeypatch.setattr(run_store, "runtime_session", lambda: (lambda: FakeSession()))

    assert asyncio.run(run_store.enqueue_job("run-1", wake_reason="input")) is True
    assert existing.status == "leased"
    assert existing.lease_owner == "worker-1"
    assert existing.attempt_count == 2
    assert existing.wake_reason == "input"
    assert existing.lease_expires_at is not None


def test_enqueue_job_keeps_recovery_deadline_on_a_fresh_lease(monkeypatch):
    deadline = datetime.utcnow() + timedelta(seconds=30)
    existing = AgentRunJob(
        id="job-1",
        run_id="run-1",
        status="leased",
        lease_owner="worker-1",
        lease_expires_at=datetime.utcnow() + timedelta(minutes=1),
        available_at=None,
        wake_reason=None,
    )

    class FakeSession:
        bind = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def scalar(self, statement):  # noqa: ARG002
            return existing

        async def commit(self):
            return None

    monkeypatch.setattr(run_store, "runtime_session", lambda: (lambda: FakeSession()))

    assert asyncio.run(run_store.enqueue_job(
        "run-1", wake_reason="completion_gap", available_at=deadline,
    )) is True
    assert existing.status == "leased"
    assert existing.wake_reason == "completion_gap"
    assert existing.available_at == deadline


def test_new_recovery_deadline_replaces_consumed_legacy_deadline():
    now = datetime.utcnow()
    old_deadline = now - timedelta(seconds=1)
    new_deadline = now + timedelta(seconds=30)

    assert run_store._merge_job_available_at(
        old_deadline, new_deadline, now=now,
    ) == new_deadline


def test_finish_job_requeues_when_recovery_wake_arrives_during_lease(monkeypatch):
    existing = AgentRunJob(
        id="job-1",
        run_id="run-1",
        status="leased",
        lease_owner="worker-1",
        lease_expires_at=datetime.utcnow() + timedelta(minutes=1),
        wake_reason="recovery",
    )

    class FakeSession:
        bind = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, model, job_id, with_for_update=False):  # noqa: ARG002
            assert with_for_update is True
            assert job_id == "job-1"
            return existing

        async def commit(self):
            return None

    monkeypatch.setattr(run_store, "runtime_session", lambda: (lambda: FakeSession()))

    assert asyncio.run(run_store.finish_job("job-1", "worker-1", status="waiting")) is True
    assert existing.status == "queued"
    assert existing.lease_owner is None
    assert existing.lease_expires_at is None
    assert existing.wake_reason == "recovery"


def test_finish_job_requeues_every_pending_wake_and_preserves_deadline(monkeypatch):
    deadline = datetime.utcnow() + timedelta(seconds=30)

    class FakeSession:
        bind = None

        def __init__(self, existing):
            self.existing = existing

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, model, job_id, with_for_update=False):  # noqa: ARG002
            assert with_for_update is True
            assert job_id == "job-1"
            return self.existing

        async def commit(self):
            return None

    for reason in ("input", "finalizer_recovery", "completion_gap"):
        existing = AgentRunJob(
            id="job-1",
            run_id="run-1",
            status="leased",
            lease_owner="worker-1",
            lease_expires_at=datetime.utcnow() + timedelta(minutes=1),
            available_at=deadline,
            wake_reason=reason,
        )
        monkeypatch.setattr(
            run_store, "runtime_session", lambda existing=existing: (
                lambda: FakeSession(existing)
            ),
        )

        assert asyncio.run(run_store.finish_job("job-1", "worker-1", status="waiting")) is True
        assert existing.status == "queued"
        assert existing.available_at == deadline
        assert existing.wake_reason == reason


def test_finish_job_can_handoff_worker_recovery_in_one_locked_update(monkeypatch):
    deadline = datetime.utcnow() + timedelta(seconds=30)
    existing = AgentRunJob(
        id="job-1",
        run_id="run-1",
        status="leased",
        lease_owner="worker-1",
        lease_expires_at=datetime.utcnow() + timedelta(minutes=1),
    )

    class FakeSession:
        bind = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, model, job_id, with_for_update=False):  # noqa: ARG002
            assert with_for_update is True
            return existing

        async def commit(self):
            return None

    monkeypatch.setattr(run_store, "runtime_session", lambda: (lambda: FakeSession()))

    assert asyncio.run(run_store.finish_job(
        "job-1",
        "worker-1",
        status="waiting",
        wake_reason="recovery",
        available_at=deadline,
    )) is True
    assert existing.status == "queued"
    assert existing.wake_reason == "recovery"
    assert existing.available_at == deadline
