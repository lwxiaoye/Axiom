import asyncio
from datetime import datetime, timezone

import pytest

from app.services.agent_harness import run_store
from app.services.agent_harness.research import budget, kernel, quality, team
from tests.test_research_team import runtime  # noqa: F401
from tests.test_research_quality import make_team
from tests.test_research_report_review import add_source


@pytest.mark.asyncio
async def test_deadline_uses_root_creation_and_survives_recovery_and_revision(runtime, monkeypatch):
    now = datetime.now(timezone.utc).timestamp()
    original = run_store.get_run_state

    async def packed(run_id):
        row = await original(run_id)
        row["created_at"] = datetime.fromtimestamp(now - 200, timezone.utc).replace(tzinfo=None).isoformat()
        return row

    monkeypatch.setattr(run_store, "get_run_state", packed)
    first = await budget.ensure_budget(runtime.env)
    assert first == pytest.approx(now + 400)
    runtime.state["goal_revision"] = 2
    monkeypatch.setattr(budget.time, "time", lambda: now + 100)
    assert await budget.ensure_budget(runtime.env) == first
    assert budget.remaining(runtime.env) == pytest.approx(300)
    assert budget.remaining(runtime.env, reserve=180) == pytest.approx(120)


@pytest.mark.asyncio
async def test_new_root_uses_own_budget_even_with_inherited_team(runtime, monkeypatch):
    now = datetime.now(timezone.utc)
    runtime.state["research_team"] = {"startedAt": "2020-01-01T00:00:00Z"}

    async def packed(_):
        return {"state": runtime.state, "created_at": now.isoformat()}

    monkeypatch.setattr(run_store, "get_run_state", packed)
    assert await budget.ensure_budget(runtime.env) == now.timestamp() + 600


@pytest.mark.asyncio
async def test_expired_collection_cannot_restart_search_but_still_writes_report(runtime, monkeypatch):
    add_source(runtime)
    runtime.state["research_budget"] = {"deadline_at": 1}
    async def forbidden(_):
        pytest.fail("Expired recovered Run must not call a model or a search tool")
        yield ""
    monkeypatch.setattr(kernel, "_prepare", forbidden)
    async def publish(env):
        from app.services.agent_harness.research.review import reviewed_report
        assert env.research_team_synthesis_only
        async def report(**kwargs):
            import json
            yield {"type": "final", "answer": json.dumps({"verdict": "accept", "coverage": "partial", "issues": []})
                if kwargs.get("gateway", {}).get("audit_scope") else "# 研究报告\n依据已有材料回答核心问题[1]，未取得外部正文的说法保留局限。"}
        async for event in reviewed_report(env, report):
            yield event
    monkeypatch.setattr("app.services.chat.main_tool_turn.run_agent_turn", publish)
    events = [e async for e in kernel.run(runtime.env)]
    assert "依据已有材料回答核心问题" in events[-1]["answer"]
    assert runtime.state["research"]["coverage_outcome"] == "partial"


@pytest.mark.asyncio
async def test_cancelled_expired_run_keeps_cancellation(runtime):
    runtime.state.update(research_budget={"deadline_at": 1}, cancel_requested=True)
    with pytest.raises(asyncio.CancelledError):
        _ = [e async for e in kernel.run(runtime.env)]


@pytest.mark.asyncio
async def test_entire_kernel_deadline_cancels_work_and_closes_source(runtime, monkeypatch):
    runtime.state["research_budget"] = {"deadline_at": budget.time.time() + budget.REPORT_RESERVE_SECONDS + 0.05}
    closed = asyncio.Event()
    async def slow(_):
        try:
            yield "progress"
            await asyncio.Event().wait()
        finally:
            closed.set()
    async def deliver(env):
        assert closed.is_set()
        assert budget.remaining(env) > 175
        yield "report"
    monkeypatch.setattr(kernel, "_prepare", slow)
    monkeypatch.setattr(kernel, "_deliver", deliver)
    assert [e async for e in kernel.run(runtime.env)] == ["progress", "report"]
    assert closed.is_set()


@pytest.mark.asyncio
async def test_report_reserve_skips_new_collection_and_preserves_evidence(runtime, monkeypatch):
    runtime.env.research_deadline = budget.time.time() + 170
    async def forbidden(self):
        pytest.fail("Report reserve must not be spent on starting more collection")
    monkeypatch.setattr(team.ResearchTeam, "execute", forbidden)
    _ = [e async for e in team.run_team(runtime.env, runtime.ledger)]
    assert runtime.state["research_team"]["stage"] == "synthesizing"
    assert runtime.state["research_team"]["qualityStop"]
    assert runtime.state["research"]["query"] == runtime.ledger.query


@pytest.mark.asyncio
async def test_assessment_caps_supplement_to_two_and_honors_report_reserve(runtime, monkeypatch):
    rt = make_team(runtime)
    async def driver(**kwargs):
        assignments = [{"member_id": "m1", "topic_id": t["id"], "instruction": "核实核心冲突"} for t in rt.team["topics"]]
        await kwargs["tools"][0].observe({"action": "supplement", "reason": "核心结论有分歧", "assignments": assignments})
        yield {"type": "final", "answer": "已记录"}
    monkeypatch.setattr(quality, "drive_model", driver)
    first = await quality.assess(rt, 0)
    assert first["action"] == "supplement"
    assert len(first["assignments"]) <= 2
    rt.team.pop("assessments")
    runtime.env.research_deadline = budget.time.time() + 210
    assert (await quality.assess(rt, 0))["action"] == "partial"


@pytest.mark.asyncio
async def test_budget_must_be_durable_before_work_starts(runtime, monkeypatch):
    async def fail(*args, **kwargs):
        return None
    monkeypatch.setattr(run_store, "patch_run_state", fail)
    with pytest.raises(RuntimeError, match="预算保存失败"):
        await budget.ensure_budget(runtime.env)
    assert not hasattr(runtime.env, "research_deadline")


@pytest.mark.asyncio
async def test_report_recovers_saved_member_analysis_after_collection_interrupt(runtime, monkeypatch):
    add_source(runtime)
    runtime.state["research_budget"] = {"deadline_at": 1}
    runtime.state["research_team"] = {"goalRevision": 1, "members": [{"name": "研究员", "status": "failed",
        "draftMaterials": {"findings": {"summary": "进展", "material": "已经保存的具体机制、示例和来源"}}}]}
    async def publish(env):
        assert "已经保存的具体机制、示例和来源" in env.turn_guard_prompt
        yield "report"
    monkeypatch.setattr("app.services.chat.main_tool_turn.run_agent_turn", publish)
    assert [event async for event in kernel.run(runtime.env)] == ["report"]
