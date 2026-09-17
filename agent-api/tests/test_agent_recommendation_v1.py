import asyncio
import json

import pytest

from app.core.auth import UserContext
from app.services.agents import recommendation_index as index
from app.services.chat.tools import recommendation as tool_module
from app.services.knowledge.embedding_service import EmbeddingConfig
from app.services.chat import main_tool_turn
from app.services.sse_protocol import HARNESS, SSEChannel
from app.services.tasks import task_run_service


def _record(agent_id: str, name: str, *, trigger=None, negative=None, tags=None, semantic_ready=True):
    record = {
        "record_type": index.RECORD_TYPE,
        "catalog_id": agent_id,
        "name": name,
        "description": f"{name}的专业流程",
        "route_description": "",
        "trigger_examples": list(trigger or []),
        "negative_examples": list(negative or []),
        "tags": list(tags or []),
        "capability_names": [],
        "category": "效率工具",
        "semantic_ready": semantic_ready,
        "is_recommend": False,
    }
    record["embedding_text"] = index._embedding_text(record)
    return record


def _user() -> UserContext:
    return UserContext(user_id="u1", username="tester", tenant_id="1000")


def test_embedding_keeps_positive_semantics_and_excludes_negative_examples():
    record = _record(
        "interview", "面试助手",
        trigger=["模拟产品经理面试"],
        negative=["只是润色一封邮件"],
        tags=["面试", "职业发展"],
    )
    assert "模拟产品经理面试" in record["embedding_text"]
    assert "职业发展" in record["embedding_text"]
    assert "只是润色一封邮件" not in record["embedding_text"]


def test_explicit_detection_requires_agent_discovery_semantics():
    assert index.detect_explicit_request("推荐一个做面试的智能体")
    assert index.detect_explicit_request("智能体广场里有面试助手吗")
    assert not index.detect_explicit_request("这个问题有没有办法解决")
    assert not index.detect_explicit_request("帮我总结这段文字")


def test_query_tokens_ignore_agent_discovery_wrapper_words():
    tokens = index._query_tokens("推荐一个做产品经理模拟面试的智能体")
    assert "产品" in tokens
    assert "面试" in tokens
    assert "推荐" not in tokens
    assert "智能" not in tokens
    assert "能体" not in tokens


def test_negative_example_veto_is_not_embedded_as_a_positive_signal():
    record = _record(
        "interview", "面试助手",
        trigger=["模拟面试"], negative=["只写一份面试通知"],
    )
    _, signals, veto = index._lexical_features("只写一份面试通知", record)
    assert veto is True
    assert "negative_penalty" not in signals


def test_visibility_uses_database_tenant_acl_and_owner_semantics():
    user = _user()
    base = dict(
        user=user, tenant_id="1000", owner_user_id="other",
        role_ids=[], dept_ids=[], user_roles=set(), user_depts=set(),
    )
    assert index._visibility_allows(**base, internal_workflow=False) is True
    assert index._visibility_allows(**base, internal_workflow=True) is False
    assert index._visibility_allows(
        **{**base, "owner_user_id": "u1"}, internal_workflow=True,
    ) is True
    assert index._visibility_allows(
        **{**base, "role_ids": ["r1"], "user_roles": {"r1"}}, internal_workflow=True,
    ) is True
    assert index._visibility_allows(
        **{**base, "tenant_id": "2000", "owner_user_id": "u1"}, internal_workflow=True,
    ) is False


@pytest.mark.asyncio
async def test_implicit_recommendation_requires_two_signals_and_clear_margin(monkeypatch):
    records = [
        _record("interview", "面试助手", trigger=["模拟产品经理面试"], tags=["面试"]),
        _record("resume", "简历助手", trigger=["优化求职简历"], tags=["简历"]),
    ]

    async def ready(*_args, **_kwargs):
        return {"threshold": 0.60, "quality": {"passed": True}}

    async def fetch(_user=None):
        return records

    async def vector(*_args, **_kwargs):
        return [{"id": "interview", "score": 0.82}, {"id": "resume", "score": 0.63}]

    monkeypatch.setattr(index, "readiness", ready)
    monkeypatch.setattr(index, "fetch_catalog_records", fetch)
    monkeypatch.setattr(index, "_raw_vector_hits", vector)

    result = await index.recommend(
        raw_message="我想模拟产品经理面试",
        task="进行一轮结构化模拟面试",
        requested_intent="capability_gap",
        recent_user_messages=[], attachment_types=[], user=_user(),
    )
    assert result.status == "matched"
    assert result.intent == "capability_gap"
    assert [item["id"] for item in result.recommendations] == ["interview"]
    assert result.confidence == "strong"


@pytest.mark.asyncio
async def test_implicit_ambiguous_candidates_are_suppressed(monkeypatch):
    records = [
        _record("a", "A面试助手", trigger=["模拟技术面试"]),
        _record("b", "B面试助手", trigger=["模拟技术面试"]),
    ]

    async def ready(*_args, **_kwargs):
        return {"threshold": 0.60, "quality": {"passed": True}}

    async def fetch(_user=None):
        return records

    async def vector(*_args, **_kwargs):
        return [{"id": "a", "score": 0.80}, {"id": "b", "score": 0.79}]

    monkeypatch.setattr(index, "readiness", ready)
    monkeypatch.setattr(index, "fetch_catalog_records", fetch)
    monkeypatch.setattr(index, "_raw_vector_hits", vector)
    result = await index.recommend(
        raw_message="帮我模拟技术面试", task="模拟技术面试",
        requested_intent="capability_gap", recent_user_messages=[], attachment_types=[], user=_user(),
    )
    assert result.status == "suppressed"
    assert result.suppressed_reason == "ambiguous_top_candidates"


def test_explicit_choices_do_not_pad_with_distant_semantic_neighbours():
    ranked = [
        {
            "catalog_id": "interview", "name": "面试助手", "confidence": "strong",
            "vector_score": 0.81, "rank_score": 0.032, "matched_signals": ["vector", "description"],
        },
        {
            "catalog_id": "counsellor", "name": "AI辅导员", "confidence": "strong",
            "vector_score": 0.53, "rank_score": 0.031, "matched_signals": ["vector", "description"],
        },
        {
            "catalog_id": "writing", "name": "写作助手", "confidence": "medium",
            "vector_score": 0.55, "rank_score": 0.016, "matched_signals": ["vector"],
        },
    ]
    assert [item["catalog_id"] for item in index._explicit_choices(ranked)] == ["interview"]


@pytest.mark.asyncio
async def test_name_only_agent_can_only_be_recommended_by_exact_name(monkeypatch):
    record = _record("bare", "稀有名称助手", semantic_ready=False)
    record.update({"description": "", "embedding_text": "稀有名称助手"})

    async def ready(*_args, **_kwargs):
        return {"threshold": 0.60, "quality": {"passed": True}}

    async def fetch(_user=None):
        return [record]

    async def vector(*_args, **_kwargs):
        return [{"id": "bare", "score": 0.95}]

    monkeypatch.setattr(index, "readiness", ready)
    monkeypatch.setattr(index, "fetch_catalog_records", fetch)
    monkeypatch.setattr(index, "_raw_vector_hits", vector)
    implicit = await index.recommend(
        raw_message="帮我完成一个专业任务", task="专业任务",
        requested_intent="capability_gap", recent_user_messages=[], attachment_types=[], user=_user(),
    )
    exact = await index.recommend(
        raw_message="我想用稀有名称助手", task="找稀有名称助手",
        requested_intent="explicit_request", recent_user_messages=[], attachment_types=[], user=_user(),
    )
    assert implicit.status == "suppressed"
    assert exact.status == "matched"
    assert exact.confidence == "exact"


@pytest.mark.asyncio
async def test_tool_registers_only_when_ready_and_is_once_per_run(monkeypatch):
    monkeypatch.setattr(tool_module.settings, "AGENT_RECOMMEND_SHADOW_MODE", True)
    async def ready(*_args, **_kwargs):
        return {"threshold": 0.6, "quality": {"passed": True}}

    async def no_recent(*_args, **_kwargs):
        return set()

    async def recommend(**_kwargs):
        return index.RecommendationResult(
            "matched", "explicit_request",
            [{"id": "interview", "name": "面试助手", "reason": "适合模拟面试"}],
            "strong", ("trigger", "description"),
        )

    monkeypatch.setattr(index, "readiness", ready)
    monkeypatch.setattr(tool_module, "_recent_recommendation_ids", no_recent)
    monkeypatch.setattr(index, "recommend", recommend)
    tool = await tool_module.build_recommend_agent_tool(
        user=_user(), raw_message="推荐面试智能体", recent_user_messages=[],
        attachments=[], thread_id="t1", run_id="r1", selected_specialist=False,
    )
    assert tool is not None
    assert tool.spec.visible_to_user is False
    assert tool.spec.timeout_seconds == 2.0
    first = await tool.execute({"task": "模拟面试", "intent": "explicit_request"})
    second = await tool.execute({"task": "模拟面试", "intent": "explicit_request"})
    assert first.ui["status"] == "matched"
    assert second.ui["suppressed_reason"] == "run_call_limit"

    monkeypatch.setattr(index, "readiness", lambda *_args, **_kwargs: asyncio.sleep(0, result=None))
    unavailable = await tool_module.build_recommend_agent_tool(
        user=_user(), raw_message="x", recent_user_messages=[], attachments=[],
        thread_id="t2", run_id="r2", selected_specialist=False,
    )
    assert unavailable is None


def test_rollout_bucket_is_stable_and_respects_boundaries(monkeypatch):
    monkeypatch.setattr(tool_module.settings, "AGENT_RECOMMEND_ROLLOUT_PERCENT", 0)
    assert tool_module._in_rollout("u1") is False
    monkeypatch.setattr(tool_module.settings, "AGENT_RECOMMEND_ROLLOUT_PERCENT", 100)
    assert tool_module._in_rollout("u1") is True
    monkeypatch.setattr(tool_module.settings, "AGENT_RECOMMEND_ROLLOUT_PERCENT", 37)
    assert tool_module._in_rollout("same-user") == tool_module._in_rollout("same-user")


@pytest.mark.asyncio
async def test_tool_result_projects_structured_sse_without_timeline_noise():
    async def events():
        yield {"type": "tool_started", "name": "recommend_agent", "call_id": "call-1", "args": {}}
        yield {
            "type": "tool_result", "name": "recommend_agent", "call_id": "call-1",
            "status": "succeeded",
            "observation": {
                "structured_data": {"ui": {
                    "status": "matched", "intent": "capability_gap", "confidence": "strong",
                    "recommendations": [{
                        "id": "interview", "name": "面试助手", "reason": "适合模拟面试",
                    }],
                }},
            },
        }

    channel = SSEChannel(HARNESS, "thread-1", "run-1")
    out = {"trace": []}
    frames = [frame async for frame in main_tool_turn.map_tool_loop_events(
        channel, events(), {}, out,
    )]
    assert len(frames) == 1
    payload = json.loads(frames[0].removeprefix("data: "))
    assert payload["type"] == "recommend_agents"
    assert payload["data"] == {
        "ids": ["interview"], "intent": "capability_gap", "confidence": "strong",
        "reasons": {"interview": "适合模拟面试"},
    }
    assert out["trace"] == []
    assert "recommend_agents" in task_run_service._TRACE_EVENT_TYPES


class _LockSession:
    def __init__(self, acquired=1):
        self.acquired = acquired

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def scalar(self, *_args, **_kwargs):
        return self.acquired

    async def execute(self, *_args, **_kwargs):
        return None


class _Count:
    def __init__(self, count):
        self.count = count


class _BuildClient:
    def __init__(self, expected_count):
        self.expected_count = expected_count
        self.upserts = []

    async def upsert(self, **kwargs):
        self.upserts.extend(kwargs.get("points") or [])

    async def count(self, **_kwargs):
        return _Count(self.expected_count)


def _indexed_record(agent_id="a1"):
    record = _record(agent_id, "面试助手", trigger=["模拟面试"], tags=["面试"])
    record.update({
        "source_app_id": agent_id, "tenant_id": "0", "pc_url": f"/agent/run/{agent_id}",
        "role_ids": [], "dept_ids": [], "status": 1, "source_version": 1,
        "index_schema_version": index.INDEX_SCHEMA_VERSION,
    })
    record["content_hash"] = "hash-" + agent_id
    return record


@pytest.mark.asyncio
async def test_rebuild_switches_alias_only_after_quality_and_count_gate(monkeypatch):
    records = [_indexed_record()]
    client = _BuildClient(expected_count=2)
    switched = []

    async def cfg():
        return EmbeddingConfig("embed-test", "key", "http://embedding", 3)

    async def manifest(_collection):
        return None

    async def calibrated(_collection, _records):
        return {"passed": True, "threshold": 0.61}

    async def switch(collection):
        switched.append(collection)

    monkeypatch.setattr(index, "async_session", lambda: _LockSession())
    monkeypatch.setattr(index.embedding_service, "get_active_embedding_config", cfg)
    monkeypatch.setattr(index, "fetch_catalog_records", lambda *_a, **_k: asyncio.sleep(0, result=records))
    monkeypatch.setattr(index, "_ensure_collection", lambda *_a, **_k: asyncio.sleep(0))
    monkeypatch.setattr(index, "_manifest", manifest)
    monkeypatch.setattr(index.embedding_service, "embed_texts", lambda *_a, **_k: asyncio.sleep(0, result=[[0.1, 0.2, 0.3]]))
    monkeypatch.setattr(index, "_calibrate", calibrated)
    monkeypatch.setattr(index, "_replace_alias", switch)
    monkeypatch.setattr(index, "_get_client", lambda: client)
    monkeypatch.setattr(index, "readiness", lambda *_a, **_k: asyncio.sleep(0, result={"ready": True}))

    result = await index.rebuild_index()
    assert result["status"] == "ready"
    assert len(switched) == 1
    assert len(client.upserts) == 2  # one agent + one ready manifest
    assert client.upserts[-1].payload["quality"]["passed"] is True


@pytest.mark.asyncio
async def test_rebuild_failure_or_lock_never_switches_alias(monkeypatch):
    switched = []
    monkeypatch.setattr(index, "async_session", lambda: _LockSession(acquired=0))
    locked = await index.rebuild_index()
    assert locked == {"status": "locked"}

    records = [_indexed_record()]
    client = _BuildClient(expected_count=2)

    async def cfg():
        return EmbeddingConfig("embed-test", "key", "http://embedding", 3)

    monkeypatch.setattr(index, "async_session", lambda: _LockSession(acquired=1))
    monkeypatch.setattr(index.embedding_service, "get_active_embedding_config", cfg)
    monkeypatch.setattr(index, "fetch_catalog_records", lambda *_a, **_k: asyncio.sleep(0, result=records))
    monkeypatch.setattr(index, "_ensure_collection", lambda *_a, **_k: asyncio.sleep(0))
    monkeypatch.setattr(index, "_manifest", lambda *_a, **_k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(index.embedding_service, "embed_texts", lambda *_a, **_k: asyncio.sleep(0, result=[[0.1, 0.2, 0.3]]))
    monkeypatch.setattr(index, "_calibrate", lambda *_a, **_k: asyncio.sleep(0, result={"passed": False, "reason": "golden_metrics_below_gate"}))
    monkeypatch.setattr(index, "_replace_alias", lambda collection: asyncio.sleep(0, result=switched.append(collection)))
    monkeypatch.setattr(index, "_get_client", lambda: client)

    failed = await index.rebuild_index()
    assert failed["status"] == "not_ready"
    assert failed["reason"] == "golden_metrics_below_gate"
    assert switched == []
