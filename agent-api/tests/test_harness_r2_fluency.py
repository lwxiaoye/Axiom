"""R2 fluency: job wakeup, coalesced delta persist, cursor cache."""
import asyncio
import json
import time

import pytest

from app.services.agent_harness.event_catalog import EVENT_CATALOG
from app.services.chat.run_hub import coalesce_delta_texts
from app.services.tasks import runtime_event_bus, task_run_service


def _delta_payload(text: str, seq: int = 3, run_id: str = "r1") -> str:
    body = {
        "version": "harness/1",
        "schema_version": 1,
        "run_id": run_id,
        "type": "message.delta",
        "sequence": seq,
        "event_id": f"e{seq}",
        "timestamp": 1,
        "data": {"text": text},
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


def _reasoning_payload(text: str, seq: int = 2, run_id: str = "r1") -> str:
    body = {
        "version": "harness/1",
        "schema_version": 1,
        "run_id": run_id,
        "type": "message.reasoning.delta",
        "sequence": seq,
        "event_id": f"e{seq}",
        "timestamp": 1,
        "data": {"text": text},
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


def test_message_delta_catalog_stays_ephemeral_for_live_protocol():
    assert EVENT_CATALOG["message.delta"].persisted is False
    assert "message.delta" not in task_run_service._TRACE_EVENT_TYPES


def test_coalesced_persist_concat_matches_token_stream():
    tokens = ["A", "B", "C" * 80, "D"]
    assert "".join(coalesce_delta_texts(tokens)) == "".join(tokens)


@pytest.mark.asyncio
async def test_job_notify_wakes_idle_waiter():
    waiter = asyncio.create_task(runtime_event_bus.wait_for_job(timeout=1.0))
    await asyncio.sleep(0.02)
    started = time.perf_counter()
    runtime_event_bus.notify_job_local()
    woke = await waiter
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert woke is True
    assert elapsed_ms < 10


@pytest.mark.asyncio
async def test_list_event_payloads_cursor_cache_and_invalidate():
    task_run_service._payload_cache.clear()
    task_run_service._payload_cache[("run-a", "user-a", 4)] = ["frame-x"]
    out = await task_run_service.list_event_payloads("run-a", "user-a", 4)
    assert out == ["frame-x"]
    out[0] = "mutated"
    assert task_run_service._payload_cache[("run-a", "user-a", 4)] == ["frame-x"]
    task_run_service.invalidate_event_payload_cache("run-a")
    assert ("run-a", "user-a", 4) not in task_run_service._payload_cache


def test_payload_cache_never_stores_empty_live_gap():
    task_run_service._payload_cache.clear()
    task_run_service._store_payload_cache(("run-a", "user-a", 5), [])
    assert ("run-a", "user-a", 5) not in task_run_service._payload_cache


@pytest.mark.asyncio
async def test_list_event_payloads_ignores_poisoned_empty_cache(monkeypatch):
    """API 进程缓存 after=N 的 [] 时，必须回源，不能让 worker 后写入的帧永远进不了轮询。"""
    from types import SimpleNamespace

    task_run_service._payload_cache.clear()
    task_run_service._payload_cache[("run-live", "user-a", 5)] = []

    class _Run:
        user_id = "user-a"
        status = "running"
        thread_id = "th-1"

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return [SimpleNamespace(
                data={"_event_timestamp": 1, "name": "search_web"},
                created_at=None,
                event_id="e6",
                sequence=6,
                type="tool.started",
            )]

    class _Session:
        async def get(self, *_a, **_k):
            return _Run()

        async def execute(self, *_a, **_k):
            return _Result()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(task_run_service, "runtime_session", lambda: (lambda: _Session()))
    out = await task_run_service.list_event_payloads("run-live", "user-a", 5)
    assert len(out) == 1
    assert "tool.started" in out[0]
    assert ("run-live", "user-a", 5) not in task_run_service._payload_cache


@pytest.mark.asyncio
async def test_message_delta_dual_write_persist_then_live(monkeypatch):
    seen = {"ephemeral": [], "persist": [], "order": []}

    async def _pub(run_id, payload):
        seen["order"].append("publish")
        seen["ephemeral"].append((run_id, payload))

    async def _rec(**kwargs):
        seen["order"].append("persist")
        seen["persist"].append(kwargs)

    monkeypatch.setattr(runtime_event_bus, "publish_ephemeral_payload", _pub)
    monkeypatch.setattr(task_run_service, "record_event", _rec)

    await task_run_service.record_sse_payload("r1", _delta_payload("你好", 3))
    assert len(seen["ephemeral"]) == 1
    assert seen["ephemeral"][0][0] == "r1"
    assert seen["persist"][0]["etype"] == "message.delta"
    assert seen["persist"][0]["sequence"] == 3
    assert seen["persist"][0]["data"]["text"] == "你好"
    assert seen["order"] == ["persist", "publish"]

    seen["ephemeral"].clear()
    seen["persist"].clear()
    await task_run_service.record_sse_payload("r1", _reasoning_payload("想", 2))
    assert len(seen["ephemeral"]) == 1
    assert seen["persist"] == []


@pytest.mark.asyncio
async def test_message_delta_persistence_failure_prevents_live_publish(monkeypatch):
    published = []

    async def _pub(run_id, payload):
        published.append((run_id, payload))

    async def _fail(**_kwargs):
        raise task_run_service.EventPersistenceError("db unavailable")

    monkeypatch.setattr(runtime_event_bus, "publish_ephemeral_payload", _pub)
    monkeypatch.setattr(task_run_service, "record_event", _fail)

    with pytest.raises(task_run_service.EventPersistenceError):
        await task_run_service.record_sse_payload("r1", _delta_payload("你好", 3))
    assert published == []
