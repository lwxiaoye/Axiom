import asyncio
import json

import pytest

from app.services.agent_harness.completion import CompletionClaim, CompletionVerifier
from app.services.agent_harness.contracts import ToolObservation
from app.services.agent_harness.research import planning, quality, team
from app.services.agent_harness.research.contracts import ResearchTopic
from app.services.agent_harness.research.editing import apply_review
from app.services.agent_harness.research.planning import coordinate_plan
from app.services.agent_harness.research.scope import assign_topics, freeze_scope
from tests.test_research_quality import make_team
from tests.test_research_planning import planner_runtime
from tests.test_research_team import runtime  # noqa: F401


def test_scope_requires_user_basis_for_breadth_and_cannot_expand_after_start():
    topics = [ResearchTopic(topic_id=f"q{i}", title=f"问题{i}") for i in range(5)]
    owner = {"query": "请比较这五个技术方案的性能、可靠性和迁移成本", "topicIds": []}
    with pytest.raises(ValueError, match="2–3"):
        freeze_scope(owner, topics, {})
    with pytest.raises(ValueError):
        freeze_scope(owner, topics, {"breadth_evidence": "全面研究所有相关行业"})
    scope = freeze_scope(owner, topics, {"breadth_evidence": "比较这五个技术方案的性能、可靠性和迁移成本",
        "scope": {"focus": "统一条件比较五个方案", "excluded": ["企业融资历史"]}})
    owner["scope"] = scope
    with pytest.raises(ValueError, match="不得新增"):
        freeze_scope(owner, topics + [ResearchTopic(topic_id="extra", title="融资历史")], {})
    assert scope["excluded"] == ["企业融资历史"]
    owner.update(members=[{"id": str(i)} for i in range(3)], topicIds=scope["topicIds"])
    assign_topics(owner)
    assignments = [m["topicIds"] for m in owner["members"]]
    assert sorted(t for group in assignments for t in group) == scope["topicIds"]
    assert max(map(len, assignments)) == 2
    assign_topics(owner)
    assert assignments == [m["topicIds"] for m in owner["members"]]


@pytest.mark.asyncio
async def test_member_rejects_other_topics_and_stops_at_durable_submission(runtime, monkeypatch):
    rt = make_team(runtime)
    member = rt.team["members"][0]
    member["topicIds"] = rt.team["topicIds"][:1]
    calls, closed = [], asyncio.Event()

    async def search(query, **kwargs):
        calls.append(query)
        assert kwargs["research_page_limit"] == 3
        return {"results": [{"url": "https://example.org/loop", "content": "原始依据", "scraped": True}]}

    async def driver(**kwargs):
        try:
            tools = {tool.name: tool for tool in kwargs["tools"]}
            facts = json.loads(kwargs["user_input"])
            assert [t["id"] for t in facts["研究主题"]] == member["topicIds"]
            denied = await tools["search_web"].observe({"query": "无关扩展", "topic_id": rt.team["topicIds"][1]})
            assert denied.status == "failed" and not calls
            await tools["search_web"].observe({"query": "核心机制", "topic_id": member["topicIds"][0]})
            await tools["submit_research_findings"].observe({"summary": "已核对循环机制。",
                "material": "循环读入观察、选择动作并处理工具反馈，具体例子和边界附原始来源。"})
            assert runtime.state["research_team"]["members"][0]["materials"]["findings"]
            yield {"type": "tool_result", "name": "submit_research_findings", "status": "completed"}
            pytest.fail("A saved dossier must not require another model completion")
        finally:
            closed.set()

    monkeypatch.setattr(team, "drive_model", driver)
    monkeypatch.setattr("app.services.knowledge.web_search_service.search_web", search)
    await rt.member(member, "findings", "")
    assert closed.is_set() and calls == ["核心机制"]
    assert member["findings"] == "已核对循环机制。"
    assert not member.get("checkpoint")
    await rt.member(member, "findings", "")
    assert calls == ["核心机制"]


@pytest.mark.asyncio
async def test_collection_closes_early_for_writing_without_cancelling_member(runtime, monkeypatch):
    rt = make_team(runtime)
    now, calls = [100.0], []
    monkeypatch.setattr(team.time, "time", lambda: now[0])

    async def search(query, **kwargs):
        calls.append(query)
        now[0] = 245.0  # 35s left in the member phase: reserve it for writing.
        return {"results": [{"url": "https://example.org/fact", "content": "证据", "scraped": True}]}

    async def driver(**kwargs):
        tools = {tool.name: tool for tool in kwargs["tools"]}
        topic = rt.team["topicIds"][0]
        await tools["search_web"].observe({"query": "关键依据", "topic_id": topic})
        result = await tools["search_web"].observe({"query": "额外扩展", "topic_id": topic})
        assert json.loads(result.model_content)["collection_closed"] is True
        await tools["submit_research_findings"].observe({"summary": "已完成核心问题取证。", "material": "保留下来的有效分析与依据。"})
        yield {"type": "tool_result", "name": "submit_research_findings"}

    monkeypatch.setattr(team, "drive_model", driver)
    monkeypatch.setattr("app.services.knowledge.web_search_service.search_web", search)
    await rt.member(rt.team["members"][0], "findings", "")
    assert calls == ["关键依据"]
    assert rt.team["members"][0]["materials"]["findings"] == "保留下来的有效分析与依据。"


@pytest.mark.asyncio
async def test_assessment_does_not_pay_for_final_acknowledgement(runtime, monkeypatch):
    rt, closed = make_team(runtime), asyncio.Event()
    async def driver(**kwargs):
        try:
            await kwargs["tools"][0].observe({"action": "partial", "reason": "核心可回答，少量细节缺证", "assignments": []})
            yield {"type": "tool_result", "name": "assess_research"}
            pytest.fail("The decision is already durable")
        finally:
            closed.set()
    monkeypatch.setattr(quality, "drive_model", driver)
    assert (await quality.assess(rt, 0))["action"] == "partial"
    assert closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["plan", "assessment"])
async def test_failed_storage_cannot_end_coordination_as_if_it_were_saved(runtime, monkeypatch, phase):
    rt = planner_runtime(runtime) if phase == "plan" else make_team(runtime)
    marker = "planInitialized" if phase == "plan" else "assessments"
    original, failed, attempts = rt.save, False, []

    async def save(**kwargs):
        nonlocal failed
        if rt.team.get(marker) and not failed:
            failed = True
            raise OSError("storage unavailable")
        await original(**kwargs)

    async def driver(**kwargs):
        args = ({"steps": [{"key": "locks", "title": "锁语义", "detail": "核对原文"}], "reason": "限定问题"}
                if phase == "plan" else {"action": "partial", "reason": "主要问题可回答，少量细节缺证", "assignments": []})
        with pytest.raises(OSError):
            await kwargs["tools"][0].observe(args)
        assert not rt.team.get(marker)
        yield {"type": "tool_result", "status": "failed"}
        attempts.append("retry")
        await kwargs["tools"][0].observe(args)
        yield {"type": "tool_result", "status": "completed"}
        pytest.fail("Durable coordination should now be closed")

    monkeypatch.setattr(rt, "save", save)
    monkeypatch.setattr(planning if phase == "plan" else quality, "drive_model", driver)
    if phase == "plan":
        await coordinate_plan(rt, revision=False)
    else:
        await quality.assess(rt, 0)
    assert attempts == ["retry"]
    assert runtime.state["research_team"][marker]


def test_review_corrects_only_the_exact_fact_and_preserves_analysis_and_table():
    draft = "# Agent loop\n\n## 核心机制\n模型选择下一步，工具反馈进入下一轮。[1]\n\n所有任务必须循环三次。[2]\n\n| 场景 | 选择 |\n| --- | --- |\n| 简单问答 | 直接回答 |\n\n## 例子\n先搜索，再根据结果决定是否计算。[1]"
    decision = {"verdict": "edit", "coverage": "partial", "issues": [{
        "quote": "所有任务必须循环三次。[2]", "replacement": "循环次数由任务进展决定；本次资料不能支持固定三次的要求。[1]",
        "reason": "第二个来源只有摘要，且不能支持三次的说法"}]}
    answer, _ = apply_review(draft, json.dumps(decision), 2)
    assert "所有任务必须" not in answer
    assert "| 简单问答 | 直接回答 |" in answer
    assert "## 例子\n先搜索，再根据结果决定是否计算。[1]" in answer


def test_code_indexes_are_not_rejected_as_source_citations():
    draft = "# 循环示例\n\n取第一条工具反馈 `observations[0]`。[1]\n\n```python\nprint(values[99])\n```\n"
    accepted = json.dumps({"verdict": "accept", "coverage": "complete", "issues": []})
    assert apply_review(draft, accepted, 1)[0] == draft
    with pytest.raises(ValueError, match="引用"):
        apply_review(draft.replace("。[1]", "。[2]"), accepted, 1)


@pytest.mark.parametrize("quote", ["不存在的文字", "重复", "# 标题\n重复 重复 [1]"])
def test_review_cannot_apply_ambiguous_missing_or_whole_report_edits(quote):
    with pytest.raises(ValueError):
        apply_review("# 标题\n重复 重复 [1]", json.dumps({"verdict": "edit", "coverage": "complete", "issues": [
            {"quote": quote, "replacement": "未经支持的全文", "reason": "核对"}]}), 1)


@pytest.mark.parametrize("partial", [False, True])
def test_reviewed_report_settles_even_if_an_earlier_source_failed(runtime, partial):
    answer = "# 有效研究报告\n\n核心结论及依据。[1]"
    ledger = runtime.ledger.model_copy(update={"coverage_outcome": "partial" if partial else ""})
    review = {"goalRevision": 1, "answer": answer, "decision": {"verdict": "accept"}}
    failure = ToolObservation(call_id="search-failed", tool_name="search_web", status="failed", retryable=True, error_code="timeout")
    result = CompletionVerifier().verify(runtime.parent, CompletionClaim(summary=answer), (failure,), ledger, review)
    assert result.terminal and not result.continuation_required
    assert result.resolution == ("partial" if partial else "completed")
    for untrusted in [{**review, "goalRevision": 2}, {**review, "answer": "另一份报告"}, {}]:
        result = CompletionVerifier().verify(runtime.parent, CompletionClaim(summary=answer), (failure,), ledger, untrusted)
        assert result.resolution == "waiting_system"


def test_interrupted_material_still_reaches_synthesis_without_public_success():
    member = {"status": "failed", "draftMaterials": {"findings": {"summary": "进度", "material": "已保存的机制分析与来源"}}}
    assert quality.research_material(member) == "已保存的机制分析与来源"
    assert member["status"] == "failed" and not member.get("findings")
