"""Exercise source coverage, late constraints and small-window checkpoints without a Provider."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.agent_harness import conversation_compact as cc
from app.services.memory import context_service as cs
from app.services.platform.token_estimator import estimate_tokens


@pytest.fixture
def summary_store(monkeypatch):
    from app.core import database

    state = {"rows": [], "saved": None}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, _stmt):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: state["rows"]))

        async def get(self, *_args, **_kwargs):
            return state.get("existing")

        def add(self, row):
            state["saved"] = row

        async def commit(self):
            pass

    monkeypatch.setattr(cs, "runtime_session", lambda: Session)
    monkeypatch.setattr(database, "async_session", Session)
    monkeypatch.setattr(cs, "get_summary", AsyncMock(return_value=None))
    monkeypatch.setattr(cs, "thread_prompt_floor", lambda _: 0)
    monkeypatch.setattr(cs, "calibration_factor", lambda _: 1)
    monkeypatch.setattr(cs.settings, "CONTEXT_COMPACT_MIN_SEGMENT_TOKENS", 1)
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [7000, 10000, 50000])
async def test_full_long_message_reaches_summary_before_coverage_advances(monkeypatch, summary_store, size):
    marker = "唯一验收要求：名单必须匿名化，保留2026秋季口径。"
    summary_store["rows"] = [
        SimpleNamespace(id=1, role="user", content="字" * size + marker),
        SimpleNamespace(id=2, role="assistant", content="收到"),
        SimpleNamespace(id=3, role="user", content="继续"),
    ]

    async def summarize(messages, **_kwargs):
        assert messages[0]["content"] == summary_store["rows"][0].content
        return marker

    monkeypatch.setattr(cc, "generate_compaction_summary", summarize)
    assert await cs.maybe_compact("t-fidelity", "m", "unused", trigger_tokens=1)
    saved = summary_store["saved"]
    payload = json.loads(saved.summary)
    effective, block, dropped = cs.apply_context_budget(summary_store["rows"], {
        "summary": payload["text"], "replacement_history": payload["replacement_history"],
        "covered_message_id": saved.covered_message_id,
    })
    assert saved.covered_message_id == 1
    assert [r.id for r in effective] == [2, 3]
    assert marker in block and dropped == 0


@pytest.mark.asyncio
async def test_failed_summary_does_not_advance_coverage(monkeypatch, summary_store):
    summary_store["rows"] = [SimpleNamespace(id=i, role="user", content="材料" * 6000)
                             for i in range(1, 4)]
    monkeypatch.setattr(cc, "generate_compaction_summary", AsyncMock(side_effect=RuntimeError("incomplete")))
    assert not await cs.maybe_compact("failed-summary", "m", "unused", trigger_tokens=1)
    assert summary_store["saved"] is None
    kept, _, dropped = cs.apply_context_budget(summary_store["rows"], None, hard_cap_tokens=100)
    assert kept == summary_store["rows"] and dropped == 0


@pytest.mark.asyncio
async def test_late_summary_cannot_claim_another_workers_coverage(monkeypatch, summary_store):
    summary_store["rows"] = [SimpleNamespace(id=i, role="user", content="材料" * 6000)
                             for i in range(1, 4)]
    current = SimpleNamespace(covered_message_id=2, summary="另一进程已覆盖消息2", version=2)

    async def summarize(*args, **kwargs):
        summary_store["existing"] = current
        return "本次只覆盖消息1的旧摘要"

    monkeypatch.setattr(cc, "generate_compaction_summary", summarize)
    assert not await cs.maybe_compact("racing-summary", "m", "unused", trigger_tokens=1)
    assert current.summary == "另一进程已覆盖消息2" and current.covered_message_id == 2


@pytest.mark.asyncio
async def test_switching_to_small_window_recompacts_existing_checkpoint(monkeypatch, summary_store):
    monkeypatch.setattr(cc.model_window, "resolve_window", lambda _: 8192)
    monkeypatch.setattr(cc.model_window, "compact_trigger", lambda _: 7000)
    old_text = "较大模型的摘要" + "字" * 12000 + "尾部要求：输出必须匿名化"
    checkpoint = {"summary": old_text, "covered_message_id": 40, "version": 3,
                  "replacement_history": [{"role": "user", "content": cc.SUMMARY_PREFIX + old_text}]}
    monkeypatch.setattr(cs, "get_summary", AsyncMock(return_value=checkpoint))
    current = SimpleNamespace(covered_message_id=40, summary=json.dumps(checkpoint), version=3)
    summary_store["existing"] = current
    summary_store["rows"] = [SimpleNamespace(id=41, role="user", content="继续")]

    async def summarize(messages, **kwargs):
        assert messages[0]["content"].endswith("输出必须匿名化")
        return "继续原有任务；输出必须匿名化。"

    monkeypatch.setattr(cc, "generate_compaction_summary", summarize)
    assert await cs.maybe_compact("small-checkpoint", "small", "unused", trigger_tokens=7000)
    assert current.covered_message_id == 40 and current.version == 4
    saved = json.loads(current.summary)
    assert "输出必须匿名化" in saved["text"]
    kept, block, _ = cs.apply_context_budget(summary_store["rows"], {
        **saved, "summary": saved["text"], "covered_message_id": 40,
    })
    assert kept[0].content == "继续" and estimate_tokens(block) < 7000


@pytest.mark.asyncio
async def test_small_window_partitions_every_character_and_preserves_late_constraint(monkeypatch):
    monkeypatch.setattr(cc.model_window, "resolve_window", lambda _: 8192)
    monkeypatch.setattr(cc.model_window, "compact_trigger", lambda _: 7000)
    monkeypatch.setattr(cc, "calibration_factor", lambda _: 1)
    marker = "必须匿名化"
    messages = [
        {"role": "system", "content": "稳定规则"},
        {"role": "user", "content": "头部要求：按班级汇总。" + "材料" * 10000 + marker},
        {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "已读取原始名单"},
        {"role": "user", "content": "继续"},
    ]
    pieces = []

    async def segment(batch, **_kwargs):
        assert cc._compaction_request_tokens(batch, model="small") <= cc._compaction_input_limit("small")
        pieces.append(batch[-1]["content"].split("\n", 1)[1])
        seen = "".join(m.get("content", "") for m in batch)
        return "已读分段；头部要求：按班级汇总。" + (marker if marker in seen else "")

    monkeypatch.setattr(cc, "_generate_compaction_segment", segment)
    result = await cc.compact_live_messages(messages, model="small", api_key="unused")
    expected = "\n".join(json.dumps(m, ensure_ascii=False, separators=(",", ":"))
                         for m in messages if m["role"] != "system")
    assert "".join(pieces) == expected
    assert len(pieces) > 1
    assert marker in result["summary"]
    assert result["tokens_after"] < cc._compaction_input_limit("small")
    assert result["messages"][0] == messages[0]
    assert messages[1]["content"].endswith(marker)  # caller-owned source unchanged


@pytest.mark.asyncio
async def test_segment_failure_never_returns_a_partial_checkpoint(monkeypatch):
    monkeypatch.setattr(cc.model_window, "resolve_window", lambda _: 8192)
    messages = [{"role": "user", "content": "资料" * 20000 + "尾部限制"}]
    original = json.dumps(messages)
    calls = 0

    async def segment(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("provider incomplete")
        return "第一段摘要"

    monkeypatch.setattr(cc, "_generate_compaction_segment", segment)
    with pytest.raises(RuntimeError, match="incomplete"):
        await cc.compact_live_messages(messages, model="small", api_key="unused")
    assert calls == 2 and json.dumps(messages) == original


def test_recent_user_excerpt_keeps_tail_with_a_cjk_token_budget():
    from app.services.agent_harness.compaction_budget import bounded_excerpt

    text = "保留开头" + "字" * 30000 + "保留末尾"
    excerpt = bounded_excerpt(text, 200)
    assert excerpt.startswith("保留开头") and excerpt.endswith("保留末尾")
    assert estimate_tokens(excerpt) <= 200


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "error"])
async def test_prepare_retains_completed_memory_and_cancels_pending_jobs(monkeypatch, failure):
    from app.services.chat import turn_prepare as tp

    cancelled = asyncio.Event()

    async def catalog(_token):
        if failure == "error":
            raise RuntimeError("catalog unavailable")
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(tp.settings, "AUTO_ROUTE_ENABLED", False)
    monkeypatch.setattr(tp.settings, "TURN_PREPARE_BUDGET_SECONDS", 0.5)
    monkeypatch.setattr(tp, "_get_catalog_records", catalog)
    monkeypatch.setattr(tp, "recent_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(tp, "_lesson_block", AsyncMock(return_value="旧任务经验"))
    monkeypatch.setattr(tp.memory_service, "recall", AsyncMock(return_value=[
        {"type": "preference", "content": "使用中文简洁回答"},
    ]))
    monkeypatch.setattr(tp.personalization_service, "prompt_block", AsyncMock(return_value="自定义偏好"))
    result = await tp.prepare_turn(
        message="起草活动通知", user_context=None, subagent_id=None, knowledge_ids=None,
        selected_knowledge=None, web_search=False, image_urls=[], resolved_model="m", newapi_key="",
        skill_ids=["selected-skill"], token="", user_id="u", thread_id="t",
    )
    assert "使用中文简洁回答" in result.memory_block
    assert "自定义偏好" in result.memory_block and "旧任务经验" in result.memory_block
    assert "selected-skill" in result.effective_skill_ids
    if failure == "timeout":
        assert cancelled.is_set()
