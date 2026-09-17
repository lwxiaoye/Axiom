import hashlib
import hmac
from types import SimpleNamespace

import pytest

from app.services.chat.tools import knowledge
from app.services.agents import agent_executor
from app.services.agent_harness import model_driver


class _Response:
    status_code = 200
    text = ""

    def json(self):
        return {"success": True, "result": {"items": []}}


class _Client:
    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, *, json, headers):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return _Response()


def _canonical(timestamp, user_id, payload):
    def stringify(value):
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    values = [
        timestamp,
        user_id,
        payload.get("agentId"),
        payload.get("query"),
        payload.get("topK"),
        payload.get("scoreThreshold"),
        payload.get("semanticWeight"),
        payload.get("keywordWeight"),
        payload.get("retrievalMode"),
        payload.get("rerankEnabled"),
        payload.get("rerankTopN"),
        str(len(payload.get("knowledgeIds") or [])),
        *(payload.get("knowledgeIds") or []),
    ]
    return b"".join(
        f"{len(stringify(value).encode('utf-8'))}:".encode("utf-8") + stringify(value).encode("utf-8")
        for value in values
    )


def test_internal_canonical_preserves_false_and_zero_values():
    canonical = knowledge._internal_retrieval_canonical("1", "user-a", {
        "knowledgeIds": [], "agentId": "agent-a", "query": "q", "topK": 0,
        "scoreThreshold": 0.0, "semanticWeight": 0, "keywordWeight": 0,
        "rerankEnabled": False, "rerankTopN": 0,
    })

    assert b"5:false" in canonical
    assert canonical.count(b"1:0") >= 5


def test_internal_canonical_matches_java_shared_test_vector():
    payload = {
        "knowledgeIds": ["kb-a"], "agentId": "agent-a", "query": "library", "topK": 5,
        "scoreThreshold": 0.4, "semanticWeight": None, "keywordWeight": None,
        "retrievalMode": None, "rerankEnabled": False, "rerankTopN": None,
    }
    signature = hmac.new(
        b"shared-secret", knowledge._internal_retrieval_canonical("1700000000", "user-a", payload),
        hashlib.sha256,
    ).hexdigest()

    assert signature == "fce2fc20d1dca0e654811d1682301fd38a6a7b036a4ed3c93904282b18e649c7"


def test_analytics_signature_binds_execution_turn_source_and_retrieval_payload(monkeypatch):
    payload = {
        "knowledgeIds": ["kb-a"], "agentId": None, "query": "library", "topK": 5,
        "scoreThreshold": None, "semanticWeight": None, "keywordWeight": None,
        "retrievalMode": None, "rerankEnabled": None, "rerankTopN": None,
    }
    monkeypatch.setattr(knowledge.settings, "INTERNAL_SYNC_SECRET", "shared-secret")
    monkeypatch.setattr(knowledge.time, "time", lambda: 1_700_000_000)

    headers = knowledge._analytics_retrieval_headers(
        "user-a", payload, execution_id="execution-a", turn_id="turn-a", source="CHAT",
    )

    retrieval_signature = hmac.new(
        b"shared-secret", _canonical("1700000000", "user-a", payload), hashlib.sha256,
    ).hexdigest()
    values = ["1700000000", "user-a", "execution-a", "turn-a", "CHAT", retrieval_signature]
    canonical = b"".join(
        f"{len(value.encode('utf-8'))}:".encode("utf-8") + value.encode("utf-8")
        for value in values
    )
    expected = hmac.new(b"shared-secret", canonical, hashlib.sha256).hexdigest()

    assert headers["X-Knowledge-Analytics-Signature"] == expected


@pytest.mark.asyncio
async def test_published_agent_retrieval_uses_signed_internal_endpoint(monkeypatch):
    calls = []
    monkeypatch.setattr(knowledge.httpx, "AsyncClient", lambda **_kwargs: _Client(calls))
    monkeypatch.setattr(knowledge.settings, "INTERNAL_SYNC_SECRET", "shared-secret")
    monkeypatch.setattr(knowledge.time, "time", lambda: 1_700_000_000)

    result = await knowledge.retrieve_knowledge(
        "token-a", ["kb-a"], "图书馆开放时间", threshold=0.4,
        tenant_id="tenant-a", agent_id="agent-a", agent_user_id="user-a",
    )

    assert result["ok"] is True
    assert calls[0]["url"].endswith("/ai/knowledge/retrieval/internal")
    assert calls[0]["headers"]["X-Internal-User-Id"] == "user-a"
    payload = calls[0]["json"]
    expected = hmac.new(
        b"shared-secret", _canonical("1700000000", "user-a", payload), hashlib.sha256,
    ).hexdigest()
    assert calls[0]["headers"]["X-Internal-Signature"] == expected
    assert payload["scoreThreshold"] == 0.4


@pytest.mark.asyncio
async def test_direct_knowledge_retrieval_stays_on_acl_endpoint(monkeypatch):
    calls = []
    monkeypatch.setattr(knowledge.httpx, "AsyncClient", lambda **_kwargs: _Client(calls))

    result = await knowledge.retrieve_knowledge(
        "token-a", ["kb-a"], "图书馆开放时间", tenant_id="tenant-a",
    )

    assert result["ok"] is True
    assert calls[0]["url"].endswith("/ai/knowledge/retrieval/test")
    assert "X-Internal-Signature" not in calls[0]["headers"]


@pytest.mark.asyncio
async def test_published_agent_dataset_tool_passes_agent_and_current_user(monkeypatch):
    calls = []

    class _Engine:
        ctx = SimpleNamespace(token="token-a", app_id="agent-a", user_id="user-a", preview_only=False)

        @staticmethod
        def input_value(_node, key, default=None):
            if key == "agent_datasetParams":
                return {"datasets": [{"datasetId": "kb-a"}], "similarity": 0.4}
            return default

    async def fake_retrieve(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True, "chunks": [], "error": None}

    monkeypatch.setattr(model_driver, "retrieve_knowledge", fake_retrieve)
    tools, _notes = await agent_executor.build_tools(_Engine(), {})

    await tools[0].execute({"query": "图书馆开放时间"})

    assert calls[0][1]["agent_id"] == "agent-a"
    assert calls[0][1]["agent_user_id"] == "user-a"
