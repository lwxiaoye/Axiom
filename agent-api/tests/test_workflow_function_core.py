"""No real Provider, live business tool, or user's Runtime database is used here."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.agents.agent_executor import AgentToolError, ToolSpec, run_function_call_loop
from app.services.agents.agent_service import agent_service
from app.services.agents.execution_context import ExecutionContext, input_tokens
from app.services.agents.execution_results import ExecutionResults, RepeatedResults, TemporaryResultStore, encode
from app.services.agent_harness import conversation_compact, function_round, model_stream, tool_result_store
from app.services.agent_harness import model_usage_audit
from app.services.agent_harness.responses_protocol import ResponsesTerminalError
from app.services.agent_harness.function_round import FunctionRoundClient
from app.services.agent_harness.tool_result_store import DurableToolResultStore, MAX_DURABLE_TOOL_RESULT_BYTES


def sse(value):
    return "data: " + json.dumps(value, ensure_ascii=False)


def chat(*, text="", name="", args="{}", call_id="call-1", finish=None, done=True):
    delta = {"content": text} if text else {}
    if name:
        delta["tool_calls"] = [{"index": 0, "id": call_id, "type": "function", "function": {"name": name, "arguments": args}}]
    lines = [sse({"choices": [{"delta": delta}]})]
    if finish != "missing":
        lines.append(sse({"choices": [{"delta": {}, "finish_reason": finish or ("tool_calls" if name else "stop")}],
                          "usage": {"prompt_tokens": 20, "completion_tokens": 5}}))
    if done:
        lines.append("data: [DONE]")
    return Response(lines)


def responses(*, text="", name="", args="{}", terminal="completed", phase="final_answer"):
    item = ({"id": "fc1", "type": "function_call", "call_id": "call-1", "name": name, "arguments": args}
            if name else {"id": "m1", "type": "message", "role": "assistant", "phase": phase,
                          "content": [{"type": "output_text", "text": text}]})
    return Response([
        sse({"type": "response.output_item.added", "output_index": 0, "item": item}),
        sse({"type": "response.output_item.done", "output_index": 0, "item": item}),
        sse({"type": f"response.{terminal}", "response": {"status": terminal, "usage": {"input_tokens": 12},
                                                         "incomplete_details": {"reason": "max_output_tokens"}}}),
    ])


class Response:
    def __init__(self, lines=(), status=200, body=""):
        self.lines, self.status_code, self.body = lines, status, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def aread(self):
        return self.body.encode()

    async def aiter_lines(self):
        for line in self.lines:
            if isinstance(line, BaseException):
                raise line
            yield line


class Client:
    queue = []
    requests = []

    def __init__(self, **_):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def stream(self, method, url, json, headers):
        self.requests.append({"url": url, "payload": deepcopy(json), "headers": headers})
        value = self.queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value(json) if callable(value) else value


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    Client.queue, Client.requests = [], []
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(agent_service, "model_responses_capability", lambda *_: False)
    monkeypatch.setattr(model_stream, "_model_stream_retry_delay", lambda *_: 0)
    monkeypatch.setattr(conversation_compact.model_window, "resolve_window", lambda _: 32768)
    for name in ("begin_logical_call", "begin_attempt", "finish_attempt", "finish_logical_call", "has_recent_failed_logical_call"):
        monkeypatch.setattr(model_usage_audit, name, AsyncMock(return_value=None))


def engine(**overrides):
    chunks = []

    async def stream(seq, text):
        chunks.append((seq, text))

    return SimpleNamespace(ctx=SimpleNamespace(**{
        "variables": {}, "llm_api_key": "test-key", "user_id": "u1", "run_id": "wf-run",
        "thread_id": "", "preview_only": False, "stream_output": stream, "chunks": chunks, **overrides,
    }))


def tool(execute, name="lookup", parameters=None):
    return ToolSpec(name=name, description=name,
                    parameters=parameters or {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"], "additionalProperties": False},
                    execute=execute)


async def run(target, tools=(), **overrides):
    return await run_function_call_loop(target, **{
        "model": "test-model", "temperature": None, "system_prompt": "只使用已证实的信息。",
        "max_histories": 0, "user_input": "核对资料，不能编造。", "tools": list(tools), **overrides,
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [30, 105])
async def test_unbounded_valid_tool_rounds_then_final(count, monkeypatch):
    called = []

    async def lookup(args):
        called.append(args)
        return f"fact {args['n']}"

    async def no_compaction(*_, **__):
        pytest.fail("ordinary rounds should not force a summary")

    monkeypatch.setattr(conversation_compact, "generate_compaction_summary", no_compaction)
    Client.queue = [chat(name="lookup", args=encode({"n": i}), call_id=f"c{i}") for i in range(count)] + [chat(text="核对完成")]
    answer, trace = await run(engine(), [tool(lookup)])
    assert answer == "核对完成" and len(trace) == len(called) == count
    assert len(Client.requests) == count + 1
    for previous, current in zip(Client.requests, Client.requests[1:]):
        assert previous["payload"]["tools"] == current["payload"]["tools"]
        prefix = previous["payload"]["messages"]
        assert current["payload"]["messages"][:len(prefix)] == prefix
    assert "调用太多" not in encode(Client.requests)


@pytest.mark.asyncio
async def test_direct_answer_one_model_call_and_history_zero():
    target = engine(variables={"histories": [{"role": "user", "content": "OLD-SECRET"}]})
    Client.queue = [chat(text="你好")]
    assert (await run(target))[0] == "你好"
    assert len(Client.requests) == 1
    assert "OLD-SECRET" not in encode(Client.requests)
    assert target.ctx.chunks == [(0, "你好")]


@pytest.mark.asyncio
@pytest.mark.parametrize("args", ['{"n":', '[]', 'null', '"x"', '{"n":"1"}', '{}', '{"n":1,"extra":2}', '{"n":NaN}', '{"n":1,"n":2}', ''])
async def test_bad_arguments_are_feedback_not_execution(args):
    invoked = []

    async def lookup(value):
        invoked.append(value)

    Client.queue = [chat(name="lookup", args=args), chat(text="请补充参数")]
    answer, _ = await run(engine(), [tool(lookup)])
    assert answer == "请补充参数" and not invoked
    feedback = json.loads(Client.requests[-1]["payload"]["messages"][-1]["content"])
    assert feedback["ok"] is False and feedback["execution_started"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("finish,done", [("length", True), ("content_filter", True), ("missing", True), ("missing", False)])
async def test_incomplete_chat_never_executes_or_emits(finish, done):
    invoked = []

    async def lookup(args):
        invoked.append(args)

    target = engine()
    Client.queue = [chat(text="internal draft", name="lookup", args='{"n":1}', finish=finish, done=done)]
    with pytest.raises((RuntimeError, ResponsesTerminalError)):
        await run(target, [tool(lookup)])
    assert not invoked and not target.ctx.chunks
    assert len(Client.requests) == 1


@pytest.mark.asyncio
async def test_tool_round_draft_not_in_final():
    async def lookup(_):
        return "source"

    target = engine()
    Client.queue = [chat(text="INTERNAL-DRAFT", name="lookup", args='{"n":1}'), chat(text="verified answer")]
    answer, _ = await run(target, [tool(lookup)])
    assert answer == "verified answer" and target.ctx.chunks == [(0, "verified answer")]


@pytest.mark.asyncio
async def test_stop_tool_and_failed_stop_tool():
    async def ok(_):
        return "actual tool result"

    Client.queue = [chat(name="lookup", args='{"n":1}')]
    assert (await run(engine(), [tool(ok)], stop_after_tool=lambda _: True))[0] == "actual tool result"
    assert len(Client.requests) == 1

    async def denied(_):
        return {"ok": False, "error": "permission denied"}

    Client.queue = [chat(name="lookup", args='{"n":1}')]
    with pytest.raises(AgentToolError):
        await run(engine(), [tool(denied)], stop_after_tool=lambda _: True)


@pytest.mark.asyncio
async def test_cancellation_propagates_and_cleans_preview_store(monkeypatch):
    stores = []
    original = TemporaryResultStore.__init__

    def create(self):
        original(self)
        stores.append(self.directory.name)

    monkeypatch.setattr(TemporaryResultStore, "__init__", create)

    async def cancel(_):
        raise asyncio.CancelledError()

    Client.queue = [chat(name="lookup", args='{"n":1}')]
    with pytest.raises(asyncio.CancelledError):
        await run(engine(preview_only=True), [tool(cancel)])
    assert all(not Path(path).exists() for path in stores)


def test_repeated_feedback_only_once_until_args_or_result_changes():
    state = RepeatedResults()
    assert [state.observe("read", {"x": 1}, value) for value in ["a", "a", "a", "b", "b", "b"]] == [False, True, False, False, True, False]
    assert not state.observe("read", {"x": 2}, "b")
    assert state.observe("read", {"x": 2}, "b")


@pytest.mark.asyncio
async def test_repeats_do_not_terminate_or_disable_tools():
    async def lookup(_):
        return "same result"

    Client.queue = [chat(name="lookup", args='{"n":1}', call_id=f"c{i}") for i in range(7)] + [chat(text="final")]
    _, traces = await run(engine(), [tool(lookup)])
    assert len(traces) == 7
    assert sum("结果未变化" in row["result"] for row in traces) == 1
    assert all(len(req["payload"]["tools"]) == 2 for req in Client.requests)


@pytest.mark.asyncio
async def test_large_json_keeps_tail_image_and_can_reassemble_original():
    scope = ExecutionResults(engine(preview_only=True).ctx)
    raw = encode({"body": "中" * 9500, "source": "学校确认资料", "image_url": "/api/map.jpg", "resource_id": "MAP-000"})
    try:
        projected = json.loads(await scope.project(raw, name="lookup", call_id="call"))
        assert projected["full_available"] and len(encode(projected)) <= scope.inline_chars
        assert "/api/map.jpg" in encode(projected["references"])
        assert "MAP-000" in encode(projected["references"])
        pieces, offset = [], 0
        while True:
            page = json.loads(await scope.read({"result_handle": projected["result_handle"], "offset": offset}))
            pieces.append(page["content"])
            if page["complete"]:
                break
            assert page["next_offset"] > offset
            offset = page["next_offset"]
        assert json.loads("".join(pieces)) == json.loads(raw)
    finally:
        scope.close()


@pytest.mark.asyncio
async def test_many_references_have_bounded_paginated_index():
    scope = ExecutionResults(engine().ctx)
    raw = encode({"images": [f"/api/image-{i}.jpg" for i in range(1500)]})
    try:
        data = json.loads(await scope.project(raw, name="images", call_id="c"))
        assert len(encode(data)) <= scope.inline_chars
        assert data["reference_index_handle"] and not data["references_complete"]
        page = json.loads(await scope.read({"result_handle": data["result_handle"], "view": "references"}))
        assert page["ok"] and "image-0.jpg" in page["content"]
    finally:
        scope.close()


@pytest.mark.asyncio
async def test_preview_capabilities_isolate_even_same_parent_context():
    ctx = engine(thread_id="business-thread", preview_only=True).ctx
    first, second = ExecutionResults(ctx), ExecutionResults(ctx)
    try:
        ref = await first.save("secret", name="tool", call_id="c")
        assert first.owner["run_id"] != second.owner["run_id"]
        assert json.loads(await second.read({"result_handle": ref.handle}))["ok"] is False
        assert json.loads(await first.read({"result_handle": "/api/secret"}))["ok"] is False
        assert await first.store.get_page(handle=ref.handle, **{**first.owner, "user_id": "other"}) is None
    finally:
        first.close()
        second.close()


@pytest.mark.asyncio
async def test_persistent_workflow_result_needs_no_main_run_and_keeps_acl(monkeypatch):
    from app.runtime_models import AgentToolResultBlob
    db = create_async_engine("sqlite+aiosqlite://")
    async with db.begin() as conn:
        await conn.run_sync(AgentToolResultBlob.__table__.create)
    factory = async_sessionmaker(db, expire_on_commit=False)
    monkeypatch.setattr(tool_result_store, "runtime_session", lambda: factory)
    scope = ExecutionResults(engine(thread_id="real-thread").ctx)
    try:
        assert isinstance(scope.store, DurableToolResultStore) and not scope.temporary
        ref = await scope.save("persistent value", name="tool", call_id="c")
        assert ref.full_available
        async with factory() as session:
            row = (await session.execute(select(AgentToolResultBlob))).scalar_one()
            assert row.run_id is None and row.workflow_execution_id == scope.execution_id
        assert json.loads(await scope.read({"result_handle": ref.handle}))["content"] == "persistent value"
        for key in ("run_id", "thread_id", "user_id"):
            assert await scope.store.get_page(handle=ref.handle, **{**scope.owner, key: "foreign"}) is None
        assert await DurableToolResultStore().get_page(handle=ref.handle, **scope.owner) is None
    finally:
        scope.close()
        await db.dispose()


@pytest.mark.asyncio
async def test_store_failure_never_claims_saved_and_never_replays_tool(monkeypatch):
    async def fail_put(self, **_):
        raise OSError("full disk")

    monkeypatch.setattr(TemporaryResultStore, "put", fail_put)
    invoked = []

    async def lookup(_):
        invoked.append(True)
        return "x" * 10000

    Client.queue = [chat(name="lookup", args='{"n":1}'), chat(text="部分资料不可读取")]
    await run(engine(), [tool(lookup)])
    projected = json.loads(Client.requests[-1]["payload"]["messages"][-1]["content"])
    assert len(invoked) == 1 and projected["full_available"] is False
    assert not projected.get("result_handle")


@pytest.mark.asyncio
async def test_capacity_limit_honest_unavailability():
    scope = ExecutionResults(engine().ctx)
    try:
        result = json.loads(await scope.project("x" * (MAX_DURABLE_TOOL_RESULT_BYTES + 1), name="big", call_id="c"))
        assert result["full_available"] is False
        assert result["unavailable_reason"] == "result_exceeds_2_mib_limit"
    finally:
        scope.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "incomplete", "failed"])
async def test_responses_terminal_and_frozen_protocol(monkeypatch, terminal):
    monkeypatch.setattr(agent_service, "model_responses_capability", lambda *_: True)
    invoked = []

    async def lookup(_):
        invoked.append(True)
        monkeypatch.setattr(agent_service, "model_responses_capability", lambda *_: False)
        return "source"

    Client.queue = [responses(name="lookup", args='{"n":1}', terminal=terminal), responses(text="final")]
    target = engine()
    if terminal == "completed":
        assert (await run(target, [tool(lookup)]))[0] == "final"
        assert len(invoked) == 1 and all(req["url"].endswith("/responses") for req in Client.requests)
    else:
        with pytest.raises(ResponsesTerminalError):
            await run(target, [tool(lookup)])
        assert not invoked and not target.ctx.chunks and len(Client.requests) == 1


@pytest.mark.asyncio
async def test_safe_zero_output_protocol_negotiation_then_lock(monkeypatch):
    monkeypatch.setattr(agent_service, "model_responses_capability", lambda *_: None)
    Client.queue = [Response(status=404, body="unsupported endpoint"), chat(text="final")]
    assert (await run(engine()))[0] == "final"
    assert [req["url"].rsplit("/", 1)[-1] for req in Client.requests] == ["responses", "completions"]


@pytest.mark.asyncio
async def test_network_retry_before_event_but_not_after_event():
    Client.queue = [httpx.ConnectError("connect"), chat(text="recovered")]
    assert (await run(engine()))[0] == "recovered"
    assert len(Client.requests) == 2
    Client.requests = []
    Client.queue = [Response([sse({"choices": [{"delta": {"content": "partial"}}]}), httpx.ReadError("lost")])]
    target = engine()
    with pytest.raises(httpx.ReadError):
        await run(target)
    assert len(Client.requests) == 1 and not target.ctx.chunks


@pytest.mark.asyncio
async def test_context_compaction_keeps_goal_images_and_handles(monkeypatch):
    target = engine()
    scope = ExecutionResults(target.ctx)
    client = FunctionRoundClient(model="test-model", api_key="test-key", ctx=target.ctx)
    user = {"role": "user", "content": [{"type": "text", "text": "只给已确认地图，不能编造"}, {"type": "image_url", "image_url": {"url": "/api/attachment.jpg"}}]}
    messages = [{"role": "system", "content": "固定规则"}, user, {"role": "assistant", "content": "x" * 100000}]
    # Constructor's current-user anchor is the real input; append tool rounds afterwards.
    state = ExecutionContext(messages=messages[:2], tools=[], model_client=client, results=scope, output_tokens=4096)
    state.messages = messages
    capture = []

    async def summary(current, **kwargs):
        capture.append((deepcopy(current), kwargs))
        return "已确认 MAP-000；来源学校，图片 /api/map.jpg。待回答流程图。"

    monkeypatch.setattr(conversation_compact, "generate_compaction_summary", summary)
    try:
        ref = await scope.save("last image", name="map", call_id="map1")
        await state.prepare()
        assert state.compactions == 1 and state.messages[0] == messages[0] and state.messages[-1] == user
        assert "/api/map.jpg" in encode(state.messages)
        assert capture[0][1]["transport_override"] == "chat_completions" and capture[0][1]["strict_terminal"]
        assert json.loads(await scope.read({"result_handle": ref.handle}))["content"] == "last image"
        assert "result_index" in encode(state.messages)
    finally:
        scope.close()


@pytest.mark.asyncio
async def test_failed_compaction_preserves_input_and_is_not_retried(monkeypatch):
    target = engine()
    scope = ExecutionResults(target.ctx)
    client = FunctionRoundClient(model="test-model", api_key="test-key", ctx=target.ctx)
    state = ExecutionContext(messages=[{"role": "user", "content": "goal"}], tools=[], model_client=client, results=scope, output_tokens=4096)
    state.messages.append({"role": "assistant", "content": "x" * 130000})
    old = deepcopy(state.messages)
    called = []

    async def fail(*_, **__):
        called.append(True)
        raise RuntimeError("summary failed")

    monkeypatch.setattr(conversation_compact, "generate_compaction_summary", fail)
    try:
        with pytest.raises(RuntimeError, match="summary failed"):
            await state.prepare()
        with pytest.raises(RuntimeError, match="已尝试压缩"):
            await state.prepare()
        assert len(called) == 1 and state.messages == old
    finally:
        scope.close()


def test_input_budget_includes_tools_images_and_not_base64_as_text():
    small = [{"role": "user", "content": "hello"}]
    image = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + "x" * 20000}}]}]
    assert input_tokens(image, [], model="m") >= 4096
    assert input_tokens(small, [{"schema": "x" * 10000}], model="m") > input_tokens(small, [], model="m")


@pytest.mark.asyncio
async def test_explicit_overflow_compacts_then_continues_same_question(monkeypatch):
    target = engine(variables={"histories": [{"role": "user", "content": "past " * 500}, {"role": "assistant", "content": "answer " * 1000}]})
    called = []

    async def summary(*_, **__):
        called.append(True)
        return "已确认背景事实。"

    monkeypatch.setattr(conversation_compact, "generate_compaction_summary", summary)
    Client.queue = [Response(status=413, body="context window exceeded"), chat(text="continued")]
    assert (await run(target, max_histories=1))[0] == "continued"
    assert len(called) == 1 and len(Client.requests) == 2
    assert Client.requests[0]["payload"]["tools"] == Client.requests[1]["payload"]["tools"]


@pytest.mark.asyncio
async def test_context_larger_than_one_blob_uses_recoverable_pages():
    scope = ExecutionResults(engine().ctx)
    original = [{"role": "user", "content": "中" * 800000}]
    try:
        checkpoint = await scope.checkpoint_manifest(original)
        assert checkpoint["original_context"]["full_available"]
        index = json.loads((await scope.store.get_page(
            handle=checkpoint["original_context"]["handle"], **scope.owner,
        )).content)
        assert len(index["pages"]) > 1
        restored = []
        for part in index["pages"]:
            offset = 0
            while True:
                page = await scope.store.get_page(handle=part["result_handle"], **scope.owner, offset=offset)
                restored.append(page.content)
                if page.complete:
                    break
                offset = page.next_offset
        assert json.loads("".join(restored)) == original
    finally:
        scope.close()


@pytest.mark.asyncio
async def test_store_failure_still_preserves_tail_references(monkeypatch):
    async def fail(self, **_):
        raise OSError("full")

    monkeypatch.setattr(TemporaryResultStore, "put", fail)
    scope = ExecutionResults(engine().ctx)
    try:
        raw = encode({"body": "x" * 9000, "image_url": "/api/tail.jpg", "resource_id": "QR-001"})
        result = json.loads(await scope.project(raw, name="query", call_id="c", failed=True))
        assert result["full_available"] is False
        assert result["tool_execution_status"] == "error"
        assert "/api/tail.jpg" in encode(result["references"])
    finally:
        scope.close()


@pytest.mark.asyncio
async def test_control_characters_read_page_stays_bounded():
    scope = ExecutionResults(engine().ctx)
    try:
        ref = await scope.save("\x00" * 10000, name="binaryish", call_id="c")
        result = await scope.read({"result_handle": ref.handle, "limit": 3000})
        assert len(result) <= scope.inline_chars
        page = json.loads(result)
        assert 0 < page["next_offset"] == len(page["content"]) < 3000
    finally:
        scope.close()


@pytest.mark.asyncio
async def test_chat_compaction_requires_stop_and_keeps_frozen_audit_parent(monkeypatch):
    captures = []

    class PostClient(Client):
        async def post(self, url, *, json, headers):
            captures.append((url, json, headers))
            return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"finish_reason": "length", "message": {"content": "bad summary"}}]})

    monkeypatch.setattr(httpx, "AsyncClient", PostClient)
    monkeypatch.setattr(conversation_compact, "_compaction_transport", AsyncMock(side_effect=AssertionError("must use frozen protocol")))
    conversation_compact._COMPACTION_FAILURES.clear()
    with pytest.raises(RuntimeError, match="did not complete"):
        await conversation_compact.generate_compaction_summary(
            [{"role": "user", "content": "goal"}], model="m", api_key="same-user-key",
            request_scope_id="strict-test", transport_override="chat_completions", strict_terminal=True,
            parent_logical_call_id="parent-model", parent_tool_call_id="parent-tool",
        )
    assert captures[0][0].endswith("/chat/completions")
    assert captures[0][2]["Authorization"] == "Bearer same-user-key"
    owner = model_usage_audit.begin_logical_call.call_args.kwargs
    assert owner["parent_logical_call_id"] == "parent-model" and owner["parent_tool_call_id"] == "parent-tool"
    assert model_usage_audit.finish_attempt.call_args.kwargs["committed"] is False
    conversation_compact._COMPACTION_FAILURES.clear()


@pytest.mark.asyncio
async def test_malformed_native_arguments_not_defaulted_to_empty_object(monkeypatch):
    monkeypatch.setattr(agent_service, "model_responses_capability", lambda *_: True)
    called = []

    async def lookup(args):
        called.append(args)

    Client.queue = [responses(name="lookup", args=""), responses(text="参数需要修正")]
    await run(engine(), [tool(lookup, parameters={"type": "object"})])
    assert not called


@pytest.mark.asyncio
async def test_native_parser_failure_is_failed_not_cancelled_audit(monkeypatch):
    monkeypatch.setattr(agent_service, "model_responses_capability", lambda *_: True)
    Client.queue = [Response([sse({"error": {"code": "provider_failure"}})])]
    with pytest.raises(ResponsesTerminalError):
        await run(engine())
    assert model_usage_audit.finish_attempt.call_count == 1
    assert model_usage_audit.finish_attempt.call_args.kwargs["terminal_status"] == "failed"
    assert model_usage_audit.finish_attempt.call_args.kwargs["committed"] is False


@pytest.mark.asyncio
async def test_nested_workflow_propagates_preview_identity(monkeypatch):
    from app.services.gateway import tool_invoker
    from app.services.workflows import workflow_engine
    seen = []

    class Engine:
        def __init__(self, graph, ctx):
            seen.append(ctx)

        async def run(self):
            pass

    monkeypatch.setattr(tool_invoker, "load_tool_definition", AsyncMock(return_value='{"fastgpt":{"nodes":[{"nodeId":"s","flowNodeType":"workflowStart"}],"edges":[]}}'))
    monkeypatch.setattr(workflow_engine, "WorkflowEngine", Engine)
    parent = workflow_engine.RunContext(input_text="hello", user_id="u1", thread_id="t1", run_id="r1",
                                        app_id="parent", preview_only=True, audit_parent_logical_call_id="lg-parent")
    await tool_invoker.run_sub_workflow("child", "child", "query", parent_ctx=parent)
    assert seen[0].preview_only and seen[0].thread_id == "t1" and seen[0].user_id == "u1"
    assert seen[0].audit_parent_logical_call_id == "lg-parent"


def test_runtime_migration_has_exclusive_owner_not_fake_run():
    from app.runtime_models import AgentToolResultBlob
    from app.core.runtime_db import RUNTIME_SCHEMA_HEAD
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects import postgresql
    sql = str(CreateTable(AgentToolResultBlob.__table__).compile(dialect=postgresql.dialect()))
    assert "workflow_execution_id" in sql and "ck_tool_result_execution_owner" in sql
    assert "fk_agent_tool_result_blob_run" in sql
    assert RUNTIME_SCHEMA_HEAD == "runtime_0020_workflow_results"
    migration = Path(__file__).resolve().parents[1] / "migrations/versions/runtime/0020_workflow_tool_results.py"
    source = migration.read_text()
    assert 'down_revision = "runtime_0019_prompt_history"' in source
    assert "CREATE TABLE" not in source


@pytest.mark.asyncio
async def test_reasoning_cursor_replayed_but_not_public_answer():
    async def lookup(_):
        return "found"

    first = chat(name="lookup", args='{"n":1}')
    first.lines.insert(0, sse({"choices": [{"delta": {"reasoning_content": "private cursor"}}]}))
    Client.queue = [first, chat(text="public answer")]
    target = engine()
    await run(target, [tool(lookup)])
    assistant = next(m for m in Client.requests[-1]["payload"]["messages"] if m["role"] == "assistant")
    assert assistant["reasoning_content"] == "private cursor"
    assert "private cursor" not in encode(target.ctx.chunks)


@pytest.mark.asyncio
async def test_parameter_rejection_is_not_mistaken_for_context_overflow(monkeypatch):
    compact = AsyncMock(side_effect=AssertionError("not a context overflow"))
    monkeypatch.setattr(conversation_compact, "generate_compaction_summary", compact)
    Client.queue = [Response(status=400, body="unknown parameter max_tokens")]
    with pytest.raises(model_stream.ModelProviderHTTPError):
        await run(engine())
    assert compact.call_count == 0 and len(Client.requests) == 1


@pytest.mark.asyncio
async def test_single_request_deadline_is_failure_not_user_cancellation():
    client = FunctionRoundClient(model="m", api_key="key", ctx=engine().ctx)
    Client.queue = [asyncio.CancelledError()]
    with pytest.raises(TimeoutError, match="单次模型请求超时"):
        await client._complete(Client(), [{"role": "user", "content": "hello"}], [],
                               deadline_guard=SimpleNamespace(expired=lambda: True))
    assert model_usage_audit.finish_attempt.call_args.kwargs["terminal_status"] == "failed"
    assert model_usage_audit.finish_attempt.call_args.kwargs["error_code"] == "model_request_timeout"


@pytest.mark.asyncio
async def test_user_cancelled_model_round_stays_cancelled():
    Client.queue = [asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        await run(engine())
    assert model_usage_audit.finish_attempt.call_args.kwargs["terminal_status"] == "cancelled"
