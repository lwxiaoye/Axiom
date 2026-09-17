"""Regression coverage for the main-chat history active-Run batch lookup."""
import asyncio
from datetime import datetime
from types import SimpleNamespace


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.execute_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, _statement):
        self.execute_count += 1
        return _Result(self.rows)


def _run_row(thread_id, run_id, status):
    return SimpleNamespace(
        id=run_id,
        thread_id=thread_id,
        user_id="user-1",
        status=status,
        kind="chat",
        model="model-1",
        agent_mode="standard",
        state={"phase": "executing"},
        subagent_id=None,
        resume_token=None,
        owner_instance_id=None,
        heartbeat_at=None,
        created_at=datetime(2026, 9, 7, 10, 0, 0),
        completed_at=None,
    )


def test_active_run_lookup_uses_one_runtime_query_for_many_threads(monkeypatch):
    from app.services.tasks import task_run_service

    session = _Session([
        _run_row("thread-1", "run-1", "running"),
        _run_row("thread-2", "run-2", "waiting_user"),
    ])
    monkeypatch.setattr(task_run_service, "runtime_session", lambda: lambda: session)

    active = asyncio.run(task_run_service.get_active_runs(
        ["thread-1", "thread-2", "thread-1", ""], "user-1",
    ))

    assert session.execute_count == 1
    assert set(active) == {"thread-1", "thread-2"}
    assert active["thread-1"]["id"] == "run-1"
    assert active["thread-2"]["status"] == "waiting_user"


def test_batch_resolution_only_refreshes_zombie_runs(monkeypatch):
    from app.services.chat.run_hub import RunHub
    from app.services.tasks import task_run_service

    initial = {
        "thread-1": {"id": "run-1", "status": "running"},
        "thread-2": {"id": "run-2", "status": "waiting_user"},
    }
    refreshed = {"thread-1": {"id": "run-1", "status": "waiting_system"}}
    lookups = []
    recoveries = []

    async def get_active_runs(thread_ids, _user_id):
        lookups.append(list(thread_ids))
        return initial if len(lookups) == 1 else refreshed

    hub = RunHub()

    async def is_zombie(run):
        return run["id"] == "run-1"

    async def recover(run_id, _run):
        recoveries.append(run_id)
        return True

    monkeypatch.setattr(task_run_service, "get_active_runs", get_active_runs)
    monkeypatch.setattr(hub, "_is_zombie_run", is_zombie)
    monkeypatch.setattr(hub, "_try_recover_task_zombie", recover)

    active = asyncio.run(hub._resolve_active_runs(["thread-1", "thread-2"], "user-1"))

    assert lookups == [["thread-1", "thread-2"], ["thread-1"]]
    assert recoveries == ["run-1"]
    assert active["thread-1"]["status"] == "waiting_system"
    assert active["thread-2"]["status"] == "waiting_user"


def test_history_list_resolves_active_runs_once_for_the_page(monkeypatch):
    from app.services.agent_harness import orchestrator as orchestrator_module

    rows = [
        SimpleNamespace(
            id="thread-1", title="会话 1", origin=None, pinned=0,
            created_at=datetime(2026, 9, 7, 10, 0, 0),
            updated_at=datetime(2026, 9, 7, 10, 1, 0), model=None,
        ),
        SimpleNamespace(
            id="thread-2", title="会话 2", origin=None, pinned=1,
            created_at=datetime(2026, 9, 7, 9, 0, 0),
            updated_at=datetime(2026, 9, 7, 9, 1, 0), model="model-2",
        ),
    ]
    session = _Session(rows)
    monkeypatch.setattr(orchestrator_module, "async_session", lambda: session)
    service = orchestrator_module.HarnessOrchestrator()
    calls = []

    async def resolve_active_runs(thread_ids, user_id):
        calls.append((list(thread_ids), user_id))
        return {"thread-2": {"id": "run-2", "status": "running"}}

    monkeypatch.setattr(service, "_resolve_active_runs", resolve_active_runs)

    result = asyncio.run(service.get_threads("user-1", limit=30))

    assert calls == [(["thread-1", "thread-2"], "user-1")]
    assert result[0]["active_run"] is None
    assert result[1]["active_run"]["id"] == "run-2"
