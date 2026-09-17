import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services.agent_harness import model_driver


def _sse(delta: dict) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


class _BrokenStream:
    async def __aenter__(self):
        raise RuntimeError("incomplete chunked read")

    async def __aexit__(self, *_args):
        return False


class _SlowFallbackClient:
    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def stream(self, *_args, **_kwargs):
        return _BrokenStream()

    async def post(self, *_args, **_kwargs):
        await asyncio.sleep(60)
        raise AssertionError("绝对墙钟失效，慢响应不应自然返回")


class _FallbackResponse:
    status_code = 200
    text = ""

    def json(self):
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "收尾已恢复。"},
            }],
            "usage": {"prompt_tokens": 8, "completion_tokens": 4},
        }


class _RetryingFallbackClient:
    post_calls = 0

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def stream(self, *_args, **_kwargs):
        return _BrokenStream()

    async def post(self, *_args, **_kwargs):
        type(self).post_calls += 1
        if type(self).post_calls == 1:
            raise model_driver.httpx.RemoteProtocolError("incomplete chunked read")
        return _FallbackResponse()


class _ConnectErrorFallbackClient(_RetryingFallbackClient):
    async def post(self, *_args, **_kwargs):
        type(self).post_calls += 1
        request = model_driver.httpx.Request("POST", "https://provider.invalid/v1/responses")
        raise model_driver.httpx.ConnectError("connection refused", request=request)


class _GatewayTimeoutResponse:
    status_code = 504
    text = "gateway timeout"


class _RetryingGatewayStatusClient(_RetryingFallbackClient):
    async def post(self, *_args, **_kwargs):
        type(self).post_calls += 1
        if type(self).post_calls == 1:
            return _GatewayTimeoutResponse()
        return _FallbackResponse()


class _SensitiveWordsResponse:
    status_code = 500
    text = json.dumps({
        "error": {
            "code": "sensitive_words_detected",
            "message": "sensitive_words_detected (request id: private)",
        }
    })

    def json(self):
        return json.loads(self.text)


class _SensitiveWordsFallbackClient(_RetryingFallbackClient):
    async def post(self, *_args, **_kwargs):
        type(self).post_calls += 1
        return _SensitiveWordsResponse()


class _PartialThenBrokenStream:
    status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def aiter_lines(self):
        yield _sse({"content": "已经向用户输出的正文。"})
        raise model_driver.httpx.RemoteProtocolError("peer closed connection")


class _NoFallbackAfterPublicTextClient:
    post_calls = 0

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def stream(self, *_args, **_kwargs):
        return _PartialThenBrokenStream()

    async def post(self, *_args, **_kwargs):
        type(self).post_calls += 1
        raise AssertionError("公开正文已输出后不得重跑模型请求")


class _ReasoningThenBrokenStream(_PartialThenBrokenStream):
    async def aiter_lines(self):
        yield _sse({"reasoning_content": "provider event without public text"})
        raise model_driver.httpx.RemoteProtocolError("peer closed connection")


class _NoReplayAfterProviderEventClient(_NoFallbackAfterPublicTextClient):
    def stream(self, *_args, **_kwargs):
        return _ReasoningThenBrokenStream()


@pytest.mark.asyncio
async def test_non_stream_fallback_has_absolute_wall_timeout(monkeypatch):
    monkeypatch.setattr(model_driver.httpx, "AsyncClient", _SlowFallbackClient)
    monkeypatch.setattr(
        model_driver.settings, "MODEL_FALLBACK_WALL_TIMEOUT_SECONDS", 0.01
    )

    with pytest.raises(RuntimeError, match="非流式降级超过 0.01 秒"):
        async for _event in model_driver.drive_model(
            model="deepseek-v4-flash",
            api_key="test-key",
            user_input="生成 PPT",
            tools=[],
        ):
            pass


@pytest.mark.asyncio
async def test_non_stream_fallback_retries_one_transport_interruption(monkeypatch):
    logical_calls = []
    attempts = []
    attempt_terminals = []
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
        attempt_terminals.append({"handle": handle, **kwargs})
        return True

    async def finish_logical_call(handle, **kwargs):
        logical_terminals.append({"handle": handle, **kwargs})
        return True

    async def load_projection(**_kwargs):
        return None

    async def save_projection(_state, **_kwargs):
        return False

    _RetryingFallbackClient.post_calls = 0
    monkeypatch.setattr(model_driver.httpx, "AsyncClient", _RetryingFallbackClient)
    monkeypatch.setattr(model_driver.model_usage_audit, "begin_logical_call", begin_logical_call)
    monkeypatch.setattr(model_driver.model_usage_audit, "begin_attempt", begin_attempt)
    monkeypatch.setattr(model_driver.model_usage_audit, "finish_attempt", finish_attempt)
    monkeypatch.setattr(model_driver.model_usage_audit, "finish_logical_call", finish_logical_call)
    monkeypatch.setattr(model_driver.thread_projection_store, "load", load_projection)
    monkeypatch.setattr(model_driver.thread_projection_store, "save", save_projection)
    monkeypatch.setattr(
        model_driver.settings, "MODEL_FALLBACK_WALL_TIMEOUT_SECONDS", 1
    )

    events = [
        event
        async for event in model_driver.drive_model(
            model="gpt-5.5",
            api_key="test-key",
            user_input="完成当前任务",
            tools=[],
            gateway={
                "run_id": "run-fallback-audit",
                "thread_id": "thread-fallback-audit",
                "user_id": "u1",
            },
        )
    ]

    assert _RetryingFallbackClient.post_calls == 2
    assert len(logical_calls) == 2
    assert logical_calls[1]["parent_logical_call_id"] == "logical-1"
    assert logical_calls[1]["fallback_reason"] == "stream_to_non_stream"
    assert [row["logical_call"].logical_call_id for row in attempts] == [
        "logical-1", "logical-2", "logical-2",
    ]
    assert [row["retry_of_attempt_id"] for row in attempts] == [
        "", "", "attempt-2",
    ]
    assert [row["handle"].request_id for row in attempt_terminals] == [
        "request-1", "request-2", "request-3",
    ]
    assert [row["terminal_status"] for row in attempt_terminals] == [
        "failed", "interrupted", "completed",
    ]
    assert attempt_terminals[-1]["usage"] == {
        "prompt_tokens": 8,
        "completion_tokens": 4,
    }
    assert [row["handle"].logical_call_id for row in logical_terminals] == [
        "logical-1", "logical-2",
    ]
    assert logical_terminals[-1]["selected_attempt_id"] == "attempt-3"
    assert next(event for event in events if event["type"] == "final")["answer"] == "收尾已恢复。"


@pytest.mark.asyncio
async def test_non_stream_connect_error_finishes_exact_attempt_before_retry(monkeypatch):
    logical_calls = []
    attempts = []
    attempt_terminals = []
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
        attempt_terminals.append({"handle": handle, **kwargs})
        return True

    async def finish_logical_call(handle, **kwargs):
        logical_terminals.append({"handle": handle, **kwargs})
        return True

    async def load_projection(**_kwargs):
        return None

    async def save_projection(_state, **_kwargs):
        return False

    _ConnectErrorFallbackClient.post_calls = 0
    monkeypatch.setattr(
        model_driver.httpx,
        "AsyncClient",
        _ConnectErrorFallbackClient,
    )
    monkeypatch.setattr(model_driver.model_usage_audit, "begin_logical_call", begin_logical_call)
    monkeypatch.setattr(model_driver.model_usage_audit, "begin_attempt", begin_attempt)
    monkeypatch.setattr(model_driver.model_usage_audit, "finish_attempt", finish_attempt)
    monkeypatch.setattr(model_driver.model_usage_audit, "finish_logical_call", finish_logical_call)
    monkeypatch.setattr(model_driver.thread_projection_store, "load", load_projection)
    monkeypatch.setattr(model_driver.thread_projection_store, "save", save_projection)
    monkeypatch.setattr(
        model_driver.settings,
        "MODEL_FALLBACK_WALL_TIMEOUT_SECONDS",
        1,
    )

    with pytest.raises(model_driver.httpx.ConnectError, match="connection refused"):
        async for _event in model_driver.drive_model(
            model="gpt-5.5",
            api_key="test-key",
            user_input="完成当前任务",
            tools=[],
            gateway={
                "run_id": "run-connect-error-audit",
                "thread_id": "thread-connect-error-audit",
                "user_id": "u1",
            },
        ):
            pass

    assert _ConnectErrorFallbackClient.post_calls == 2
    assert [row["handle"].request_id for row in attempt_terminals] == [
        "request-1",
        "request-2",
        "request-3",
    ]
    assert [row["terminal_status"] for row in attempt_terminals] == [
        "failed",
        "interrupted",
        "interrupted",
    ]
    for connect_terminal in attempt_terminals[1:]:
        assert connect_terminal["error_code"] == "ConnectError"
        assert connect_terminal["provider_event_seen"] is False
        assert connect_terminal["terminal_seen"] is False
        assert connect_terminal["unknown_provider_charge"] is True
    assert [row["handle"].logical_call_id for row in logical_terminals] == [
        "logical-1",
        "logical-2",
    ]
    assert logical_terminals[-1]["terminal_status"] == "failed"
    assert logical_terminals[-1]["selected_attempt_id"] == "attempt-3"


@pytest.mark.asyncio
async def test_non_stream_fallback_retries_one_gateway_timeout(monkeypatch):
    _RetryingGatewayStatusClient.post_calls = 0
    monkeypatch.setattr(model_driver.httpx, "AsyncClient", _RetryingGatewayStatusClient)
    monkeypatch.setattr(
        model_driver.settings, "MODEL_FALLBACK_WALL_TIMEOUT_SECONDS", 1
    )

    events = [
        event
        async for event in model_driver.drive_model(
            model="gpt-5.5",
            api_key="test-key",
            user_input="完成当前任务",
            tools=[],
        )
    ]

    assert _RetryingGatewayStatusClient.post_calls == 2
    assert next(event for event in events if event["type"] == "final")["answer"] == "收尾已恢复。"


@pytest.mark.asyncio
async def test_non_stream_sensitive_words_status_is_not_retried(monkeypatch):
    _SensitiveWordsFallbackClient.post_calls = 0
    monkeypatch.setattr(
        model_driver.httpx, "AsyncClient", _SensitiveWordsFallbackClient
    )
    monkeypatch.setattr(
        model_driver.settings, "MODEL_FALLBACK_WALL_TIMEOUT_SECONDS", 1
    )

    with pytest.raises(model_driver.ModelProviderPolicyRejected) as failure:
        async for _event in model_driver.drive_model(
            model="gpt-5.5",
            api_key="test-key",
            user_input="完成当前任务",
            tools=[],
        ):
            pass

    assert _SensitiveWordsFallbackClient.post_calls == 1
    assert "敏感词策略" in failure.value.public_message
    assert "private" not in failure.value.public_message


@pytest.mark.asyncio
async def test_transport_interruption_after_public_text_is_not_retried(monkeypatch):
    _NoFallbackAfterPublicTextClient.post_calls = 0
    monkeypatch.setattr(
        model_driver.httpx, "AsyncClient", _NoFallbackAfterPublicTextClient
    )

    with pytest.raises(model_driver.httpx.RemoteProtocolError):
        async for _event in model_driver.drive_model(
            model="gpt-5.5",
            api_key="test-key",
            user_input="完成当前任务",
            tools=[],
        ):
            pass

    assert _NoFallbackAfterPublicTextClient.post_calls == 0


@pytest.mark.asyncio
async def test_transport_interruption_after_private_provider_event_is_not_replayed(monkeypatch):
    _NoReplayAfterProviderEventClient.post_calls = 0
    monkeypatch.setattr(
        model_driver.httpx, "AsyncClient", _NoReplayAfterProviderEventClient
    )

    with pytest.raises(model_driver.ModelStreamReplayUnsafe):
        async for _event in model_driver.drive_model(
            model="gpt-5.5",
            api_key="test-key",
            user_input="完成当前任务",
            tools=[],
        ):
            pass

    assert _NoReplayAfterProviderEventClient.post_calls == 0
