import asyncio
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessageChunk

from app.services.chat import plain_turn


class _Channel:
    def message_reasoning_delta(self, text):
        return {"type": "reasoning_delta", "text": text}

    def message_reasoning_completed(self, text, seconds):
        return {"type": "reasoning_completed", "text": text, "seconds": seconds}

    def message_delta(self, text):
        return {"type": "delta", "text": text}

    def context_usage(self, *args, **kwargs):
        return {"type": "usage", "args": args, "kwargs": kwargs}

    def context_compacted(self, text):
        return {"type": "compacted", "text": text}

    def model_connection(self, status, transport="", **kwargs):
        return {
            "type": "model_connection",
            "status": status,
            "transport": transport,
            **kwargs,
        }


def _env(model: str, key: str = "key") -> SimpleNamespace:
    return SimpleNamespace(
        session=object(),
        thread=object(),
        channel=_Channel(),
        run_id="run-plain",
        thread_id="thread-plain",
        user_id="user-plain",
        user_context={},
        token="access-token",
        newapi_key=key,
        resolved_model=model,
        message="question",
        attachments=[],
        regenerate=False,
        is_first_turn=False,
        prompt_rows=[],
        prep=SimpleNamespace(
            agents=[],
            trusted_skills=[],
            memory_block="",
            skill_catalog_block="",
        ),
        skill_catalog_block="",
        summary_block="",
        knowledge_ids=[],
        selected_knowledge=[],
        model_input_content="question",
        history_rows=[],
        raw_est_tokens=10,
        ctx_window=10_000,
        summary=None,
        kb_pre_context="",
        fallback_plain=False,
        intentional_pure_qa=True,
        plain={},
        spawn_partial_persist=lambda *_args, **_kwargs: None,
    )


async def _patch_persistence(monkeypatch):
    async def persist(*_args, **_kwargs):
        return SimpleNamespace(id="assistant-1")

    async def get_run_state(*_args, **_kwargs):
        return None

    async def patch_run_state(_run_id, patch):
        return {"state": dict(patch)}

    async def begin_logical(**_kwargs):
        return SimpleNamespace(logical_call_id="logical-plain")

    async def begin_attempt(_logical, **_kwargs):
        return SimpleNamespace(attempt_id="attempt-plain")

    async def finish_audit(*_args, **_kwargs):
        return True

    monkeypatch.setattr(plain_turn.turn_finalizer, "persist_assistant_turn", persist)
    monkeypatch.setattr(plain_turn, "record_usage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plain_turn.run_store, "get_run_state", get_run_state)
    monkeypatch.setattr(plain_turn.run_store, "patch_run_state", patch_run_state)
    monkeypatch.setattr(plain_turn.model_usage_audit, "begin_logical_call", begin_logical)
    monkeypatch.setattr(plain_turn.model_usage_audit, "begin_attempt", begin_attempt)
    monkeypatch.setattr(plain_turn.model_usage_audit, "finish_attempt", finish_audit)
    monkeypatch.setattr(plain_turn.model_usage_audit, "finish_logical_call", finish_audit)


def test_plain_projection_preserves_native_responses_history_and_audit_input():
    provider_messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "task"},
        {
            "role": "assistant",
            "content": "answer",
            "_responses_output_items": [
                {"type": "reasoning", "encrypted_content": "ciphertext"},
                {
                    "type": "message",
                    "id": "msg-1",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "answer"}],
                },
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "lookup",
                    "arguments": "{}",
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "result"},
    ]

    converted = plain_turn._provider_messages_to_langchain(provider_messages)
    assistant = converted[2]
    assert assistant.content[0]["encrypted_content"] == "ciphertext"
    assert assistant.content[1]["id"] == "msg-1"
    assert assistant.content[2]["type"] == "function_call"
    assert converted[3].tool_call_id == "call-1"

    payload = plain_turn._plain_attempt_payload(
        provider_messages,
        model="model-1",
        use_responses_transport=True,
    )
    assert payload["input"][2]["encrypted_content"] == "ciphertext"
    assert payload["input"][3]["type"] == "message"
    assert "id" not in payload["input"][3]
    assert payload["input"][4]["call_id"] == "call-1"
    assert payload["input"][5] == {
        "type": "function_call_output",
        "call_id": "call-1",
        "output": "result",
    }
    assert payload["store"] is False
    assert payload["include"] == ["reasoning.encrypted_content"]
    assert "stream_options" not in payload


def test_plain_responses_conversion_uses_the_tool_path_developer_role():
    provider_messages = [
        {"role": "system", "content": "stable rules"},
        {"role": "user", "content": "hello"},
    ]
    converted = plain_turn._provider_messages_to_langchain(
        provider_messages,
        use_responses_transport=True,
    )

    assert converted[0].additional_kwargs["__openai_role__"] == "developer"
    payload = plain_turn._plain_attempt_payload(
        provider_messages,
        model="gpt-5.5",
        use_responses_transport=True,
    )
    assert payload["input"][0]["role"] == "developer"


def test_plain_responses_terminal_items_preserve_encrypted_reasoning_and_text():
    message = AIMessageChunk(content=[
        {
            "type": "reasoning",
            "id": "rs-1",
            "encrypted_content": "ciphertext",
            "summary": [
                {"type": "summary_text", "text": "brief", "index": 0},
            ],
            "index": 0,
        },
        {
            "type": "text",
            "text": "answer",
            "id": "msg-1",
            "index": 1,
        },
    ])

    items = plain_turn._responses_output_items_from_message(message)

    assert items == [
        {
            "type": "reasoning",
            "id": "rs-1",
            "encrypted_content": "ciphertext",
            "summary": [{"type": "summary_text", "text": "brief"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "id": "msg-1",
            "content": [
                {"type": "output_text", "text": "answer", "annotations": []},
            ],
        },
    ]


@pytest.mark.asyncio
async def test_plain_turn_probes_responses_then_falls_back_to_chat_stream(
    monkeypatch,
) -> None:
    await _patch_persistence(monkeypatch)
    canary_errors = []

    async def record_canary_error(_prepared, *, run_id):
        canary_errors.append(run_id)
        return True

    monkeypatch.setattr(
        plain_turn,
        "record_projection_canary_error",
        record_canary_error,
    )
    monkeypatch.setattr(
        plain_turn.agent_service,
        "model_transport_aliases",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        plain_turn.agent_service,
        "model_responses_capability",
        lambda *_args, **_kwargs: None,
    )
    learned = []
    monkeypatch.setattr(
        plain_turn.agent_service,
        "remember_model_responses_capability",
        lambda model, key, supported: learned.append((model, key, supported)),
    )

    class UnsupportedResponses(Exception):
        status_code = 404

    class FakeChatOpenAI:
        options = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            type(self).options.append(kwargs)

        async def astream(self, _messages):
            if self.kwargs.get("use_responses_api"):
                raise UnsupportedResponses("unknown endpoint")
            yield SimpleNamespace(
                content="chat answer",
                content_blocks=[],
                usage_metadata=None,
            )

    monkeypatch.setattr(plain_turn, "ChatOpenAI", FakeChatOpenAI)
    env = _env("plain-probe-model", "plain-probe-key")
    events = [event async for event in plain_turn.stream_llm_round(env)]

    assert FakeChatOpenAI.options[0]["use_responses_api"] is True
    assert "use_responses_api" not in FakeChatOpenAI.options[1]
    assert [event["status"] for event in events if event["type"] == "model_connection"] == [
        "recovering",
        "recovered",
    ]
    assert [event["text"] for event in events if event["type"] == "delta"] == [
        "chat answer"
    ]
    # A negative runtime observation is channel/adaptor-specific; the current Run is locked to
    # Chat, but the process-wide model/key cache is not permanently poisoned.
    assert learned == []
    assert env.plain["full_response"] == "chat answer"
    assert canary_errors == ["run-plain"]


@pytest.mark.asyncio
async def test_plain_turn_deepseek_exact_conversion_500_falls_back_once(
    monkeypatch,
) -> None:
    await _patch_persistence(monkeypatch)
    monkeypatch.setattr(
        plain_turn.agent_service,
        "model_transport_aliases",
        lambda *_args, **_kwargs: ("DeepSeek V4 Flash",),
    )
    monkeypatch.setattr(
        plain_turn.agent_service,
        "model_responses_capability",
        lambda *_args, **_kwargs: None,
    )
    learned = []
    monkeypatch.setattr(
        plain_turn.agent_service,
        "remember_model_responses_capability",
        lambda model, key, supported: learned.append((model, key, supported)),
    )
    locks = []

    async def patch_run_state(_run_id, patch):
        locks.append(patch["model_transport"])
        return {"state": dict(patch)}

    monkeypatch.setattr(plain_turn.run_store, "patch_run_state", patch_run_state)

    class ConversionUnsupported(Exception):
        status_code = 500
        body = {
            "error": {
                "code": "convert_request_failed",
                "message": "not implemented",
            }
        }

    class FakeChatOpenAI:
        options = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            type(self).options.append(kwargs)

        async def astream(self, _messages):
            if self.kwargs.get("use_responses_api"):
                raise ConversionUnsupported("structured conversion failure")
            yield SimpleNamespace(
                content="",
                content_blocks=[{"type": "reasoning", "reasoning": "先核对。"}],
                usage_metadata=None,
            )
            yield SimpleNamespace(
                content="chat answer",
                content_blocks=[],
                usage_metadata=None,
            )

    monkeypatch.setattr(plain_turn, "ChatOpenAI", FakeChatOpenAI)
    env = _env("deepseek-v4-plain-500", "deepseek-plain-500-key")
    events = [event async for event in plain_turn.stream_llm_round(env)]

    assert len(FakeChatOpenAI.options) == 2
    assert FakeChatOpenAI.options[0]["use_responses_api"] is True
    assert "use_responses_api" not in FakeChatOpenAI.options[1]
    assert [event["transport"] for event in events if event["type"] == "model_connection"] == [
        "chat_completions_fallback",
        "chat_completions_fallback",
    ]
    assert [event["text"] for event in events if event["type"] == "reasoning_delta"] == [
        "先核对。"
    ]
    assert env.plain["full_response"] == "chat answer"
    assert locks[-1]["protocol"] == "chat_completions"
    assert locks[-1]["confirmed"] is True
    assert learned == []


@pytest.mark.asyncio
async def test_plain_turn_generic_responses_500_retries_without_chat_fallback(
    monkeypatch,
) -> None:
    await _patch_persistence(monkeypatch)
    monkeypatch.setattr(
        plain_turn.agent_service,
        "model_transport_aliases",
        lambda *_args, **_kwargs: ("DeepSeek V4",),
    )
    monkeypatch.setattr(
        plain_turn.agent_service,
        "model_responses_capability",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(plain_turn, "_model_retry_delay", lambda _attempt: 0.0)

    class GenericServerError(Exception):
        status_code = 500
        body = {"error": {"code": "server_error", "message": "busy"}}

    class FakeChatOpenAI:
        calls = 0
        options = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            type(self).options.append(kwargs)

        async def astream(self, _messages):
            type(self).calls += 1
            if type(self).calls == 1:
                raise GenericServerError("ordinary service failure")
            yield SimpleNamespace(
                content="responses recovered",
                content_blocks=[],
                usage_metadata=None,
            )

    monkeypatch.setattr(plain_turn, "ChatOpenAI", FakeChatOpenAI)
    env = _env("deepseek-v4-plain-generic-500")
    events = [event async for event in plain_turn.stream_llm_round(env)]

    assert FakeChatOpenAI.calls == 2
    assert len(FakeChatOpenAI.options) == 1
    assert FakeChatOpenAI.options[0]["use_responses_api"] is True
    assert any(
        event.get("transport") == "stream_retry"
        and event.get("status") == "recovering"
        for event in events
    )
    assert not any(
        event.get("transport") == "chat_completions_fallback"
        for event in events
    )
    assert env.plain["full_response"] == "responses recovered"


@pytest.mark.asyncio
async def test_plain_turn_sensitive_words_500_stops_after_one_attempt(
    monkeypatch,
) -> None:
    await _patch_persistence(monkeypatch)
    monkeypatch.setattr(
        plain_turn.agent_service,
        "model_transport_aliases",
        lambda *_args, **_kwargs: ("DeepSeek V4",),
    )
    monkeypatch.setattr(
        plain_turn.agent_service,
        "model_responses_capability",
        lambda *_args, **_kwargs: True,
    )

    class SensitiveWordsRejected(Exception):
        status_code = 500
        body = {
            "error": {
                "code": "sensitive_words_detected",
                "message": "sensitive_words_detected (request id: private)",
            }
        }

    class FakeChatOpenAI:
        calls = 0

        def __init__(self, **_kwargs):
            pass

        async def astream(self, _messages):
            type(self).calls += 1
            raise SensitiveWordsRejected("private provider response")
            yield  # pragma: no cover - keep this method an async generator

    monkeypatch.setattr(plain_turn, "ChatOpenAI", FakeChatOpenAI)
    env = _env("deepseek-v4-plain-policy")
    events = []
    with pytest.raises(plain_turn.ModelProviderPolicyRejected) as failure:
        async for event in plain_turn.stream_llm_round(env):
            events.append(event)

    assert FakeChatOpenAI.calls == 1
    assert events == []
    assert "敏感词策略" in failure.value.public_message
    assert "private" not in failure.value.public_message


@pytest.mark.asyncio
async def test_plain_turn_recovers_after_five_network_retries(monkeypatch) -> None:
    await _patch_persistence(monkeypatch)
    monkeypatch.setattr(
        plain_turn.agent_service,
        "model_transport_aliases",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        plain_turn.agent_service,
        "model_responses_capability",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(plain_turn, "_model_retry_delay", lambda _attempt: 0.0)
    request = httpx.Request("POST", "https://example.test/chat/completions")

    class FakeChatOpenAI:
        calls = 0

        def __init__(self, **_kwargs):
            pass

        async def astream(self, _messages):
            type(self).calls += 1
            if type(self).calls <= 5:
                raise httpx.ConnectError("offline", request=request)
            yield SimpleNamespace(content="recovered", content_blocks=[], usage_metadata=None)

    monkeypatch.setattr(plain_turn, "ChatOpenAI", FakeChatOpenAI)
    env = _env("plain-chat-model")
    events = [event async for event in plain_turn.stream_llm_round(env)]

    connection = [event for event in events if event["type"] == "model_connection"]
    assert [event["attempt"] for event in connection if event["status"] == "recovering"] == [
        1, 2, 3, 4, 5,
    ]
    assert connection[-1]["status"] == "recovered"
    assert FakeChatOpenAI.calls == 6
    assert [event["text"] for event in events if event["type"] == "delta"] == [
        "recovered"
    ]
    assert env.plain["full_response"] == "recovered"


@pytest.mark.asyncio
async def test_plain_turn_stops_after_five_failed_reconnects(monkeypatch) -> None:
    await _patch_persistence(monkeypatch)
    monkeypatch.setattr(
        plain_turn.agent_service,
        "model_transport_aliases",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        plain_turn.agent_service,
        "model_responses_capability",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(plain_turn, "_model_retry_delay", lambda _attempt: 0.0)
    request = httpx.Request("POST", "https://example.test/chat/completions")

    class FakeChatOpenAI:
        calls = 0

        def __init__(self, **_kwargs):
            pass

        async def astream(self, _messages):
            type(self).calls += 1
            raise httpx.ReadTimeout("timeout", request=request)
            yield  # pragma: no cover - keep this method an async generator

    monkeypatch.setattr(plain_turn, "ChatOpenAI", FakeChatOpenAI)
    env = _env("plain-chat-exhausted")
    events = []
    with pytest.raises(RuntimeError, match="连续重试 5 次"):
        async for event in plain_turn.stream_llm_round(env):
            events.append(event)

    assert FakeChatOpenAI.calls == 6
    assert [event["attempt"] for event in events if event["status"] == "recovering"] == [
        1, 2, 3, 4, 5,
    ]
    assert events[-1]["status"] == "failed"


@pytest.mark.asyncio
async def test_plain_turn_audits_each_sdk_stream_attempt_and_parent_purpose(
    monkeypatch,
) -> None:
    await _patch_persistence(monkeypatch)
    monkeypatch.setattr(
        plain_turn.agent_service,
        "model_transport_aliases",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        plain_turn.agent_service,
        "model_responses_capability",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(plain_turn, "_model_retry_delay", lambda _attempt: 0.0)
    audit: dict[str, list] = {
        "logical": [],
        "attempt": [],
        "attempt_finish": [],
        "logical_finish": [],
    }

    async def begin_logical(**kwargs):
        audit["logical"].append(kwargs)
        return SimpleNamespace(logical_call_id=f"logical-{len(audit['logical'])}")

    async def begin_attempt(handle, **kwargs):
        audit["attempt"].append((handle, kwargs))
        return SimpleNamespace(attempt_id=f"attempt-{len(audit['attempt'])}")

    async def finish_attempt(handle, **kwargs):
        audit["attempt_finish"].append((handle, kwargs))
        return True

    async def finish_logical(handle, **kwargs):
        audit["logical_finish"].append((handle, kwargs))
        return True

    monkeypatch.setattr(plain_turn.model_usage_audit, "begin_logical_call", begin_logical)
    monkeypatch.setattr(plain_turn.model_usage_audit, "begin_attempt", begin_attempt)
    monkeypatch.setattr(plain_turn.model_usage_audit, "finish_attempt", finish_attempt)
    monkeypatch.setattr(plain_turn.model_usage_audit, "finish_logical_call", finish_logical)

    request = httpx.Request("POST", "https://example.test/chat/completions")

    class FakeChatOpenAI:
        calls = 0

        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0

        async def astream(self, _messages):
            type(self).calls += 1
            if type(self).calls == 1:
                raise httpx.ReadTimeout("retry me", request=request)
            yield SimpleNamespace(
                id="response-plain",
                response_metadata={},
                content="answer",
                content_blocks=[],
                usage_metadata={
                    "input_tokens": 200,
                    "output_tokens": 8,
                    "input_token_details": {"cache_read": 160},
                },
            )

    monkeypatch.setattr(plain_turn, "ChatOpenAI", FakeChatOpenAI)
    env = _env("plain-audit-model")
    env.model_call_purpose = "parent_summary"
    env.model_call_purpose_detail = "delegation_result"

    events = [event async for event in plain_turn.stream_llm_round(env)]

    assert [event["text"] for event in events if event["type"] == "delta"] == ["answer"]
    assert len(audit["logical"]) == 1
    assert audit["logical"][0]["purpose"] == "parent_summary"
    assert audit["logical"][0]["purpose_detail"] == "delegation_result"
    assert audit["logical"][0]["transport"] == "chat_completions"
    assert len(audit["attempt"]) == 2
    assert audit["attempt"][0][1]["attempt_kind"] == "initial"
    assert audit["attempt"][1][1]["attempt_kind"] == "stream_retry"
    assert audit["attempt"][1][1]["retry_of_attempt_id"] == "attempt-1"
    assert all(item[1]["legacy_compatible"] is False for item in audit["attempt"])
    audited_messages = audit["attempt"][0][1]["wire_payload"]["messages"]
    assert audited_messages[0]["role"] == "system"
    assert "<environment_context>" not in audited_messages[0]["content"]
    assert audited_messages[-1]["role"] == "system"
    assert "<environment_context>" in audited_messages[-1]["content"]
    assert audit["attempt_finish"][0][1]["terminal_status"] == "failed"
    assert audit["attempt_finish"][0][1]["usage"] is None
    assert audit["attempt_finish"][1][1]["terminal_status"] == "completed"
    assert audit["attempt_finish"][1][1]["usage"]["input_tokens"] == 200
    assert audit["logical_finish"][-1][1]["terminal_status"] == "completed"


@pytest.mark.asyncio
async def test_plain_turn_cancellation_finishes_the_inflight_attempt(monkeypatch) -> None:
    await _patch_persistence(monkeypatch)
    monkeypatch.setattr(
        plain_turn.agent_service,
        "model_transport_aliases",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        plain_turn.agent_service,
        "model_responses_capability",
        lambda *_args, **_kwargs: False,
    )
    attempt_finishes: list[dict] = []
    logical_finishes: list[dict] = []

    async def begin_logical(**_kwargs):
        return SimpleNamespace(logical_call_id="logical-cancel")

    async def begin_attempt(_handle, **_kwargs):
        return SimpleNamespace(attempt_id="attempt-cancel")

    async def finish_attempt(_handle, **kwargs):
        attempt_finishes.append(kwargs)
        return True

    async def finish_logical(_handle, **kwargs):
        logical_finishes.append(kwargs)
        return True

    monkeypatch.setattr(plain_turn.model_usage_audit, "begin_logical_call", begin_logical)
    monkeypatch.setattr(plain_turn.model_usage_audit, "begin_attempt", begin_attempt)
    monkeypatch.setattr(plain_turn.model_usage_audit, "finish_attempt", finish_attempt)
    monkeypatch.setattr(plain_turn.model_usage_audit, "finish_logical_call", finish_logical)

    class FakeChatOpenAI:
        def __init__(self, **_kwargs):
            pass

        async def astream(self, _messages):
            yield SimpleNamespace(
                content="partial",
                content_blocks=[],
                usage_metadata=None,
            )
            raise asyncio.CancelledError()

    monkeypatch.setattr(plain_turn, "ChatOpenAI", FakeChatOpenAI)
    env = _env("plain-cancel-model")

    with pytest.raises(asyncio.CancelledError):
        async for _event in plain_turn.stream_llm_round(env):
            pass

    assert attempt_finishes[-1]["terminal_status"] == "cancelled"
    assert attempt_finishes[-1]["provider_event_seen"] is True
    assert attempt_finishes[-1]["terminal_seen"] is False
    assert attempt_finishes[-1]["committed"] is False
    assert logical_finishes[-1]["terminal_status"] == "cancelled"
