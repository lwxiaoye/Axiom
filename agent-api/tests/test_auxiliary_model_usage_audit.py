import json
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.agent_harness import model_usage_audit
from app.services.chat.tools import browser
from app.services.knowledge import embedding_service, web_search_service


class _AuditRecorder:
    def __init__(self):
        self.logical = []
        self.attempts = []
        self.attempt_finishes = []
        self.logical_finishes = []

    async def begin_logical_call(self, **kwargs):
        self.logical.append(kwargs)
        return SimpleNamespace(logical_call_id="lg-1")

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


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


class _PostClient:
    response = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, *_args, **_kwargs):
        return self.response


@pytest.mark.asyncio
async def test_paid_search_and_browser_digest_are_attributed(monkeypatch, audit):
    _PostClient.response = _Response({
        "organic": [{"title": "t", "link": "https://example.com", "snippet": "s"}],
    })
    monkeypatch.setattr(web_search_service.httpx, "AsyncClient", _PostClient)
    rows, error = await web_search_service._stage_search_single(
        "q",
        {"topK": 2, "serperApiKey": "secret", "_callerRunId": "run-1"},
        "serper",
    )
    assert not error and rows
    assert audit.logical[-1]["purpose"] == "paid_search"

    _PostClient.response = _Response({
        "choices": [{"message": {"content": "digest"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1},
    })
    monkeypatch.setattr(browser.httpx, "AsyncClient", _PostClient)
    monkeypatch.setattr(settings, "BROWSER_FETCH_MODEL", "digest-model")
    answer = await browser._digest(
        "page", url="https://example.com", prompt="summarize", newapi_key="k",
        run_id="run-1", thread_id="thread-1",
    )
    assert answer == "digest"
    assert audit.logical[-1]["purpose"] == "browser_digest"
    assert len(audit.attempts) == len(audit.attempt_finishes) == 2


@pytest.mark.asyncio
async def test_browser_digest_cache_avoids_second_provider_attempt(monkeypatch, audit):
    class _CountingClient(_PostClient):
        calls = 0

        async def post(self, *_args, **_kwargs):
            type(self).calls += 1
            return _Response({
                "choices": [{"message": {"content": "same digest"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1},
            })

    _CountingClient.calls = 0
    browser._DIGEST_CACHE.clear()
    monkeypatch.setattr(browser.httpx, "AsyncClient", _CountingClient)
    monkeypatch.setattr(settings, "BROWSER_FETCH_MODEL", "digest-model")
    monkeypatch.setattr(settings, "BROWSER_FETCH_CACHE_TTL_S", 60)

    first = await browser._digest(
        "page", url="https://example.com", prompt="  summarize  ", newapi_key="k",
        run_id="run-1", thread_id="thread-1",
    )
    second = await browser._digest(
        "page", url="https://example.com", prompt="summarize", newapi_key="k",
        run_id="run-1", thread_id="thread-1",
    )

    assert first == second == "same digest"
    assert _CountingClient.calls == 1
    assert len(audit.attempts) == len(audit.attempt_finishes) == 1


class _EmbeddingResponse(_Response):
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status={self.status_code}")


@pytest.mark.asyncio
async def test_embedding_ordinary_4xx_is_not_retried(monkeypatch, audit):
    class _EmbeddingClient(_PostClient):
        calls = 0

        async def post(self, *_args, **_kwargs):
            type(self).calls += 1
            return _EmbeddingResponse({"error": "bad request"}, status_code=400)

    _EmbeddingClient.calls = 0
    monkeypatch.setattr(embedding_service.httpx, "AsyncClient", _EmbeddingClient)

    with pytest.raises(RuntimeError, match="status=400"):
        await embedding_service.embed_query(
            "query",
            model="embedding-model",
            user_key="key",
            audit_run_id="run-1",
            audit_thread_id="thread-1",
            audit_purpose_detail="router_recall_embedding",
        )

    assert _EmbeddingClient.calls == 1
    assert len(audit.attempts) == len(audit.attempt_finishes) == 1
    assert audit.logical[0]["purpose"] == "tool_internal"
    assert audit.logical[0]["purpose_detail"] == "router_recall_embedding"


@pytest.mark.asyncio
async def test_embedding_5xx_retry_reuses_frozen_payload(monkeypatch, audit):
    class _EmbeddingClient(_PostClient):
        responses = [
            _EmbeddingResponse({"error": "temporary"}, status_code=503),
            _EmbeddingResponse({
                "id": "embedding-ok",
                "data": [{"embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 3, "total_tokens": 3},
            }),
        ]

        async def post(self, *_args, **_kwargs):
            return type(self).responses.pop(0)

    _EmbeddingClient.responses = [
        _EmbeddingResponse({"error": "temporary"}, status_code=503),
        _EmbeddingResponse({
            "id": "embedding-ok",
            "data": [{"embedding": [0.1, 0.2]}],
            "usage": {"prompt_tokens": 3, "total_tokens": 3},
        }),
    ]
    monkeypatch.setattr(embedding_service.httpx, "AsyncClient", _EmbeddingClient)

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(embedding_service.asyncio, "sleep", _no_sleep)
    vector = await embedding_service.embed_query(
        "query",
        model="embedding-model",
        user_key="key",
        audit_run_id="run-1",
        audit_thread_id="thread-1",
    )

    assert vector == [0.1, 0.2]
    assert len(audit.attempts) == len(audit.attempt_finishes) == 2
    first_payload = audit.attempts[0][1]["wire_payload"]
    second_payload = audit.attempts[1][1]["wire_payload"]
    assert first_payload == second_payload == {
        "model": "embedding-model",
        "input": "query",
    }
    assert audit.attempts[1][1]["retry_of_attempt_id"] == "at-1"


@pytest.mark.asyncio
async def test_research_search_forwards_thread_and_root_with_run_fallback(monkeypatch):
    from app.services.agent_harness.research import kernel

    captured: dict = {}

    async def _search_web(_query, **kwargs):
        captured.update(kwargs)
        return {
            "results": [],
            "text": "",
            "error": "",
            "scraped_pages": [],
        }

    async def _record(*_args, **_kwargs):
        return None

    async def _ingest(*_args, **_kwargs):
        return SimpleNamespace()

    class _Channel:
        @staticmethod
        def tool_started(*_args, **_kwargs):
            return "started"

        @staticmethod
        def tool_progress(*_args, **_kwargs):
            return "progress"

        @staticmethod
        def tool_completed(*_args, **_kwargs):
            return "completed"

        @staticmethod
        def tool_failed(*_args, **_kwargs):
            return "failed"

    monkeypatch.setattr(web_search_service, "search_web", _search_web)
    monkeypatch.setattr(kernel, "record_search_attempt", _record)
    monkeypatch.setattr(kernel, "ingest_tool_receipt", _ingest)
    env = SimpleNamespace(
        channel=_Channel(),
        user_id="user-3",
        run_id="run-3",
        thread_id="thread-3",
        newapi_key="key",
    )

    events = [
        event async for event in kernel._search_once(  # noqa: SLF001
            env, query="query", topic_title="topic",
        )
    ]

    assert events[0][0] == "started"
    assert any(event[0] == "completed" for event in events)
    assert captured["caller_run_id"] == "run-3"
    assert captured["caller_thread_id"] == "thread-3"
    assert captured["caller_root_run_id"] == "run-3"


@pytest.mark.asyncio
async def test_live_compaction_forwards_run_and_root(monkeypatch):
    from app.services.agent_harness import conversation_compact, model_driver, run_store

    captured: dict = {}

    async def _compact(messages, **kwargs):
        captured.update(kwargs)
        return {
            "messages": list(messages),
            "tokens_before": 100,
            "tokens_after": 50,
            "replacement_history": [],
        }

    async def _persist(*_args, **_kwargs):
        return None

    monkeypatch.setattr(conversation_compact, "should_compact_history", lambda *_a, **_k: True)
    monkeypatch.setattr(conversation_compact, "history_tokens", lambda *_a, **_k: 100)
    monkeypatch.setattr(conversation_compact, "compact_live_messages", _compact)
    monkeypatch.setattr(model_driver, "_persist_loop_checkpoint", _persist)
    monkeypatch.setattr(run_store, "patch_run_state", _persist)

    messages = [{"role": "user", "content": "hello"}]
    events = [
        event async for event in model_driver._maybe_compact_live_history(  # noqa: SLF001
            messages,
            model="model",
            api_key="key",
            gateway={
                "run_id": "run-4",
                "root_run_id": "root-4",
                "thread_id": "thread-4",
            },
            step=2,
        )
    ]

    assert [event["status"] for event in events] == ["started", "completed"]
    assert captured["run_id"] == "run-4"
    assert captured["root_run_id"] == "root-4"
    assert captured["thread_id"] == "thread-4"
