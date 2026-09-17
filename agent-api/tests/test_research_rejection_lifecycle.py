"""Exercise the real Run event pump with isolated persistence and model failures."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import sse_protocol
from app.services.agent_harness import artifact_checkpoint, run_store
from app.services.agent_harness.model_stream import ModelProviderPolicyRejected
from app.services.agent_harness.public_errors import ResearchReportRejected, public_terminal_reason
from app.services.chat import run_hub
from app.services.tasks import run_reconcile_service, task_run_service


@pytest.fixture
def pump(monkeypatch):
    state = {"status": "running"}
    hub = run_hub.RunHub()
    recorded = []
    published = []

    async def status(_):
        return state["status"]

    async def finalize(_, terminal, **kwargs):
        if state["status"] in task_run_service.TERMINAL_RUN_STATUSES:
            return False
        state.update(status=terminal, error=kwargs.get("error"))
        return True

    async def record(_, payload):
        recorded.append(task_run_service.parse_harness_sse_payload(payload))

    async def publish(_, payload):
        published.append(payload)

    async def patch(_, values, **kwargs):
        state.update(values, **kwargs)
        return {"state": state}

    async def recover(*args, **kwargs):
        state["status"] = "waiting_system"
        return {"marked": True}

    recovery = AsyncMock(side_effect=recover)
    monkeypatch.setattr(task_run_service, "get_run_status", status)
    monkeypatch.setattr(task_run_service, "finalize_run", finalize)
    monkeypatch.setattr(task_run_service, "record_sse_payload", record)
    monkeypatch.setattr(run_store, "patch_run_state", patch)
    monkeypatch.setattr(run_hub, "recover_run_after_error", recovery)
    monkeypatch.setattr(hub, "_publish_run_payload", publish)
    monkeypatch.setattr(hub, "_ensure_terminal_anchor", AsyncMock())
    context_quarantine = AsyncMock(return_value=1)
    monkeypatch.setattr(
        run_reconcile_service,
        "quarantine_policy_rejected_run",
        context_quarantine,
    )
    monkeypatch.setattr(artifact_checkpoint, "run_owner", AsyncMock(return_value=("", "")))
    monkeypatch.setattr(run_hub.sandbox_session_pool, "close_scope", AsyncMock(return_value=0))
    monkeypatch.setattr(run_hub.sandbox_session_pool, "peek", lambda _: None)
    return SimpleNamespace(
        hub=hub,
        state=state,
        recorded=recorded,
        published=published,
        recovery=recovery,
        context_quarantine=context_quarantine,
    )


async def run_failure(pump, error):
    channel = sse_protocol.SSEChannel(sse_protocol.HARNESS, "t", "r", start_sequence=100)

    async def source():
        yield channel.run_phase_changed("synthesizing")
        raise error

    await pump.hub._pump_background_run(
        run_id="r", thread_id="t", protocol=sse_protocol.HARNESS, source=source(),
        job={"id": "j", "attempt_count": 3},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "waiting_system"])
async def test_rejected_report_ends_run_once_without_recovery(pump, status):
    pump.state["status"] = status
    await run_failure(pump, ResearchReportRejected("private draft details"))
    assert pump.state["status"] == pump.state["phase"] == "failed"
    assert pump.state["terminal_reason"] == ResearchReportRejected.public_message
    assert public_terminal_reason(pump.state["terminal_reason"], phase="failed") == pump.state["terminal_reason"]
    assert [e["type"] for e in pump.recorded] == ["run.phase.changed", "run.failed"]
    assert [e["sequence"] for e in pump.recorded] == [101, 102]
    assert pump.state["last_checkpoint_sequence"] == 102
    assert "private draft" not in str(pump.recorded)
    assert pump.published.count("data: [DONE]\n\n") == 1
    pump.recovery.assert_not_awaited()
    pump.context_quarantine.assert_not_awaited()
    pump.hub._ensure_terminal_anchor.assert_awaited_once_with("r", "t")


@pytest.mark.asyncio
async def test_provider_policy_rejection_ends_run_once_without_recovery(pump):
    error = ModelProviderPolicyRejected(
        500,
        "sensitive_words_detected",
        '{"error":{"code":"sensitive_words_detected","message":"private"}}',
    )
    await run_failure(pump, error)

    assert pump.state["status"] == pump.state["phase"] == "failed"
    assert pump.state["terminal_reason"] == error.public_message
    failed = [event for event in pump.recorded if event["type"] == "run.failed"]
    assert len(failed) == 1
    assert failed[0]["data"]["message"] == error.public_message
    assert "private" not in str(failed)
    assert public_terminal_reason(
        pump.state["terminal_reason"], phase="failed"
    ) == error.public_message
    assert pump.state["policy_rejection_context_quarantined"] is True
    pump.context_quarantine.assert_awaited_once_with("r", "t")
    pump.recovery.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "completed"])
async def test_rejection_cannot_overwrite_existing_terminal(pump, status):
    pump.state["status"] = status
    await run_failure(pump, ResearchReportRejected("rejected"))
    assert pump.state["status"] == status
    assert not any(e["type"] == "run.failed" for e in pump.recorded)
    pump.recovery.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_persistence_failure_does_not_publish_false_failure(pump, monkeypatch):
    monkeypatch.setattr(task_run_service, "finalize_run", AsyncMock(return_value=False))
    await run_failure(pump, ResearchReportRejected("rejected"))
    assert pump.state["status"] == "waiting_system"
    assert not any(e["type"] == "run.failed" for e in pump.recorded)
    pump.recovery.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError("model timeout"), httpx.ReadTimeout("model read timeout"), RuntimeError("temporary fault")])
async def test_transient_errors_still_recover_same_run(pump, error):
    await run_failure(pump, error)
    assert pump.state["status"] == "waiting_system"
    assert not any(e["type"] == "run.failed" for e in pump.recorded)
    pump.recovery.assert_awaited_once()


@pytest.mark.asyncio
async def test_closing_page_subscription_preserves_background_run_and_replay(pump, monkeypatch):
    ready, finish = asyncio.Event(), asyncio.Event()
    channel = sse_protocol.SSEChannel(sse_protocol.HARNESS, "t", "r")

    async def publish(run_id, payload):
        pump.published.append(payload)
        await run_hub.RunHub._publish_run_payload(pump.hub, run_id, payload)

    async def source():
        yield channel.run_phase_changed("synthesizing")
        ready.set()
        await finish.wait()
        await task_run_service.finalize_run("r", "completed")
        yield channel.run_completed()

    monkeypatch.setattr(pump.hub, "_publish_run_payload", publish)
    monkeypatch.setattr(task_run_service, "get_run", AsyncMock(return_value={"status": "running", "thread_id": "t"}))
    monkeypatch.setattr(task_run_service, "list_event_payloads", AsyncMock(return_value=[]))
    task = asyncio.create_task(pump.hub._pump_background_run(
        run_id="r", thread_id="t", protocol=sse_protocol.HARNESS, source=source()))
    try:
        await asyncio.wait_for(ready.wait(), timeout=1)
        subscription = pump.hub.subscribe_run(user_id="u", run_id="r", protocol=sse_protocol.HARNESS)
        assert "synthesizing" in await anext(subscription)
        await subscription.aclose()  # Navigation tears down this observer only.
        assert not task.done()
        assert not pump.hub._run_subscribers.get("r")
        assert pump.state["status"] == "running"
        finish.set()
        await asyncio.wait_for(task, timeout=1)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert pump.state["status"] == "completed"
    pump.recovery.assert_not_awaited()
    replay = [p async for p in pump.hub.subscribe_run(user_id="u", run_id="r", protocol=sse_protocol.HARNESS)]
    assert any("run.completed" in p for p in replay)
    assert not any("run.failed" in p or "run.cancelled" in p for p in replay)
