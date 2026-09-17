"""Focused contracts for the Agent Harness control plane.

These tests deliberately avoid external providers: routing and capability visibility must remain
deterministic even while a model/API is unavailable.
"""
import asyncio
import inspect
import json
import uuid
from contextlib import suppress

import pytest
from sqlalchemy import delete

from app.core.runtime_db import runtime_session
from app.runtime_models import AgentRun, AgentRunEvent
from app.services.agent_harness import run_store
from app.services.agent_harness.public_errors import public_terminal_reason
from app.services import sse_protocol
from app.services.chat.capability_broker import CapabilityBroker, MAX_DISCOVERED, build_capability_search_tool
from app.services.chat.run_hub import _user_facing_run_error
from app.services.chat.tools.base import MainTool
from app.services.chat.turn_decision import decide_turn


def _tool(name: str) -> MainTool:
    async def execute(_args):
        return "ok"
    return MainTool(name=name, description=f"{name} description", parameters={}, execute=execute)


def test_plain_question_defaults_to_agent_tools_available():
    """常识问句默认 agent：工具常驻，模型自决；不再零工具物理短路。"""
    decision = decide_turn("为什么天空是蓝色？")
    assert decision.route(has_explicit_resources=False, message="为什么天空是蓝色？") == "agent"


def test_explicit_resource_or_execution_enters_agent_route():
    assert decide_turn("帮我生成一个说明文档").route() == "agent"
    assert decide_turn("解释一下这个文件").route(has_explicit_resources=True) == "agent"


def test_broker_starts_small_and_loads_at_most_five_capabilities():
    tools = [_tool("ask_user_choice"), _tool("search_web"), _tool("browser_fetch"), _tool("browser_open")]
    tools += [_tool(f"github_read_{i}") for i in range(8)]
    broker = CapabilityBroker(tools, pinned=["ask_user_choice"])
    search = build_capability_search_tool(broker)
    broker.register(search, active=True)
    assert [item.name for item in broker.initial_tools()] == ["search_capabilities", "ask_user_choice"]
    loaded = broker.activate("github repository")
    assert 1 <= len(loaded) <= MAX_DISCOVERED
    assert len(broker.active_names) <= 2 + MAX_DISCOVERED


def test_observation_keeps_model_and_ui_channels_separate():
    from app.services.chat.tools.base import ToolValue

    async def execute(_args):
        return ToolValue(model_content="read 3 records", ui={"summary": "search_web"})

    obs = asyncio.run(MainTool(
        "search_web", "search", {}, execute, output_model=ToolValue,
    ).observe({}))
    assert obs.model_content == "read 3 records"
    assert obs.ui["summary"] == "search_web"
    assert obs.error is None
    assert asyncio.run(_tool("read_file").observe({})).status == "succeeded"


def test_model_busy_error_is_actionable_without_provider_details():
    error = type("BusyError", (Exception,), {"status_code": 503})("service_unavailable_error")
    message = _user_facing_run_error(error)
    assert "模型服务当前繁忙" in message
    assert "重试" in message
    assert "service_unavailable" not in message


def test_payment_required_and_insufficient_balance_are_public_quota_errors():
    error = type("PaymentError", (Exception,), {"status_code": 402})(
        'Error code: 402 - {"message":"Insufficient Balance","type":"new_api_error"}'
    )
    message = _user_facing_run_error(error)
    assert "模型额度不足" in message
    assert "402" not in message
    assert "Insufficient Balance" not in message
    assert "模型额度不足" in _user_facing_run_error(Exception("Insufficient Balance"))


def test_public_snapshot_sanitizes_historical_raw_terminal_reasons():
    raw = 'Error code: 402 - {"message":"Insufficient Balance","type":"new_api_error"}'
    message = public_terminal_reason(raw, phase="failed")
    assert message and "模型额度不足" in message
    assert "402" not in message and "new_api_error" not in message
    assert public_terminal_reason("user_cancelled", phase="cancelled") == "已停止生成"


def test_terminal_paths_use_canonical_public_events_and_safe_runstate():
    from app import worker
    from app.services.agent_harness.orchestrator import HarnessOrchestrator
    from app.services.chat.run_hub import RunHub

    pump_source = inspect.getsource(RunHub._pump_background_run)
    accept_source = inspect.getsource(HarnessOrchestrator.accept_harness_run)
    worker_source = inspect.getsource(worker._execute_job)
    assert 'channel.run_cancelled("user_cancelled")' in pump_source
    assert '"terminal_reason": str(e)' not in pump_source
    assert '"terminal_reason": str(exc)' not in accept_source
    assert '"terminal_reason": str(exc)' not in worker_source


@pytest.mark.asyncio
async def test_persisted_events_advance_runstate_and_snapshot_cursor():
    from app.services.tasks import task_run_service

    factory = runtime_session()
    if factory is None:
        pytest.skip("runtime database is unavailable")
    run_id = f"harness-cursor-{uuid.uuid4().hex}"
    async with factory() as session:
        session.add(AgentRun(
            id=run_id,
            thread_id=f"thread-{uuid.uuid4().hex}",
            user_id="harness-cursor-user",
            status="running",
            state=run_store.new_run_state(),
            state_version=1,
        ))
        await session.commit()
    try:
        await task_run_service.record_event(
            run_id, uuid.uuid4().hex, 7, "run.started", {},
        )
        state = await run_store.get_run_state(run_id)
        snapshot = await run_store.get_run_snapshot(run_id)
        replay = await task_run_service.list_event_payloads(run_id, "harness-cursor-user")
        assert state and state["state"]["event_cursor"] == 7
        assert snapshot and snapshot.event_cursor == 7
        envelope = json.loads(replay[0].removeprefix("data: ").strip())
        assert envelope["version"] == "harness/1"
        assert envelope["schema_version"] == 1
    finally:
        async with factory() as session:
            await session.execute(delete(AgentRunEvent).where(AgentRunEvent.run_id == run_id))
            await session.execute(delete(AgentRun).where(AgentRun.id == run_id))
            await session.commit()


@pytest.mark.asyncio
async def test_cross_worker_ephemeral_frame_cannot_overtake_durable_completion(monkeypatch):
    """An ephemeral sequence must not advance the client past an earlier durable event."""
    from app.services.chat.run_hub import RunHub
    from app.services.tasks import runtime_event_bus, task_run_service

    run_id = f"ordered-events-{uuid.uuid4().hex}"
    thread_id = "thread-ordered-events"
    hub = RunHub()

    def frame(sequence: int, event_type: str, data: dict) -> str:
        channel = sse_protocol.SSEChannel(
            sse_protocol.HARNESS, thread_id, run_id, start_sequence=sequence - 1,
        )
        return channel._env(event_type, data)  # noqa: SLF001 - exact protocol fixture

    durable_1 = frame(1, "run.started", {"thread_id": thread_id, "agent_mode": "standard"})
    durable_2 = frame(2, "message.completed", {"text": "完整答案", "message_id": 1})
    ephemeral_3 = frame(3, "message.delta", {"text": "尾"})
    durable_4 = frame(4, "run.completed", {"message_id": 1})
    run_reads = [
        {"status": "routing", "thread_id": thread_id},
        {"status": "routing", "thread_id": thread_id},
        {"status": "completed", "thread_id": thread_id},
    ]

    async def fake_get_run(_run_id, _user_id):
        return run_reads.pop(0) if run_reads else {"status": "completed", "thread_id": thread_id}

    async def fake_list_events(_run_id, _user_id, after_sequence=0, **_kwargs):
        if after_sequence <= 0:
            return [durable_1]
        if after_sequence == 1:
            # The terminal event may already be committed too; only events before the live
            # sequence may be emitted during the ordering barrier.
            return [durable_2, durable_4]
        if after_sequence == 3:
            return [durable_4]
        return []

    monkeypatch.setattr(task_run_service, "get_run", fake_get_run)
    monkeypatch.setattr(task_run_service, "list_event_payloads", fake_list_events)

    subscription = hub.subscribe_run(
        user_id="ordered-user",
        run_id=run_id,
        protocol=sse_protocol.HARNESS,
    )
    try:
        assert hub._payload_sequence(await anext(subscription)) == 1
        runtime_event_bus.notify_ephemeral(run_id, ephemeral_3)
        assert hub._payload_sequence(await anext(subscription)) == 2
        assert hub._payload_sequence(await anext(subscription)) == 3
        assert hub._payload_sequence(await anext(subscription)) == 4
        assert (await anext(subscription)).strip() == "data: [DONE]"
    finally:
        await subscription.aclose()


def test_runtime_readiness_requires_exact_schema_head(monkeypatch):
    from app.core import runtime_db

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar(self):
            return self.value

    class Connection:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, _statement):
            return Result(self.value)

    class Engine:
        def __init__(self, value):
            self.value = value

        def connect(self):
            return Connection(self.value)

    monkeypatch.setattr(runtime_db, "get_engine", lambda: Engine("obsolete_runtime_head"))
    assert asyncio.run(runtime_db.readiness_probe()) is False
    monkeypatch.setattr(runtime_db, "get_engine", lambda: Engine(runtime_db.RUNTIME_SCHEMA_HEAD))
    assert asyncio.run(runtime_db.readiness_probe()) is True


def test_save_run_state_locks_row_and_advances_cas_version(monkeypatch):
    """兼容写入不得绕过 Harness 版本号，否则可覆盖并发 checkpoint。"""
    from app.services.tasks import task_run_service

    class Row:
        state = {
            "schema_version": run_store.HARNESS_STATE_SCHEMA_VERSION,
            "phase": "executing",
            "plan_id": "p1",
        }
        state_version = 7

    row = Row()
    seen = {"for_update": False, "committed": False}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _model, _run_id, *, with_for_update=False):
            seen["for_update"] = with_for_update
            return row

        async def commit(self):
            seen["committed"] = True

    monkeypatch.setattr(task_run_service, "runtime_session", lambda: lambda: Session())
    assert asyncio.run(task_run_service.save_run_state("r1", {"skill_ids": ["s1"]})) is True
    assert seen == {"for_update": True, "committed": True}
    assert row.state == {
        "schema_version": run_store.HARNESS_STATE_SCHEMA_VERSION,
        "phase": "executing", "plan_id": "p1", "skill_ids": ["s1"],
    }
    assert row.state_version == 8


def test_worker_watchdog_honors_persisted_cancel_request(monkeypatch):
    from app import worker
    from app.services.tasks import task_run_service

    async def _status(_run_id):
        return "running"

    async def _cancel_requested(_run_id):
        return True

    monkeypatch.setattr(task_run_service, "get_run_status", _status)
    monkeypatch.setattr(task_run_service, "is_run_cancel_requested", _cancel_requested)

    async def _run():
        execution = asyncio.create_task(asyncio.sleep(30))
        await worker._lease_watchdog("j1", "r1", execution)
        with suppress(asyncio.CancelledError):
            await execution
        return execution.cancelled()

    assert asyncio.run(_run()) is True


def test_worker_watchdog_does_not_cancel_its_own_terminal_publish(monkeypatch):
    from app import worker
    from app.services.tasks import task_run_service

    async def _status(_run_id):
        return "completed"

    async def _cancel_requested(_run_id):
        return False

    monkeypatch.setattr(task_run_service, "get_run_status", _status)
    monkeypatch.setattr(task_run_service, "is_run_cancel_requested", _cancel_requested)

    async def _run():
        execution = asyncio.create_task(asyncio.sleep(0.7))
        await worker._lease_watchdog("j1", "r1", execution)
        await execution
        return execution.cancelled()

    assert asyncio.run(_run()) is False


def test_resume_path_reuses_broker_and_tool_contract_gate():
    """HITL 续接不得绕过首轮工具 schema 边界。"""
    from app.services.agent_harness.orchestrator import HarnessOrchestrator

    source = inspect.getsource(HarnessOrchestrator._resume_orchestration)
    assert 'state.get("active_capabilities")' in source
    assert "model_driver.assert_tool_contracts(tools)" in source
    assert "CapabilityBroker(tools, pinned=resume_pins)" in source
    assert '"capability_broker": broker' in source
