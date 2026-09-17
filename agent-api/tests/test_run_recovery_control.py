"""Pure recovery coordination tests; no database or Docker required."""

import asyncio
import json
from datetime import datetime, timedelta

from app.services.agent_harness import run_store
from app.services.chat import run_hub
from app.services.tasks import task_run_service


def test_recover_run_checkpoints_before_requeue(monkeypatch):
    calls = []

    async def fake_mark(run_id, **kwargs):
        calls.append(("mark", run_id, kwargs))
        return "resume-token"

    async def fake_checkpoint(run_id, **kwargs):
        calls.append(("checkpoint", run_id, kwargs))
        return {
            "state": {
                "execution_control": {
                    "segment_index": 1,
                    "checkpoint_sequence": 1,
                    "recovery_reason": kwargs["recovery_reason"],
                    "recovery_count": 1,
                },
            },
        }

    async def fake_enqueue(run_id, **kwargs):
        calls.append(("enqueue", run_id, kwargs))
        return True

    async def fake_append(run_id, event_type, data):
        calls.append(("event", run_id, event_type, data))

    monkeypatch.setattr(task_run_service, "mark_run_recoverable", fake_mark)
    monkeypatch.setattr(run_store, "record_recovery_checkpoint", fake_checkpoint)
    monkeypatch.setattr(run_store, "enqueue_job", fake_enqueue)
    monkeypatch.setattr(task_run_service, "append_run_event", fake_append)

    assert asyncio.run(task_run_service.recover_run_after_fault(
        "run-1", reason="worker_restart", backoff_seconds=0.5,
    )) is True
    assert [item[0] for item in calls] == ["checkpoint", "mark", "enqueue", "event"]
    assert calls[2][2]["wake_reason"] == "recovery"
    assert calls[3][2] == "run.recovery.scheduled"


def test_recover_run_does_not_enqueue_without_checkpoint(monkeypatch):
    calls = []

    async def fake_mark(_run_id, **_kwargs):
        return "resume-token"

    async def fake_checkpoint(_run_id, **_kwargs):
        return None

    async def fake_enqueue(*_args, **_kwargs):
        calls.append("enqueue")
        return True

    monkeypatch.setattr(task_run_service, "mark_run_recoverable", fake_mark)
    monkeypatch.setattr(run_store, "record_recovery_checkpoint", fake_checkpoint)
    monkeypatch.setattr(run_store, "enqueue_job", fake_enqueue)

    assert asyncio.run(task_run_service.recover_run_after_fault(
        "run-1", reason="sandbox_exit",
    )) is False
    assert calls == []


def test_worker_owned_recovery_waits_for_lease_finisher_before_requeue(monkeypatch):
    calls = []

    async def fake_checkpoint(_run_id, **_kwargs):
        calls.append("checkpoint")
        return {"state": {"execution_control": {"segment_index": 2}}}

    async def fake_status(_run_id):
        return "running"

    async def fake_mark(_run_id, **_kwargs):
        calls.append("mark")
        return "resume-token"

    async def fake_enqueue(*_args, **_kwargs):
        calls.append("enqueue")
        return True

    monkeypatch.setattr(run_hub.run_store, "record_recovery_checkpoint", fake_checkpoint)
    monkeypatch.setattr(run_hub.task_run_service, "get_run_status", fake_status)
    monkeypatch.setattr(run_hub.task_run_service, "mark_run_recoverable", fake_mark)
    monkeypatch.setattr(run_hub.run_store, "enqueue_job", fake_enqueue)

    result = asyncio.run(run_hub.recover_run_after_error(
        "run-1", reason="worker_restart", job={"id": "job-1", "attempt_count": 1},
    ))
    assert result["marked"] is True
    assert result["queued"] is False
    assert calls == ["checkpoint", "mark"]


def test_worker_recovery_handoff_keeps_backoff_in_lease_finisher(monkeypatch):
    from app import worker

    calls = []
    deadline = datetime.utcnow() + timedelta(seconds=30)

    async def fake_finish(job_id, worker_id, **kwargs):
        calls.append(("finish", job_id, worker_id, kwargs))
        return True

    async def fake_enqueue(*args, **kwargs):
        calls.append(("enqueue", args, kwargs))
        return True

    monkeypatch.setattr(worker.run_store, "finish_job", fake_finish)
    monkeypatch.setattr(worker.run_store, "enqueue_job", fake_enqueue)

    asyncio.run(worker._finish_recovery_job(
        {"id": "job-1", "run_id": "run-1", "attempt_count": 1, "max_attempts": 3},
        {"checkpoint": True, "retry_allowed": True, "available_at": deadline},
    ))

    assert calls == [(
        "finish",
        "job-1",
        worker.WORKER_ID,
        {"status": "waiting", "wake_reason": "recovery", "available_at": deadline},
    )]


def test_worker_recovery_exhausted_attempts_yield_the_worker(monkeypatch):
    from app import worker

    calls = []

    async def fake_finish(job_id, worker_id, **kwargs):
        calls.append((job_id, worker_id, kwargs))
        return True

    monkeypatch.setattr(worker.run_store, "finish_job", fake_finish)

    started_at = datetime.utcnow()
    asyncio.run(worker._finish_recovery_job(
        {"id": "job-1", "run_id": "run-1", "attempt_count": 3, "max_attempts": 3},
        {"checkpoint": True, "retry_allowed": False},
    ))

    deadline = calls[0][2]["available_at"]
    assert calls[0][2]["wake_reason"] == "recovery"
    assert deadline >= started_at + timedelta(seconds=worker.RECOVERY_EXHAUSTED_BACKOFF_SECONDS - 1)


def test_loop_checkpoint_preserves_old_history_and_strips_only_inline_image_bytes():
    huge = [{"role": "user", "content": "x" * 50_000} for _ in range(8)]
    huge[0] = {"role": "system", "content": "sys"}
    huge.insert(2, {
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,aaa"}},
            {"type": "image_url", "image_url": {"url": "https://example.test/image.png"}},
            {"type": "input_image", "image_url": "data:image/png;base64,bbb"},
            {"type": "image", "source": {"type": "base64", "data": "ccc"}},
            {"type": "image", "source": {"type": "url", "url": "https://example.test/b.png"}},
            "保留字符串文本",
            {"type": "text", "text": "看图"},
        ],
    })
    trimmed = run_store.trim_loop_checkpoint_messages(huge, max_chars=20_000)
    assert trimmed[0]["role"] == "system"
    assert all("data:image" not in str(item.get("content")) for item in trimmed)
    assert len(trimmed) == len(huge)
    assert len(json.dumps(trimmed, ensure_ascii=False)) > 20_000
    assert "内嵌图片" in str(trimmed[2]["content"])
    assert "https://example.test/image.png" in str(trimmed[2]["content"])
    assert "https://example.test/b.png" in str(trimmed[2]["content"])
    assert "保留字符串文本" in str(trimmed[2]["content"])


def test_loop_checkpoint_persists_typed_world_state_and_soft_limit_metadata(monkeypatch):
    captured = {}

    async def patch(_run_id, value):
        captured.update(value["loop_checkpoint"])
        return {"state": value}

    monkeypatch.setattr(run_store, "patch_run_state", patch)
    result = asyncio.run(run_store.persist_loop_checkpoint(
        "run-1",
        messages=[{"role": "user", "content": "x" * 181_000}],
        world_state={"conversation_summary": "summary", "turn_guard": "guard"},
        goal_revision=2,
        plan_version=3,
        step=4,
    ))

    assert result is not None
    assert captured["world_state"] == {
        "conversation_summary": "summary",
        "turn_guard": "guard",
    }
    assert captured["history_kind"] == "full"
    assert captured["soft_limit_exceeded"] is True
    assert captured["goal_revision"] == 2
    assert captured["plan_version"] == 3
    assert captured["step"] == 4


def test_recovery_observation_is_not_stacked():
    rows = run_store.with_recovery_observation([{"role": "user", "content": "原任务"}])
    assert "【恢复观察】" in rows[-1]["content"]
    again = run_store.with_recovery_observation(rows)
    assert sum(1 for item in again if "【恢复观察】" in str(item.get("content") or "")) == 1


def test_loop_resume_requires_checkpoint_messages():
    empty = {"interactive_type": "task_recovery", "loop_checkpoint": {"messages": []}}
    assert run_store.loop_checkpoint_messages(empty) == []
    present = {
        "interactive_type": "task_recovery",
        "loop_checkpoint": {"messages": [{"role": "user", "content": "已做到一半"}]},
    }
    assert run_store.loop_checkpoint_messages(present)[0]["content"] == "已做到一半"
