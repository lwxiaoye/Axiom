"""Regression for long evidence -> leaked plan -> invalid Responses -> recovery loop."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.agent_harness import model_driver
from app.services.agent_harness.context import ContextCompiler
from app.services.agent_harness.public_errors import ModelRequestRejected, ModelResponseContractError
from app.services.agent_harness.research.contracts import ResearchLedger, SourceRecord
from app.services.agent_harness.research.kernel import _inject_evidence, _TEAM_SYNTHESIS_GUARD
from app.services.agent_harness.responses_protocol import messages_to_responses_input, pair_responses_function_call_outputs
from app.services.agents.agent_service import agent_service
from app.services.chat.main_tool_turn import recovery_projection_world_state
from tests.test_research_rejection_lifecycle import pump, run_failure  # noqa: F401


PLAN_TEXT = '<tool_call><update_plan>{"steps":[{"title":"非法重排计划","status":"running"}]}</update_plan></tool_call>'
ANSWER = "这是依据已读取材料核验的研究结论[1]。"


def native_answer(text=ANSWER):
    return {"id": "msg", "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": text}]}


def native_plan():
    return {"type": "function_call", "call_id": "plan-1", "name": "update_plan",
            "arguments": '{"steps":[{"title":"非法重排计划","status":"running"}]}'}


SYNTHESIS_TOOL_OUTPUTS = [
    [native_answer(PLAN_TEXT)], [native_plan()],
    *[[native_answer(prefix + json.dumps({"name": name, "arguments": {}}))]
      for name in ("browser_open", "edit_file", "ask_user_choice")
      for prefix in ("", "准备执行下一步\n")],
]


@pytest.fixture
def provider(monkeypatch):
    queue, payloads, urls = [], [], []

    class Response:
        def __init__(self, row):
            self.status_code = row.get("status", 200)
            self.body = row.get("body", {})
            self.items = row.get("items", [])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def aread(self):
            return json.dumps(self.body).encode()

        async def aiter_lines(self):
            for i, item in enumerate(self.items):
                yield "data: " + json.dumps({"type": "response.output_item.done", "output_index": i, "item": item})
            yield "data: " + json.dumps({"type": "response.completed", "response": {
                "id": "response", "status": "completed", "output": self.items,
                "usage": {"input_tokens": 10, "output_tokens": 5}}})
            yield "data: [DONE]"

    class Client:
        def __init__(self, *_, **__):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def stream(self, _, url, **kwargs):
            assert url.endswith(("/responses", "/chat/completions"))
            urls.append(url)
            payloads.append(copy.deepcopy(kwargs["json"]))
            assert queue, "unexpected extra model request"
            return Response(queue.pop(0))

        async def post(self, *_, **__):
            raise AssertionError("must not retry the same invalid request as non-streaming")

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    monkeypatch.setattr(agent_service, "model_responses_capability", lambda *_, **__: True)
    return SimpleNamespace(queue=queue, payloads=payloads, urls=urls)


def long_context():
    ledger = ResearchLedger(query="研究问题", sources=[
        SourceRecord(url=f"https://source-{i}.example/doc", title=f"来源{i}",
                     snippet=str(i) * 8000, scraped=True) for i in range(8)
    ])
    env = SimpleNamespace(research_team_synthesis_only=True, turn_guard_prompt="旧材料" * 30_000)
    _inject_evidence(env, ledger)
    return env


def test_long_evidence_and_pre_fix_recovery_cannot_truncate_stage_constraints():
    env = long_context()
    previous = {"turn_guard": "旧计划与截断证据" * 5000, "conversation_summary": "概要" * 20_000}
    restored = recovery_projection_world_state(
        {"turn_guard": env.turn_guard_prompt}, initial_messages=[{"role": "user", "content": "继续"}],
        checkpoint_world_state=previous, turn_constraints=env.research_synthesis_constraints,
    )
    compiled = ContextCompiler.bounded_world_state(restored)
    assert compiled["turn_constraints"] == _TEAM_SYNTHESIS_GUARD
    assert restored["turn_guard"] == previous["turn_guard"]
    assert "turn_constraints" not in previous
    assert len(json.dumps(compiled, ensure_ascii=False, separators=(",", ":"))) <= 64_000
    # Source numbering remains stable even when body text needs a smaller budget.
    assert "[8] 来源7" in env.turn_guard_prompt[:40_000]


def test_recovered_call_keeps_native_reasoning_and_gets_its_real_receipt():
    native = [{"type": "reasoning", "encrypted_content": "opaque"}, native_answer(PLAN_TEXT)]
    messages = [
        {"role": "assistant", "content": PLAN_TEXT, "_responses_output_items": native,
         "tool_calls": [{"id": "recovered-1", "function": {"name": "update_plan", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "recovered-1", "content": "计划已更新。"},
    ]
    before = copy.deepcopy(messages)
    rows = messages_to_responses_input(messages)
    assert rows[0] == native[0]
    assert [r["call_id"] for r in rows if r.get("type") == "function_call"] == ["recovered-1"]
    assert rows[-1] == {"type": "function_call_output", "call_id": "recovered-1", "output": "计划已更新。"}
    assert messages == before


def test_orphan_and_duplicate_outputs_never_reach_provider():
    call = {"type": "function_call", "call_id": "ok", "name": "lookup", "arguments": "{}"}
    receipt = {"type": "function_call_output", "call_id": "ok", "output": "real"}
    rows = pair_responses_function_call_outputs([
        {"type": "function_call_output", "call_id": "orphan", "output": "unattributed"},
        call, call, receipt, receipt,
    ])
    assert rows == [call, receipt]
    assert pair_responses_function_call_outputs(rows) == rows


@pytest.mark.asyncio
async def test_long_context_reaches_real_driver_and_produces_one_report(provider, monkeypatch):
    from app.services.agent_harness import run_store, plan_store
    from tests.test_agent_harness_context import _run

    monkeypatch.setattr(run_store, "get_run_state", AsyncMock(return_value={"state": {}}))
    monkeypatch.setattr(run_store, "get_run_snapshot", AsyncMock(return_value=_run()))
    monkeypatch.setattr(run_store, "patch_run_state", AsyncMock(return_value={"state": {}}))
    monkeypatch.setattr(run_store, "persist_loop_checkpoint", AsyncMock(return_value={"state": {}}))
    monkeypatch.setattr(plan_store, "get_plan_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(model_driver.model_usage_audit, "begin_logical_call", AsyncMock(return_value=None))
    env = long_context()
    provider.queue.append({"items": [native_answer()]})
    events = [e async for e in model_driver.drive_model(
        model="deepseek-synthesis-test", api_key="test", user_input="研究问题", tools=[],
        world_state={"turn_guard": env.turn_guard_prompt, "turn_constraints": env.research_synthesis_constraints},
        gateway={"run_id": "run-1", "research_synthesis_only": True}, research_profile=True,
    )]
    assert len(provider.payloads) == 1
    assert not provider.payloads[0].get("tools")
    assert "不要重新制定研究计划" in json.dumps(provider.payloads[0], ensure_ascii=False)
    assert [e["answer"] for e in events if e["type"] == "final"] == [ANSWER]


@pytest.mark.asyncio
@pytest.mark.parametrize("items", SYNTHESIS_TOOL_OUTPUTS)
async def test_tool_free_synthesis_rejects_text_and_native_plan_without_side_effects(provider, monkeypatch, items):
    persist = AsyncMock()
    monkeypatch.setattr("app.services.tasks.plan_service.upsert_plan", persist)
    provider.queue.extend([{"items": items}, {"items": items}])
    events = []
    with pytest.raises(ModelResponseContractError):
        async for event in model_driver.drive_model(
            model="deepseek-synthesis-test", api_key="test", user_input="写报告", tools=[],
            gateway={"research_synthesis_only": True}, research_profile=True,
        ):
            events.append(event)
    assert len(provider.payloads) == 2
    assert not any(e["type"] in {"task_plan", "tool_started", "final"} for e in events)
    persist.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("items", SYNTHESIS_TOOL_OUTPUTS)
async def test_one_synthesis_correction_can_deliver_without_reopening_tools(provider, monkeypatch, items):
    persist = AsyncMock()
    monkeypatch.setattr("app.services.tasks.plan_service.upsert_plan", persist)
    provider.queue.extend([{"items": items}, {"items": [native_answer()]}])
    events = [event async for event in model_driver.drive_model(
        model="deepseek-synthesis-test", api_key="test", user_input="写报告", tools=[],
        gateway={"research_synthesis_only": True}, research_profile=True,
    )]
    assert len(provider.payloads) == 2
    assert all(not payload.get("tools") for payload in provider.payloads)
    assert not any(event["type"] in {"task_plan", "tool_started"} for event in events)
    assert [event["answer"] for event in events if event["type"] == "final"] == [ANSWER]
    persist.assert_not_awaited()
    rows = provider.payloads[1]["input"]
    outputs = [row for row in rows if row.get("type") == "function_call_output"]
    if items[0]["type"] == "function_call":
        assert len(outputs) == 1 and "未执行" in outputs[0]["output"]
        assert any(row.get("type") == "function_call" and row.get("call_id") == outputs[0]["call_id"] for row in rows)
    else:
        assert not outputs


@pytest.mark.asyncio
async def test_restored_synthesis_correction_does_not_reset_its_allowance(provider):
    from app.services.agent_harness.model_stream import ModelProviderHTTPError

    checkpoints = []
    async def save(messages, **kwargs):
        checkpoints.append(copy.deepcopy(messages))
    provider.queue.extend([{"items": [native_plan()]}, {"status": 429, "body": {"error": "rate limit"}}])
    gateway = {"research_synthesis_only": True, "checkpoint_sink": save}
    with pytest.raises(ModelProviderHTTPError):
        _ = [event async for event in model_driver.drive_model(
            model="deepseek-synthesis-test", api_key="test", user_input="写报告", tools=[],
            gateway=gateway, research_profile=True,
        )]
    assert len(checkpoints) == 1
    assert checkpoints[0][-1]["name"] == model_driver._research_synthesis_correction_name("")
    provider.queue.append({"items": [native_plan()]})
    with pytest.raises(ModelResponseContractError):
        _ = [event async for event in model_driver.drive_model(
            model="deepseek-synthesis-test", api_key="test", user_input="写报告", tools=[],
            initial_messages=checkpoints[0], gateway=gateway, research_profile=True,
        )]
    assert len(provider.payloads) == 3
    assert len(checkpoints) == 1


@pytest.mark.asyncio
async def test_correction_checkpoint_failure_does_not_issue_another_model_request(provider):
    async def cannot_save(*args, **kwargs):
        raise RuntimeError("checkpoint unavailable")

    provider.queue.append({"items": [native_plan()]})
    with pytest.raises(RuntimeError, match="checkpoint unavailable"):
        _ = [event async for event in model_driver.drive_model(
            model="deepseek-synthesis-test", api_key="test", user_input="写报告", tools=[],
            gateway={"research_synthesis_only": True, "checkpoint_sink": cannot_save}, research_profile=True,
        )]
    assert len(provider.payloads) == 1


def test_synthesis_correction_is_scoped_to_one_run():
    name = model_driver._research_synthesis_correction_name("run-1")
    assert name == model_driver._research_synthesis_correction_name("run-1")
    assert name != model_driver._research_synthesis_correction_name("run-2")
    assert len(name) <= 64


@pytest.mark.asyncio
async def test_compaction_preserves_the_committed_correction_marker(monkeypatch):
    from app.services.agent_harness import conversation_compact

    marker = {"role": "user", "name": model_driver._research_synthesis_correction_name(""),
              "content": "保持核验格式，不调用工具"}
    messages = [{"role": "user", "content": "长历史" * 100}, marker]
    monkeypatch.setattr(conversation_compact, "should_compact_history", lambda *args, **kwargs: True)
    monkeypatch.setattr(conversation_compact, "compact_live_messages", AsyncMock(return_value={
        "messages": [{"role": "user", "content": "概要"}], "tokens_before": 500, "tokens_after": 20,
    }))
    checkpoint = AsyncMock()
    events = [event async for event in model_driver._maybe_compact_live_history(
        messages, model="m", api_key="test", step=1,
        gateway={"research_synthesis_only": True, "checkpoint_sink": checkpoint},
    )]
    assert any(event.get("status") == "completed" for event in events)
    assert messages[-1] == marker
    assert checkpoint.call_args.args[0][-1] == marker


@pytest.mark.asyncio
@pytest.mark.parametrize("prose", ["", "已经核验的报告内容。" * 100])
async def test_unadvertised_plan_cannot_use_either_plan_fast_path(provider, monkeypatch, prose):
    persist = AsyncMock()
    monkeypatch.setattr("app.services.tasks.plan_service.upsert_plan", persist)
    provider.queue.extend([{"items": [native_answer(prose), native_plan()]}, {"items": [native_answer()]}])
    events = [e async for e in model_driver.drive_model(
        model="deepseek-synthesis-test", api_key="test", user_input="回答问题", tools=[],
    )]
    assert not any(e["type"] == "task_plan" for e in events)
    persist.assert_not_awaited()
    assert any(e["type"] == "final" for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 405, 415, 422])
async def test_invalid_request_stops_once_and_never_enters_worker_recovery(provider, pump, status):
    provider.queue.append({"status": status, "body": {"error": {
        "type": "invalid_request_error", "message": "No tool call found for tool output with call_id private-id"}}})
    with pytest.raises(ModelRequestRejected) as exc:
        _ = [e async for e in model_driver.drive_model(
            model="deepseek-synthesis-test", api_key="test", user_input="写报告", tools=[],
            initial_messages=[
                {"role": "user", "content": "写报告"},
                {"role": "assistant", "content": "", "_responses_output_items": [
                    {"type": "reasoning", "encrypted_content": "opaque-existing-cursor"}]},
            ],
        )]
    assert len(provider.payloads) == 1
    await run_failure(pump, exc.value)
    assert pump.state["status"] == "failed"
    assert "private-id" not in str(pump.recorded)
    pump.recovery.assert_not_awaited()
    pump.context_quarantine.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_request_after_allowed_protocol_fallback_is_still_terminal(provider, pump):
    provider.queue.extend([
        {"status": 500, "body": {"error": {"code": "convert_request_failed", "message": "not implemented"}}},
        {"status": 400, "body": {"error": {"type": "invalid_request_error", "message": "invalid payload"}}},
    ])
    with pytest.raises(ModelRequestRejected) as exc:
        _ = [e async for e in model_driver.drive_model(
            model="deepseek-synthesis-test", api_key="test", user_input="写报告", tools=[],
        )]
    assert len(provider.payloads) == 2
    assert provider.urls[0].endswith("/responses")
    assert provider.urls[1].endswith("/chat/completions")
    await run_failure(pump, exc.value)
    assert pump.state["status"] == "failed"
    pump.recovery.assert_not_awaited()


@pytest.mark.asyncio
async def test_context_overflow_retains_recovery_instead_of_invalid_request_terminal(provider, pump):
    from app.services.agent_harness.model_stream import ModelProviderHTTPError

    provider.queue.append({"status": 400, "body": {"error": {"code": "context_length_exceeded"}}})
    with pytest.raises(ModelProviderHTTPError) as exc:
        _ = [e async for e in model_driver.drive_model(
            model="deepseek-synthesis-test", api_key="test", user_input="写报告", tools=[],
        )]
    assert len(provider.payloads) == 1
    await run_failure(pump, exc.value)
    assert pump.state["status"] == "waiting_system"
    pump.recovery.assert_awaited_once()


def test_unsupported_token_parameter_is_not_misclassified_as_context_overflow():
    assert model_driver._is_terminal_model_request_error(400, "Unsupported parameter: max_tokens")
    assert not model_driver._is_terminal_model_request_error(400, "Prompt exceeds maximum context length")
    assert not model_driver._is_terminal_model_request_error(429, "rate limit")
    assert not model_driver._is_terminal_model_request_error(503, "service unavailable")


@pytest.mark.parametrize("status,body,expected", [
    (401, "invalid api key: private-key", "鉴权失败"),
    (402, "payment required private-key", "额度不足"),
    (403, "insufficient_quota private-key", "额度不足"),
    (403, "permission_denied private-key", "访问权限"),
])
def test_access_failures_have_safe_actionable_terminal_messages(status, body, expected):
    from app.services.agent_harness.public_errors import public_terminal_reason

    error = ModelRequestRejected(status, body)
    assert model_driver._is_terminal_model_request_error(status, body)
    assert expected in str(error)
    assert "private-key" not in str(error)
    assert public_terminal_reason(str(error), phase="failed") == str(error)
