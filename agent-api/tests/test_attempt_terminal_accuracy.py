from types import SimpleNamespace

import httpx
import pytest

from app.services.agent_harness import model_usage_audit
from app.services.chat import turn_finalizer
from app.services.chat.tools import image_fetch
from app.services.chat.tools.base import CURRENT_TOOL_CONTEXT, ToolExecutionContext
from app.services.memory import memory_service
from app.services.platform import platform_config_service


class _AuditRecorder:
    def __init__(self):
        self.logical = []
        self.attempts = []
        self.attempt_finishes = []
        self.logical_finishes = []

    async def begin_logical_call(self, **kwargs):
        handle = SimpleNamespace(logical_call_id=f"lg-{len(self.logical) + 1}")
        self.logical.append((kwargs, handle))
        return handle

    async def begin_attempt(self, logical, **kwargs):
        handle = SimpleNamespace(attempt_id=f"at-{len(self.attempts) + 1}")
        self.attempts.append((logical, kwargs, handle))
        return handle

    async def finish_attempt(self, handle, **kwargs):
        self.attempt_finishes.append((handle, kwargs))
        return True

    async def finish_logical_call(self, handle, **kwargs):
        self.logical_finishes.append((handle, kwargs))
        return True


@pytest.fixture
def audit(monkeypatch):
    recorder = _AuditRecorder()
    monkeypatch.setattr(model_usage_audit, "begin_logical_call", recorder.begin_logical_call)
    monkeypatch.setattr(model_usage_audit, "begin_attempt", recorder.begin_attempt)
    monkeypatch.setattr(model_usage_audit, "finish_attempt", recorder.finish_attempt)
    monkeypatch.setattr(model_usage_audit, "finish_logical_call", recorder.finish_logical_call)
    return recorder


class _RaisingClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, *_args, **_kwargs):
        raise RuntimeError("network failed before response")

    def stream(self, *_args, **_kwargs):
        raise RuntimeError("network failed before response")


def _assert_unseen_failure(audit: _AuditRecorder) -> None:
    assert len(audit.attempts) == len(audit.attempt_finishes) == 1
    facts = audit.attempt_finishes[0][1]
    assert facts["provider_event_seen"] is False
    assert facts["terminal_seen"] is False


class _PartialStreamResponse:
    status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def aiter_lines(self):
        yield 'data: {"id":"resp-partial","choices":[{"delta":{"content":"part"}}]}'
        raise RuntimeError("stream body failed")


class _PartialStreamClient(_RaisingClient):
    def stream(self, *_args, **_kwargs):
        return _PartialStreamResponse()


@pytest.mark.asyncio
async def test_memory_summary_network_error_has_no_provider_event(monkeypatch, audit):
    async def _memories(*_args, **_kwargs):
        return [{"type": "fact", "content": "remembered"}]

    monkeypatch.setattr(memory_service, "list_memories", _memories)
    monkeypatch.setattr(httpx, "AsyncClient", _RaisingClient)
    result = await memory_service.generate_summary(
        "user-1",
        model="m",
        api_key="k",
        run_id="run-1",
        thread_id="thread-1",
    )

    assert result == ""
    _assert_unseen_failure(audit)


@pytest.mark.asyncio
async def test_title_langchain_network_error_has_no_provider_event(monkeypatch, audit):
    class _Llm:
        def __init__(self, **_kwargs):
            pass

        async def ainvoke(self, _messages):
            raise RuntimeError("connect failed")

    monkeypatch.setattr(turn_finalizer, "ChatOpenAI", _Llm)
    await turn_finalizer.generate_title(
        "thread-1",
        "first message",
        "m",
        "k",
        run_id="run-1",
    )

    _assert_unseen_failure(audit)


@pytest.mark.asyncio
async def test_image_vet_network_error_has_no_provider_event(monkeypatch, audit):
    async def _ocr_config():
        return {"model": "vision-model"}

    monkeypatch.setattr(platform_config_service, "get_ocr_config", _ocr_config)
    monkeypatch.setattr(image_fetch.httpx, "AsyncClient", _RaisingClient)
    token = CURRENT_TOOL_CONTEXT.set(ToolExecutionContext(
        call_id="tool-1", run_id="run-1", thread_id="thread-1",
    ))
    try:
        accepted, rejected = await image_fetch.vet_images(
            {"image.png": b"\x89PNGdata"}, "k",
        )
    finally:
        CURRENT_TOOL_CONTEXT.reset(token)

    assert accepted == {"image.png": b"\x89PNGdata"}
    assert rejected == []
    _assert_unseen_failure(audit)
