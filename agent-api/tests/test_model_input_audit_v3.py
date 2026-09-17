import json
from types import SimpleNamespace

import pytest

from app.services.chat import model_input_audit as audit


def _install_v4_audit_spy(monkeypatch, model_driver):
    logical_calls = []
    attempts = []
    terminals = []
    logical_terminals = []

    async def begin_logical_call(**kwargs):
        logical_calls.append(kwargs)
        return SimpleNamespace(
            logical_call_id=f"logical-{len(logical_calls)}",
            transport=kwargs["transport"],
            run_id=kwargs["run_id"],
        )

    async def begin_attempt(logical_call, **kwargs):
        attempts.append({"logical_call": logical_call, **kwargs})
        return SimpleNamespace(
            attempt_id=f"attempt-{len(attempts)}",
            logical_call_id=logical_call.logical_call_id,
            request_id=f"request-{len(attempts)}",
            transport=logical_call.transport,
        )

    async def finish_attempt(handle, **kwargs):
        terminals.append({"handle": handle, **kwargs})
        return True

    async def finish_logical_call(handle, **kwargs):
        logical_terminals.append({"handle": handle, **kwargs})
        return True

    async def load_projection(**_kwargs):
        return None

    async def load_projection_transitions(**_kwargs):
        return []

    async def save_projection(_state, *, run_id=None, **_kwargs):
        return True

    monkeypatch.setattr(model_driver.model_usage_audit, "begin_logical_call", begin_logical_call)
    monkeypatch.setattr(model_driver.model_usage_audit, "begin_attempt", begin_attempt)
    monkeypatch.setattr(model_driver.model_usage_audit, "finish_attempt", finish_attempt)
    monkeypatch.setattr(model_driver.model_usage_audit, "finish_logical_call", finish_logical_call)
    monkeypatch.setattr(model_driver.thread_projection_store, "load", load_projection)
    monkeypatch.setattr(
        model_driver.thread_projection_store,
        "load_transport_transition_candidates",
        load_projection_transitions,
    )
    monkeypatch.setattr(model_driver.thread_projection_store, "save", save_projection)
    return logical_calls, attempts, terminals, logical_terminals


def test_sanitize_removes_credentials_and_hidden_reasoning():
    clean = audit._sanitize({
        "messages": [{"role": "user", "content": "hello", "reasoning_content": "secret-thought"}],
        "Authorization": "Bearer secret",
        "api_key": "secret",
    })
    assert clean["Authorization"] == "[redacted]"
    assert clean["api_key"] == "[redacted]"
    assert "reasoning_content" not in clean["messages"][0]


@pytest.mark.asyncio
async def test_missing_runtime_database_is_fail_open(monkeypatch):
    monkeypatch.setattr(audit, "runtime_session", lambda: None)
    result = await audit.record_model_request(
        payload={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        run_id="run-1", model="m", request_sequence=1,
    )
    assert result is None


@pytest.mark.asyncio
async def test_persisted_snapshot_contains_manifest_and_mismatch_state(monkeypatch):
    captured = {}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def add(self, row):
            captured["row"] = row

        async def commit(self):
            captured["committed"] = True

    monkeypatch.setattr(audit, "runtime_session", lambda: Session)
    handle = await audit.record_model_request(
        payload={
            "model": "m",
            "messages": [{"role": "user", "content": "visible"}],
            "tools": [{"type": "function", "function": {"name": "read", "parameters": {}}}],
        },
        shadow_payload={"model": "m", "messages": [{"role": "user", "content": "drift"}]},
        run_id="run-2", thread_id="thread-2", model="m", request_sequence=2,
    )
    row = captured["row"]
    assert handle is not None
    assert handle.request_id == row.request_id and captured["committed"] is True
    assert row.match_status == "mismatch"
    assert {item["kind"] for item in row.source_manifest} == {"user_message", "tool_schema"}


def test_manifest_distinguishes_model_visible_sources():
    manifest = audit._manifest({
        "messages": [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "request"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
        ],
        "tools": [{"type": "function", "function": {"name": "read", "parameters": {}}}],
    })
    assert [item["kind"] for item in manifest] == [
        "system_prompt", "user_message", "tool_call", "tool_result", "tool_schema",
    ]


def test_shadow_hash_detects_builder_drift():
    visible = audit._sanitize({"messages": [{"role": "user", "content": "a"}]})
    shadow = audit._sanitize({"messages": [{"role": "user", "content": "b"}]})
    assert audit._canonical_hash(visible) != audit._canonical_hash(shadow)


def test_prefix_diagnostics_never_store_message_content():
    previous = {
        "messages": [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "private-before"},
        ],
        "tools": [{"type": "function", "function": {"name": "read", "parameters": {}}}],
    }
    current = {
        "messages": previous["messages"] + [{"role": "assistant", "content": "private-after"}],
        "tools": previous["tools"],
    }
    diagnostics = audit._prefix_diagnostics(previous, current, previous_request_id="req-1")
    assert diagnostics["lcp_item_count"] == 2
    assert diagnostics["previous_item_count"] == 2
    assert diagnostics["tool_schema_equal"] is True
    assert "private-before" not in json.dumps(diagnostics)
    assert "private-after" not in json.dumps(diagnostics)


def test_normalize_deepseek_cache_usage_prefers_reported_hit_and_miss():
    usage = audit.normalize_cache_usage({
        "prompt_tokens": 100,
        "completion_tokens": 12,
        "prompt_cache_hit_tokens": 80,
        "prompt_cache_miss_tokens": 20,
    })
    assert usage.input_tokens == 100
    assert usage.output_tokens == 12
    assert usage.cache_read_tokens == 80
    assert usage.cache_miss_tokens == 20
    assert usage.cache_miss_source == "reported"
    assert usage.usage_schema == "deepseek"

    with_write = audit.normalize_cache_usage({
        "prompt_tokens": 100,
        "completion_tokens": 12,
        "prompt_cache_hit_tokens": 80,
        "prompt_cache_miss_tokens": 20,
        "prompt_cache_write_tokens": 16,
    })
    assert with_write.cache_write_tokens == 16


def test_normalize_responses_cache_usage_derives_miss_only_when_cached_is_reported():
    usage = audit.normalize_cache_usage({
        "input_tokens": 100,
        "output_tokens": 8,
        "input_tokens_details": {"cached_tokens": 70},
    })
    assert usage.cache_read_tokens == 70
    assert usage.cache_miss_tokens == 30
    assert usage.cache_miss_source == "derived"
    assert usage.usage_schema == "responses"

    unsupported = audit.normalize_cache_usage({"input_tokens": 100, "output_tokens": 8})
    assert unsupported.cache_read_tokens is None
    assert unsupported.cache_miss_tokens is None
    assert unsupported.cache_write_tokens is None
    assert unsupported.cache_miss_source == "unknown"


def test_audit_model_is_not_an_sse_event():
    from app.runtime_models import AgentModelCacheAudit, AgentModelInputAudit, AgentRunEvent

    assert AgentModelInputAudit.__tablename__ == "agent_model_input_audits"
    assert AgentModelCacheAudit.__tablename__ == "agent_model_cache_audits"
    assert AgentModelInputAudit.__tablename__ != AgentRunEvent.__tablename__


def test_provider_tool_schema_order_and_hash_are_registration_independent():
    from app.services.agent_harness import model_driver
    from app.services.chat.tools import MainTool

    async def execute(_args):
        return "ok"

    tools = [
        MainTool(name="zeta", description="z", parameters={}, execute=execute),
        MainTool(name="alpha", description="a", parameters={}, execute=execute),
    ]
    first = model_driver._stable_payload_tools(tools)
    second = model_driver._stable_payload_tools(list(reversed(tools)))
    assert [item["function"]["name"] for item in first] == ["alpha", "zeta"]
    assert first == second
    assert audit._canonical_hash(first) == audit._canonical_hash(second)


@pytest.mark.asyncio
async def test_cache_usage_is_linked_to_exact_request_handle(monkeypatch):
    captured = {}

    class Result:
        def scalar_one_or_none(self):
            return None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, _statement):
            return Result()

        def add(self, row):
            captured["row"] = row

        async def commit(self):
            captured["committed"] = True

    handle = audit.ModelRequestAuditHandle(
        request_id="req-1",
        run_id="run-1",
        thread_id="thread-1",
        request_sequence=11,
        model="deepseek-v4",
        context_epoch=2,
        epoch_reason="tool_schema_changed",
        base_prompt_hash="b" * 64,
        tool_schema_hash="t" * 64,
        state_snapshot_hash="s" * 64,
        prefix_diagnostics={"lcp_item_count": 12},
    )
    monkeypatch.setattr(audit, "runtime_session", lambda: Session)
    persisted = await audit.record_model_cache_usage(
        handle=handle,
        usage={
            "input_tokens": 100,
            "output_tokens": 5,
            "input_tokens_details": {"cached_tokens": 90},
        },
        transport="responses",
    )
    row = captured["row"]
    assert persisted is True and captured["committed"] is True
    assert row.request_id == "req-1"
    assert row.context_epoch == 2
    assert row.cache_read_tokens == 90
    assert row.cache_miss_tokens == 10
    assert row.prefix_diagnostics == {"lcp_item_count": 12}


@pytest.mark.asyncio
async def test_main_loop_audits_exact_streaming_provider_payload(monkeypatch):
    from app.services.agent_harness import model_driver

    logical_calls, captured, terminals, _ = _install_v4_audit_spy(
        monkeypatch, model_driver
    )

    class Response:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_lines(self):
            yield "data: " + json.dumps({
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "id": "msg-1",
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "ok"}],
                },
            })
            yield "data: " + json.dumps({
                "type": "response.completed",
                "response": {
                    "id": "resp-1",
                    "status": "completed",
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 2,
                        "input_tokens_details": {"cached_tokens": 10},
                    },
                },
            })
            yield "data: [DONE]"

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)

    events = [event async for event in model_driver.drive_model(
        model="gpt-5.5",
        api_key="k",
        user_input="hello",
        tools=[],
        gateway={"run_id": "run-audit", "thread_id": "thread-audit", "user_id": "u1"},
    )]

    assert any(event.get("type") == "final" for event in events)
    assert len(logical_calls) == 1
    assert len(captured) == 1
    assert captured[0]["wire_payload"]["reasoning"] == {
        "effort": "medium", "summary": "detailed"
    }
    assert captured[0]["shadow_payload"]["reasoning"] == {
        "effort": "medium",
        "summary": "detailed",
    }
    assert "input" in captured[0]["wire_payload"]
    assert "messages" not in captured[0]["wire_payload"]
    assert audit._canonical_hash(audit._sanitize(captured[0]["wire_payload"])) == audit._canonical_hash(
        audit._sanitize(captured[0]["shadow_payload"])
    )
    assert len(terminals) == 1
    assert terminals[0]["handle"].request_id == "request-1"
    assert terminals[0]["usage"]["input_tokens_details"]["cached_tokens"] == 10


@pytest.mark.asyncio
async def test_stream_retry_links_usage_to_the_successful_attempt_handle(monkeypatch):
    import httpx

    from app.services.agent_harness import model_driver

    _, request_records, terminals, _ = _install_v4_audit_spy(
        monkeypatch, model_driver
    )

    class Response:
        status_code = 200

        def __init__(self, *, enter_error=None):
            self.enter_error = enter_error

        async def __aenter__(self):
            if self.enter_error is not None:
                raise self.enter_error
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_lines(self):
            yield "data: " + json.dumps({
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "id": "msg-retry",
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "ok"}],
                },
            })
            yield "data: " + json.dumps({
                "type": "response.completed",
                "response": {
                    "id": "resp-retry",
                    "status": "completed",
                    "usage": {
                        "input_tokens": 30,
                        "output_tokens": 2,
                        "input_tokens_details": {"cached_tokens": 25},
                    },
                },
            })
            yield "data: [DONE]"

    request = httpx.Request("POST", "https://example.test/responses")

    class Client:
        attempts = [
            Response(enter_error=httpx.ConnectError("offline", request=request)),
            Response(),
        ]

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return type(self).attempts.pop(0)

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    monkeypatch.setattr(model_driver, "_model_stream_retry_delay", lambda _attempt: 0.0)
    monkeypatch.setattr(model_driver.settings, "MODEL_STREAM_MAX_RETRIES", 1)

    events = [event async for event in model_driver.drive_model(
        model="gpt-5.5",
        api_key="k",
        user_input="hello",
        tools=[],
        gateway={
            "run_id": "run-retry-audit",
            "thread_id": "thread-retry-audit",
            "user_id": "u1",
        },
    )]

    assert any(event.get("type") == "final" for event in events)
    assert [row["attempt_kind"] for row in request_records] == ["initial", "network_retry"]
    assert [row["retry_of_attempt_id"] for row in request_records] == ["", "attempt-1"]
    assert [row["terminal_status"] for row in terminals] == ["interrupted", "completed"]
    assert terminals[-1]["handle"].request_id == "request-2"
    assert terminals[-1]["usage"]["input_tokens_details"]["cached_tokens"] == 25


@pytest.mark.asyncio
async def test_two_round_driver_payload_preserves_the_provider_input_prefix(monkeypatch):
    from app.services.agent_harness import AgentMode, RunPhase, RunSnapshot
    from app.services.agent_harness import model_driver, plan_store, run_store
    from app.services.agent_harness.context import (
        ContextCompiler,
        ContextFacts,
        ContextProjectionLedger,
        canonical_hash,
        display_history_hash,
    )
    from app.services.chat.tools import MainTool
    from app.services.tasks import task_run_service

    def sse(event):
        return "data: " + json.dumps(event, ensure_ascii=False)

    first_round = [
        sse({
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "fc-prefix",
                "type": "function_call",
                "call_id": "call-prefix",
                "name": "lookup",
                "arguments": "{}",
            },
        }),
        sse({
            "type": "response.completed",
            "response": {
                "id": "resp-prefix-1",
                "status": "completed",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 3,
                    "input_tokens_details": {"cached_tokens": 0},
                },
            },
        }),
        "data: [DONE]",
    ]
    second_round = [
        sse({
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "msg-prefix-final",
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "done"}],
            },
        }),
        sse({
            "type": "response.completed",
            "response": {
                "id": "resp-prefix-2",
                "status": "completed",
                "usage": {
                    "input_tokens": 130,
                    "output_tokens": 2,
                    "input_tokens_details": {"cached_tokens": 100},
                },
            },
        }),
        "data: [DONE]",
    ]

    class Response:
        status_code = 200

        def __init__(self, lines):
            self.lines = lines

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_lines(self):
            for line in self.lines:
                yield line

    class Client:
        responses = [first_round, second_round]
        payloads = []

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, _method, _url, **kwargs):
            type(self).payloads.append(kwargs["json"])
            return Response(type(self).responses.pop(0))

    run_snapshot = RunSnapshot(
        run_id="run-prefix",
        thread_id="thread-prefix",
        user_id="u1",
        agent_mode=AgentMode.STANDARD,
        phase=RunPhase.EXECUTING,
        state_version=1,
        goal_revision=0,
        plan_version=0,
        event_cursor=0,
    )

    async def get_run(_run_id):
        return run_snapshot

    async def get_plan(_run_id):
        return None

    async def record_observations(*_args, **_kwargs):
        return 1

    projection_save_revisions = []

    async def lookup(_args):
        return "lookup-ok"

    gateway = {
        "run_id": "run-prefix",
        "thread_id": "thread-prefix",
        "user_id": "u1",
    }
    lookup_tool = MainTool(
        name="lookup",
        description="lookup",
        parameters={},
        execute=lookup,
        internal=True,
    )
    provider_tools = model_driver.tools_to_responses(
        model_driver._stable_payload_tools([
            lookup_tool,
            model_driver._make_fetch_tool(gateway, {}),
        ])
    )
    prior_display_history = [
        {"role": "user", "content": "before"},
        {"role": "assistant", "content": "public previous answer"},
    ]
    prior_provider_source = [
        {"role": "user", "content": "before"},
        {
            "role": "assistant",
            "content": "provider previous answer",
            "_responses_output_items": [{
                "type": "reasoning",
                "encrypted_content": "opaque-prior-reasoning",
            }],
        },
    ]
    prior_ledger = ContextProjectionLedger()
    prior_ledger.project(
        source_messages=prior_provider_source,
        snapshot=ContextCompiler().compile(ContextFacts(run=run_snapshot)),
        base_prompt_hash=canonical_hash({"system_prompt": ""}),
        tool_schema_hash=canonical_hash(provider_tools),
        transport="responses",
    )
    persisted_projection = prior_ledger.to_persisted_state(
        thread_id="thread-prefix",
        model="gpt-5.5",
        mode="shadow",
        display_history=prior_display_history,
    )

    async def load_projection(**_kwargs):
        return persisted_projection

    async def enabled_rollout(*_args, **_kwargs):
        return "on"

    async def save_projection(state, **_kwargs):
        projection_save_revisions.append(state.storage_revision)
        return True

    canary_markers = []

    async def mark_canary(**kwargs):
        canary_markers.append(kwargs)
        return True

    logical_calls, request_records, terminals, _ = _install_v4_audit_spy(
        monkeypatch, model_driver
    )

    monkeypatch.setattr(model_driver.httpx, "AsyncClient", Client)
    monkeypatch.setattr(run_store, "get_run_snapshot", get_run)
    monkeypatch.setattr(plan_store, "get_plan_snapshot", get_plan)
    monkeypatch.setattr(task_run_service, "record_tool_observations", record_observations)
    monkeypatch.setattr(
        model_driver.thread_projection_store,
        "load",
        load_projection,
    )
    monkeypatch.setattr(
        model_driver.thread_projection_store,
        "effective_rollout_mode",
        enabled_rollout,
    )
    monkeypatch.setattr(
        model_driver.thread_projection_store,
        "save",
        save_projection,
    )
    monkeypatch.setattr(
        model_driver.thread_projection_store,
        "mark_canary_candidate",
        mark_canary,
    )

    events = [event async for event in model_driver.drive_model(
        model="gpt-5.5",
        api_key="k",
        user_input="check",
        history=prior_display_history,
        display_history=prior_display_history,
        display_user_message={
            "role": "user",
            "content": "check",
            "attachments_json": json.dumps([{
                "filename": "evidence.pdf",
                "kind": "text",
                "status": "ok",
                "file_id": "file-evidence",
            }]),
        },
        world_state={"conversation_summary": "prior summary"},
        tools=[lookup_tool],
        gateway=gateway,
    )]

    assert any(event.get("type") == "final" for event in events)
    assert len(Client.payloads) == 2
    first_input = Client.payloads[0]["input"]
    second_input = Client.payloads[1]["input"]
    assert second_input[:len(first_input)] == first_input
    assert any(
        item.get("type") == "reasoning"
        and item.get("encrypted_content") == "opaque-prior-reasoning"
        for item in first_input
    )
    assert "public previous answer" not in json.dumps(first_input, ensure_ascii=False)
    # One crash-safe marker admits the first request; every later tool round stays on that
    # canonical Provider cursor without re-marking or falling back to the display transcript.
    assert len(canary_markers) == 1
    assert any(
        '"conversation_summary":"prior summary"' in str(item.get("content") or "")
        for item in first_input
        if isinstance(item, dict)
    )
    assert Client.payloads[0]["tools"] == Client.payloads[1]["tools"]
    assert len(logical_calls) == 2
    assert len(request_records) == 2
    assert [row["handle"].request_id for row in terminals] == ["request-1", "request-2"]
    assert logical_calls[0]["context_metadata"]["context_epoch"] == 0
    assert logical_calls[1]["context_metadata"]["context_epoch"] == 0
    assert (
        logical_calls[0]["context_metadata"]["tool_schema_hash"]
        == logical_calls[1]["context_metadata"]["tool_schema_hash"]
    )
    final = next(event for event in events if event.get("type") == "final")
    # The durable Canary admission marker is revision 1; each subsequent Provider/tool
    # checkpoint advances from that acquired CAS token.
    assert projection_save_revisions == [1, 2, 3]
    assert final["_projection_commit"]["state"].storage_revision == 4
    assert final["_projection_commit"]["state"].display_history_hash == (
        display_history_hash([
            *prior_display_history,
            {
                "role": "user",
                "content": "check",
                "attachments_json": json.dumps([{
                    "filename": "evidence.pdf",
                    "kind": "text",
                    "status": "ok",
                    "file_id": "file-evidence",
                }]),
            },
        ])
    )
