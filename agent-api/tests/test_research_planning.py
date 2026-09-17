import asyncio
import copy
import json

import pytest

from app.services.agent_harness.research import planning, team
from app.services.agent_harness.research.contracts import ResearchLedger, ResearchTopic, SourceRecord
from app.services.agent_harness.research.planning import coordinate_plan
from tests.test_research_team import runtime  # noqa: F401


def planner_runtime(runtime):
    runtime.state["research"] = ResearchLedger(query="研究 SQLite WAL 的并发限制").to_state()
    return team.ResearchTeam(runtime.env, {
        "id": "rt", "version": 0, "goalRevision": 1, "query": "研究 SQLite WAL 的并发限制",
        "stage": "planning", "members": [{"id": "a", "name": "小澈", "role": "researcher", "status": "pending", "searches": []}],
        "leader": {"id": "leader", "name": "主智能体", "role": "leader", "status": "pending", "searches": []},
        "topicIds": [], "topics": [], "activity": [],
    })


@pytest.mark.asyncio
async def test_model_plan_and_evidence_driven_revision_preserve_sources(runtime, monkeypatch):
    rt = planner_runtime(runtime)
    calls = []
    async def driver(**kwargs):
        calls.append(kwargs)
        assert kwargs["gateway"]["run_id"] == ""
        assert [t.name for t in kwargs["tools"]] == ["revise_research_plan"]
        facts = json.loads(kwargs["user_input"])
        steps = [
            {"key": "locks", "title": "核对 WAL 的读写锁关系", "detail": "对照官方锁说明与独立来源"},
            {"key": "checkpoint", "title": "分析长读事务对检查点的影响", "detail": "核对快照和日志增长条件"},
            {"key": "storage", "title": "核实共享存储的部署边界", "detail": "区分本机与网络文件系统"},
        ]
        if len(calls) == 2:
            assert facts["实际证据"] and facts["成员公开发现"][0]["findings"]
            steps[0]["detail"] = "已有并发证据，补核排他锁生效时点"
            rejected = await kwargs["tools"][0].observe({"steps": [*steps,
                {"key": "readonly", "title": "新增只读打开的版本差异", "detail": "新支线"}], "reason": "扩大范围"})
            assert rejected.status == "failed"
        result = await kwargs["tools"][0].observe({"steps": steps, "reason": "针对 SQLite 的并发与部署限制分头核验。"})
        assert result.status == "succeeded"
        yield {"type": "final", "answer": "计划已更新"}
    monkeypatch.setattr(planning, "drive_model", driver)
    await coordinate_plan(rt, revision=False)
    first = copy.deepcopy(runtime.state["research"])
    assert [t["title"] for t in first["topics"]] == ["核对 WAL 的读写锁关系", "分析长读事务对检查点的影响", "核实共享存储的部署边界"]
    assert all(t["status"] == "pending" for t in first["topics"])
    source = SourceRecord(url="https://sqlite.org/wal.html", topic_id="locks", query="WAL locks", snippet="原文", scraped=True).model_dump()
    runtime.state["research"]["sources"] = [source]
    rt.team["members"][0]["findings"] = "只读打开条件在版本间存在差异，需补证。"
    await coordinate_plan(rt, revision=True)
    assert len(runtime.state["research"]["topics"]) == 3
    assert runtime.state["research"]["sources"] == [source]
    assert rt.team["topicIds"][-1] == "storage"
    assert runtime.state["loop_checkpoint"] == {"root": True}
    assert rt.team["planRevision"] == 2
    await coordinate_plan(rt, revision=False)
    await coordinate_plan(rt, revision=True)
    assert len(calls) == 2


def test_revision_cannot_delete_existing_evidence_identity():
    ledger = ResearchLedger(topics=[ResearchTopic(topic_id="locks", title="锁语义")])
    with pytest.raises(ValueError, match="保留"):
        planning.revised_topics(ledger, [{"key": "new", "title": "换个问题", "detail": ""}])
    with pytest.raises(ValueError, match="唯一"):
        planning.revised_topics(ledger, [{"key": "locks", "title": "锁"}] * 2)


@pytest.mark.asyncio
async def test_new_plan_rejects_unbounded_scope_but_accepts_focused_questions(runtime, monkeypatch):
    rt = planner_runtime(runtime)
    async def driver(**kwargs):
        steps = [{"key": f"q{i}", "title": f"核心问题{i}", "detail": "核查来源"} for i in range(6)]
        rejected = await kwargs["tools"][0].observe({"steps": steps, "reason": "全面展开"})
        assert rejected.status == "failed"
        assert not rt.team.get("planInitialized")
        accepted = await kwargs["tools"][0].observe({"steps": steps[:2], "reason": "只回答核心问题"})
        assert accepted.status == "succeeded"
        yield {"type": "final", "answer": "已保存"}
    monkeypatch.setattr(planning, "drive_model", driver)
    await coordinate_plan(rt, revision=False)
    assert len(rt.team["topics"]) == 2


@pytest.mark.asyncio
async def test_progress_requires_real_sources_and_terminal_keeps_gaps(runtime):
    rt = planner_runtime(runtime)
    ledger = ResearchLedger(query=rt.team["query"], search_calls=3, topics=[
        ResearchTopic(topic_id="locks", title="锁语义", detail="核验锁"),
        ResearchTopic(topic_id="storage", title="网络盘限制", detail="核验边界"),
    ], sources=[
        SourceRecord(url="https://sqlite.org/wal.html", topic_id="locks", query="wal", snippet="全文", scraped=True),
        SourceRecord(url="https://example.org/wal", topic_id="locks", query="wal", snippet="独立说明", scraped=True),
    ])
    runtime.state["research"] = ledger.to_state()
    rt.team["members"][0]["searches"] = [{"topic_id": "storage", "status": "running"}]
    async with rt.lock:
        await planning.refresh_progress(rt)
    assert [t["status"] for t in runtime.state["research"]["topics"]] == ["completed", "in_progress"]
    async with rt.lock:
        await planning.refresh_progress(rt, final=True)
    assert [t["status"] for t in runtime.state["research"]["topics"]] == ["completed", "failed"]
    rows = [e for e in runtime.events if isinstance(e, list)][-1]
    assert "证据仍不足" in rows[1]["detail"]


@pytest.mark.asyncio
async def test_cancel_during_planning_propagates(runtime, monkeypatch):
    rt = planner_runtime(runtime)
    started = asyncio.Event()
    async def slow(**kwargs):
        started.set()
        await asyncio.Event().wait()
        yield {}
    monkeypatch.setattr(planning, "drive_model", slow)
    task = asyncio.create_task(coordinate_plan(rt, revision=False))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not rt.team.get("planInitialized")


@pytest.mark.asyncio
async def test_saved_plan_ends_coordination_without_an_extra_model_reply(runtime, monkeypatch):
    rt, closed = planner_runtime(runtime), asyncio.Event()
    async def driver(**kwargs):
        try:
            result = await kwargs["tools"][0].observe({"steps": [
                {"key": "loop", "title": "循环的机制与边界", "detail": "解释输入、动作和反馈"}],
                "reason": "只围绕核心机制取证", "scope": {"focus": "理解循环", "excluded": ["全框架盘点"]}})
            assert result.status == "succeeded"
            yield {"type": "tool_result", "name": "revise_research_plan"}
            pytest.fail("The saved plan is the end of this coordination task")
        finally:
            closed.set()
    monkeypatch.setattr(planning, "drive_model", driver)
    await coordinate_plan(rt, revision=False)
    assert closed.is_set()
    assert runtime.state["research_team"]["scope"]["excluded"] == ["全框架盘点"]
