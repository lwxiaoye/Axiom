"""A recovered Run without run.started must not gain the host timezone offset."""

import asyncio
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.tasks import task_run_service as service


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, run, events):
        self.run = run
        self.rows = iter([[run], events, []])
        self.events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, *_args):
        # The replay query follows this owner lookup, while trace reads start with runs.
        self.rows = iter([self.events])
        return self.run

    async def execute(self, *_args):
        return _Result(next(self.rows, []))


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="requires process timezone support")
@pytest.mark.parametrize("host_zone", ["UTC", "Asia/Singapore", "America/New_York"])
@pytest.mark.parametrize("stored_event_timestamp", [True, False])
def test_recovered_history_and_replay_keep_utc_time(monkeypatch, host_zone, stored_event_timestamp):
    started = datetime(2026, 9, 4, 3, 54, 0)
    ended = datetime(2026, 9, 4, 3, 55, 0)
    start_ms = int(started.replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = start_ms + 60_000
    run = SimpleNamespace(
        id="timezone-run", thread_id="timezone-thread", user_id="owner", status="completed",
        created_at=started, completed_at=None, state={},
    )
    event = SimpleNamespace(
        id=1, run_id=run.id, event_id="timezone-event", sequence=1,
        type="run.completed", created_at=ended,
        data={"message_id": 42, **({"_event_timestamp": end_ms} if stored_event_timestamp else {})},
    )
    monkeypatch.setattr(service, "runtime_session", lambda: lambda: _Session(run, [event]))
    try:
        with monkeypatch.context() as local:
            local.setenv("TZ", host_zone)
            time.tzset()
            trace = asyncio.run(service.get_execution_traces_by_thread(run.thread_id))[42]
            assert trace["startedAt"] == start_ms
            assert trace["completedAt"] == end_ms
            assert trace["durationMs"] == 60_000
            assert trace["status"] == "completed"
            payloads = asyncio.run(service.list_event_payloads(run.id, run.user_id, use_cache=False))
            payload = json.loads(payloads[0].removeprefix("data: ").strip())
            assert payload["timestamp"] == end_ms
            assert "_event_timestamp" not in payload["data"]
    finally:
        time.tzset()
