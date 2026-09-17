from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.chat import history_trace_projection as projection
from app.services.tasks import task_run_service


class _Context:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


class _RuntimeSession:
    async def get(self, _model, run_id):
        return SimpleNamespace(id=run_id, thread_id="thread-1")


class _MysqlSession:
    def __init__(self, row):
        self.row = row
        self.committed = False

    async def get(self, _model, message_id, **_kwargs):
        return self.row if message_id == self.row.id else None

    async def commit(self):
        self.committed = True


def test_projection_round_trip_and_invalid_payload_fail_closed():
    raw = projection.encode_execution_trace_projection({
        "status": "completed",
        "task_plan": [{"key": "research", "title": "检索来源", "status": "completed"}],
        "steps": [{"kind": "tool", "name": "search_web", "status": "completed"}],
    })
    restored = projection.decode_execution_trace_projection(raw)
    assert restored is not None
    assert restored["projection_version"] == 1
    assert restored["task_plan"][0]["title"] == "检索来源"
    assert projection.decode_execution_trace_projection("not-json") is None


def test_oversized_projection_drops_screenshot_but_keeps_real_steps(monkeypatch):
    monkeypatch.setattr(projection, "_MAX_PROJECTION_BYTES", 4000)
    raw = projection.encode_execution_trace_projection({
        "status": "completed",
        "steps": [{
            "kind": "tool",
            "name": "browser_fetch",
            "status": "completed",
            "label": "已打开来源",
            "shot": "data:image/png;base64," + ("A" * 8000),
        }],
    })
    restored = json.loads(raw)
    assert "shot" not in restored["steps"][0]
    assert restored["steps"][0]["label"] == "已打开来源"


def test_terminal_projection_persists_trace_for_shared_history(monkeypatch):
    row = SimpleNamespace(
        id=42,
        role="assistant",
        run_id="run-1",
        execution_trace_json=None,
    )
    mysql = _MysqlSession(row)
    monkeypatch.setattr(projection, "runtime_session", lambda: lambda: _Context(_RuntimeSession()))
    monkeypatch.setattr(projection, "async_session", lambda: _Context(mysql))

    calls = []

    async def _trace(thread_id, input_messages=None, run_traces=None, only_run_id=None):
        calls.append((thread_id, only_run_id))
        trace = {
            "status": "completed",
            "agent_mode": "research",
            "steps": [{"kind": "tool", "name": "search_web", "status": "completed"}],
        }
        run_traces["run-1"] = trace
        return {42: trace}

    monkeypatch.setattr(task_run_service, "get_execution_traces_by_thread", _trace)
    assert asyncio.run(projection.persist_terminal_execution_trace_projection("run-1", 42))
    assert calls == [("thread-1", "run-1")]
    assert mysql.committed is True
    restored = projection.decode_execution_trace_projection(row.execution_trace_json)
    assert restored["agent_mode"] == "research"
    assert restored["steps"][0]["name"] == "search_web"


def test_history_prefers_current_runtime_then_falls_back_to_projection():
    import inspect
    from app.services.agent_harness import orchestrator

    source = inspect.getsource(orchestrator.HarnessOrchestrator.get_thread_messages)
    assert "trace = execution_map.get(m.id)" in source
    assert "trace = run_traces.get(str(m.run_id))" in source
    assert "trace = stored_trace" in source
    assert source.index("trace = stored_trace") > source.index("trace = execution_map.get(m.id)")


@pytest.mark.parametrize("with_trace", [False, True])
def test_citations_are_saved_on_the_same_message_without_losing_trace(monkeypatch, with_trace):
    initial = {"run_id": "run-1", "steps": [{"kind": "tool", "name": "search_knowledge"}]} if with_trace else {}
    row = SimpleNamespace(id=42, thread_id="thread-1", role="assistant", execution_trace_json=json.dumps(initial))
    mysql = _MysqlSession(row)
    monkeypatch.setattr(projection, "async_session", lambda: _Context(mysql))
    sources = [
        {"type": "knowledge", "title": "报到说明"},
        {"type": "image", "title": "流程图", "url": "/upload/flow.png"},
        {"type": "image", "title": "校区地图", "url": "/upload/map.png"},
    ]

    assert asyncio.run(projection.persist_message_citation_projection("thread-1", 42, sources))
    saved = projection.decode_execution_trace_projection(row.execution_trace_json)
    assert [s["url"] for s in saved["citations"] if s["type"] == "image"] == ["/upload/flow.png", "/upload/map.png"]
    if with_trace:
        assert saved["steps"] == initial["steps"]
    else:
        assert "steps" not in saved and "status" not in saved
    assert mysql.committed


def test_citation_projection_refuses_another_thread_or_user_message(monkeypatch):
    row = SimpleNamespace(id=42, thread_id="thread-other", role="assistant", execution_trace_json=None)
    mysql = _MysqlSession(row)
    monkeypatch.setattr(projection, "async_session", lambda: _Context(mysql))
    sources = [{"type": "image", "url": "/upload/map.png"}]
    assert not asyncio.run(projection.persist_message_citation_projection("thread-1", 42, sources))
    row.thread_id, row.role = "thread-1", "user"
    assert not asyncio.run(projection.persist_message_citation_projection("thread-1", 42, sources))
    assert not mysql.committed and row.execution_trace_json is None


def test_terminal_projection_does_not_erase_image_references(monkeypatch):
    row = SimpleNamespace(id=42, role="assistant", run_id="run-1", execution_trace_json=json.dumps({
        "citations": [{"type": "image", "url": "/upload/map.png", "title": "校区地图"}],
    }))
    mysql = _MysqlSession(row)
    monkeypatch.setattr(projection, "runtime_session", lambda: lambda: _Context(_RuntimeSession()))
    monkeypatch.setattr(projection, "async_session", lambda: _Context(mysql))
    monkeypatch.setattr(task_run_service, "get_execution_traces_by_thread", AsyncMock(return_value={
        42: {"run_id": "run-1", "status": "completed", "steps": []},
    }))
    assert asyncio.run(projection.persist_terminal_execution_trace_projection("run-1", 42))
    saved = projection.decode_execution_trace_projection(row.execution_trace_json)
    assert saved["status"] == "completed"
    assert projection.citations_from_projection(saved)[0]["url"] == "/upload/map.png"


def test_citation_save_keeps_shared_snapshot_when_runtime_is_not_configured(monkeypatch):
    from app.services.knowledge import citation_service

    persist = AsyncMock(return_value=True)
    monkeypatch.setattr(projection, "persist_message_citation_projection", persist)
    monkeypatch.setattr(citation_service, "runtime_session", lambda: None)
    asyncio.run(citation_service.save("thread-1", 42, [{"type": "image", "url": "/upload/map.png"}]))
    persist.assert_awaited_once()
    assert persist.await_args.args[:2] == ("thread-1", 42)
    assert persist.await_args.args[2][0]["url"] == "/upload/map.png"


@pytest.mark.parametrize("live_sources", [[], [{"type": "image", "url": "/upload/live.png"}]])
def test_history_restores_images_without_runtime_and_prefers_live_references(monkeypatch, live_sources):
    from app.services.agent_harness import orchestrator
    from app.services.knowledge import citation_service

    row = SimpleNamespace(
        id=42, role="assistant", content="请看校区地图。\n\n[图1]", feedback=None,
        status="completed", run_id="run-1", created_at=None, attachments_json=None,
        execution_trace_json=json.dumps({"citations": [{"type": "image", "url": "/upload/saved.png"}]}),
    )

    class HistorySession:
        async def get(self, _model, _id):
            return SimpleNamespace(id="thread-1", user_id="user-1")

        async def execute(self, _query):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row]))

    monkeypatch.setattr(orchestrator, "async_session", lambda: _Context(HistorySession()))
    monkeypatch.setattr(citation_service, "get_for_thread", AsyncMock(return_value={42: live_sources}))
    monkeypatch.setattr(task_run_service, "get_subagent_steps_by_thread", AsyncMock(return_value={}))
    monkeypatch.setattr(task_run_service, "get_execution_traces_by_thread", AsyncMock(return_value={}))

    result = asyncio.run(orchestrator.HarnessOrchestrator().get_thread_messages("user-1", "thread-1"))
    assert len(result) == 1
    assert result[0]["citations"][0]["url"] == ("/upload/live.png" if live_sources else "/upload/saved.png")
    assert result[0]["execution_trace"] is None
    assert result[0]["content"] == row.content


def test_invalid_citation_projection_does_not_guess_image_sources():
    assert projection.citations_from_projection("invalid-json") == []
    assert projection.citations_from_projection({"citations": "not-a-list"}) == []
    assert projection.citations_from_projection({"steps": [], "citations": [None]}) == []


def test_run_acceptance_binds_initial_user_message_to_run():
    import inspect
    from app.services.agent_harness import orchestrator

    source = inspect.getsource(orchestrator.HarnessOrchestrator.accept_harness_run)
    assert "role=\"user\"" in source
    assert "run_id=run_id" in source


def test_unbound_active_trace_gets_one_complete_assistant_projection():
    from app.services.agent_harness.orchestrator import (
        _append_unbound_active_run_trace_projections,
    )

    messages = [{
        "id": 8261,
        "role": "user",
        "content": "make a deck",
        "run_id": None,
        "execution_trace": None,
    }]
    trace = {
        "status": "running",
        "startedAt": 1788229776392,
        "event_cursor": 9874,
        "steps": [
            {"kind": "thinking", "text": "plan", "status": "completed"},
            {"kind": "tool", "name": "write_file", "status": "running"},
        ],
    }

    _append_unbound_active_run_trace_projections(messages, {"run-active": trace})

    assert len(messages) == 2
    projection = messages[-1]
    assert projection["role"] == "assistant"
    assert projection["run_id"] == "run-active"
    assert projection["execution_trace"] is trace
    assert projection["execution_trace"]["startedAt"] == 1788229776392
    assert len(projection["execution_trace"]["steps"]) == 2


def test_bound_run_trace_does_not_create_a_duplicate_projection():
    from app.services.agent_harness.orchestrator import (
        _append_unbound_active_run_trace_projections,
    )

    messages = [{
        "id": 8261,
        "role": "user",
        "content": "make a deck",
        "run_id": "run-active",
        "execution_trace": {"status": "running"},
    }]

    _append_unbound_active_run_trace_projections(
        messages,
        {"run-active": {"status": "running", "steps": []}},
    )

    assert len(messages) == 1


def test_backfill_can_derive_remote_runtime_without_printing_credentials(tmp_path, monkeypatch):
    from scripts import backfill_execution_trace_projections as backfill

    monkeypatch.delenv("SOURCE_RUNTIME_DATABASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CHECKPOINT_DATABASE_URL=postgresql://user:secret@runtime.example:5432/checkpoints\n",
        encoding="utf-8",
    )
    resolved = backfill._source_url(SimpleNamespace(source_checkpoint_env_file=str(env_file)))
    assert resolved == (
        "postgresql+psycopg://user:secret@runtime.example:5432/agent_runtime"
    )
