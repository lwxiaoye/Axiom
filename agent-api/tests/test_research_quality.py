import asyncio
import copy
import json

import pytest

from app.services.agent_harness.research import quality, team
from app.services.agent_harness.research.contracts import ResearchLedger, ResearchTopic, SourceRecord
from app.services.agent_harness.research.engine import evidence_brief, synthesis_sources
from app.services.agent_harness.research.evidence import sources_from_receipt, merge_sources
from app.services.agent_harness.research.report import build_document
from tests.test_research_team import runtime  # noqa: F401


def make_team(runtime):
    return team.ResearchTeam(runtime.env, {
        "id": "rt", "version": 0, "goalRevision": 1, "query": runtime.ledger.query,
        "topicIds": [t.topic_id for t in runtime.ledger.topics],
        "topics": [{"id": t.topic_id, "title": t.title} for t in runtime.ledger.topics],
        "leader": {"id": "lead", "name": "主智能体", "task": "统筹", "status": "pending", "searches": []},
        "members": [{"id": "m1", "role": "researcher", "name": "小澈", "task": "核验", "status": "pending", "searches": []}],
    })


@pytest.mark.asyncio
async def test_false_ready_records_limits_without_automatically_expanding_scope(runtime, monkeypatch):
    rt = make_team(runtime)
    calls = []
    async def driver(**kwargs):
        calls.append(kwargs)
        receipt = await kwargs["tools"][0].observe({"action": "ready", "reason": "材料足够", "assignments": []})
        assert receipt.status == "succeeded"
        yield {"type": "final", "answer": "结束"}
    monkeypatch.setattr(quality, "drive_model", driver)
    result = await quality.assess(rt, 0)
    assert result["action"] == "partial"
    assert result["assignments"] == []
    restored = team.ResearchTeam(runtime.env, json.loads(json.dumps(runtime.state["research_team"])))
    assert await quality.assess(restored, 0) == result
    assert len(calls) == 1
    final = await quality.assess(restored, quality.MAX_SUPPLEMENT_ROUNDS)
    assert final["action"] == "partial"
    assert runtime.state["loop_checkpoint"] == {"root": True}


@pytest.mark.asyncio
async def test_spent_network_budget_cannot_schedule_another_supplement(runtime, monkeypatch):
    rt = make_team(runtime)
    rt.team["members"][0]["searches"] = [{"status": "failed"}] * quality.MAX_TEAM_READS

    async def driver(**kwargs):
        await kwargs["tools"][0].observe({"action": "supplement", "reason": "继续读取", "assignments": []})
        yield {"type": "final", "answer": "结束"}

    monkeypatch.setattr(quality, "drive_model", driver)
    result = await quality.assess(rt, 0)
    assert result["action"] == "partial"
    assert "预算已用完" in result["reason"]


def test_repeated_page_reading_preserves_foundation_and_latest_evidence():
    source = SourceRecord(url="https://sqlite.org/wal.html", title="WAL", scraped=True,
                          snippet="EARLY_CONCURRENCY_FACT " + "原始正文" * 950)
    ledger = ResearchLedger(query="WAL", sources=[source])
    for index in range(4):
        ledger = merge_sources(ledger, [source.model_copy(update={
            "snippet": f"LATEST_CHECKPOINT_{index} " + str(index) * 3980,
        })])
    body = evidence_brief(ledger)
    assert "EARLY_CONCURRENCY_FACT" in body
    assert "LATEST_CHECKPOINT_3" in body
    assert len(ledger.sources[0].snippet) <= 8000


@pytest.mark.asyncio
async def test_full_material_is_preserved_but_not_broadcast(runtime, monkeypatch):
    rt = make_team(runtime)
    material = "具体数据、来源解释和反例。" * 300 + "最后一项适用边界"
    async def search(*args, **kwargs):
        return {"results": [{"url": "https://sqlite.org/wal.html", "content": "实际正文", "scraped": True}]}
    async def driver(**kwargs):
        tools = {t.name: t for t in kwargs["tools"]}
        await tools["search_web"].observe({"query": "wal", "topic_id": rt.team["topicIds"][0]})
        result = await tools["submit_research_findings"].observe({"summary": "已核对锁与反例。", "material": material})
        assert result.status == "succeeded"
        yield {"type": "final", "answer": "已核对锁与反例。"}
    monkeypatch.setattr(team, "drive_model", driver)
    monkeypatch.setattr("app.services.knowledge.web_search_service.search_web", search)
    await rt.member(rt.team["members"][0], "findings", "")
    member = rt.team["members"][0]
    assert member["materials"]["findings"] == material
    assert member["findings"] == "已核对锁与反例。"
    assert "最后一项适用边界" in quality.research_material(member)
    assert material not in json.dumps(runtime.events, ensure_ascii=False)
    assert "materials" not in json.dumps(team.public_snapshot(rt.team))


@pytest.mark.asyncio
async def test_supplement_has_real_read_receipts_and_does_not_repeat_on_resume(runtime, monkeypatch):
    runtime.ledger.sources.append(SourceRecord(url="https://sqlite.org/wal.html", snippet="待核验的搜索摘要"))
    runtime.state["research"] = runtime.ledger.to_state()
    rt = make_team(runtime)
    member = rt.team["members"][0]
    member["findings"], member["review"] = "原发现", "发现缺证"
    rt.team["leader"]["findings"] = "已提交"
    invoked = []
    async def assess(runtime, index):
        return ({"action": "supplement", "assignments": [{"member_id": "m1", "topic_id": rt.team["topicIds"][0], "instruction": "读取官方锁语义原文"}],
                 "evidenceBefore": []} if index == 0 else {"action": "ready"})
    async def scrape(url, **kwargs):
        invoked.append(url)
        return {"ok": True, "url": url, "title": "锁语义", "text": "新原文" * 1500, "scraped": True}
    async def driver(**kwargs):
        tools = {t.name: t for t in kwargs["tools"]}
        assert json.loads(kwargs["user_input"])["定向补证任务"]
        receipt = await tools["read_research_source"].observe({"url": "https://sqlite.org/wal.html", "topic_id": rt.team["topicIds"][0]})
        assert receipt.status == "succeeded"
        yield {"type": "final", "answer": "已读正文并修正锁边界。"}
    monkeypatch.setattr(team, "assess", assess)
    monkeypatch.setattr(team, "drive_model", driver)
    monkeypatch.setattr("app.services.knowledge.web_search_service.scrape_url", scrape)
    await rt.execute()
    assert rt.team["stage"] == "synthesizing"
    assert member["supplement_1"]
    assert runtime.state["research"]["sources"][0]["scraped"]
    assert len(runtime.state["research"]["sources"][0]["snippet"]) == 4500
    restored = team.ResearchTeam(runtime.env, copy.deepcopy(runtime.state["research_team"]))
    await restored.execute()
    assert len(invoked) == 1


@pytest.mark.asyncio
async def test_no_new_body_stops_supplement_instead_of_looping(runtime, monkeypatch):
    runtime.ledger.sources.append(SourceRecord(url="https://sqlite.org/wal.html", snippet="仅有摘要"))
    runtime.state["research"] = runtime.ledger.to_state()
    rt = make_team(runtime)
    rt.team["members"][0].update(findings="摘要", review="缺正文")
    rt.team["leader"]["findings"] = "摘要"
    async def assess(runtime, index):
        assert index == 0
        return {"action": "supplement", "evidenceBefore": [], "assignments": [{"member_id": "m1", "topic_id": rt.team["topicIds"][0], "instruction": "补证"}]}
    async def driver(**kwargs):
        yield {"type": "final", "answer": "来源不可用，无法核验。"}
    monkeypatch.setattr(team, "assess", assess)
    monkeypatch.setattr(team, "drive_model", driver)
    await rt.execute()
    assert rt.team["qualityStop"]
    assert rt.team["stage"] == "synthesizing"


@pytest.mark.asyncio
async def test_quality_cancel_propagates_without_saved_success(runtime, monkeypatch):
    rt = make_team(runtime)
    started = asyncio.Event()
    async def driver(**kwargs):
        started.set()
        await asyncio.Event().wait()
        yield {}
    monkeypatch.setattr(quality, "drive_model", driver)
    task = asyncio.create_task(quality.assess(rt, 0))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not rt.team.get("assessments")


def test_late_fulltext_reaches_report_with_stable_reference_numbers():
    ledger = ResearchLedger(topics=[ResearchTopic(topic_id="critical", title="关键边界")], sources=[
        SourceRecord(url=f"https://example.org/{i}", snippet="普通摘要" * 1000) for i in range(79)])
    source = sources_from_receipt(tool_name="search_web", meta=None, topic_id="critical", citations=[{
        "url": "https://sqlite.org/wal.html", "content": "关键机制与反例。" * 450, "scraped": True}])[0]
    ledger.sources.append(source)
    brief = evidence_brief(ledger)
    assert "关键机制与反例" in brief and "[80]" in brief
    assert len(brief) < 75_000
    assert len(source.snippet) > 2000
    doc = build_document(markdown="# 报告\n\n边界 [80]", ledger=ledger)
    assert doc.references[79]["url"] == synthesis_sources(ledger)[79].url == source.url


def test_shared_source_does_not_erase_earlier_topic_coverage():
    first = SourceRecord(url="https://sqlite.org/wal.html", topic_id="locks", snippet="官方正文", scraped=True, query="locks")
    other = SourceRecord(url="https://independent.example/wal", topic_id="locks", snippet="独立正文", scraped=True, query="locks")
    ledger = ResearchLedger(sources=[first, other])
    merged = merge_sources(ledger, [first.model_copy(update={"topic_id": "recovery", "query": "recovery"})])
    assert merged.sources[0].applies_to("locks") and merged.sources[0].applies_to("recovery")
    assert merged.topic_coverage_ready(ResearchTopic(topic_id="locks", title="锁语义"))


@pytest.mark.asyncio
async def test_team_budget_blocks_network_before_request(runtime, monkeypatch):
    rt = make_team(runtime)
    rt.team["leader"]["searches"] = [{"phase": "findings"}] * quality.MAX_TEAM_READS
    async def forbidden(*args, **kwargs):
        raise AssertionError("exhausted budget must not call network")
    async def driver(**kwargs):
        read = next(t for t in kwargs["tools"] if t.name == "read_research_source")
        result = await read.observe({"url": "https://sqlite.org/wal.html", "topic_id": rt.team["topicIds"][0]})
        assert result.status == "failed"
        yield {"type": "final", "answer": "预算内未能补齐，明确局限。"}
    monkeypatch.setattr("app.services.knowledge.web_search_service.scrape_url", forbidden)
    monkeypatch.setattr(team, "drive_model", driver)
    await rt.member(rt.team["members"][0], "supplement_1", "")
    assert rt.team["members"][0]["searches"] == []


def test_focused_read_reaches_later_sections_and_preserves_previous_excerpt():
    from app.services.knowledge.web_search_service import research_excerpt
    body = "目录 checkpoint\n" + "前言正文。" * 2000 + "checkpoint: 长读事务阻止回收。" * 40
    selected = research_excerpt(body, "checkpoint")
    assert "长读事务阻止回收" in selected and len(selected) <= 4000
    old = SourceRecord(url="https://sqlite.org/wal.html", snippet="此前并发机制", scraped=True)
    ledger = merge_sources(ResearchLedger(sources=[old]), [old.model_copy(update={"snippet": selected})])
    assert "此前并发机制" in ledger.sources[0].snippet and "长读事务阻止回收" in ledger.sources[0].snippet
    body = "SQLITE_BUSY: contention\n" + "普通正文。" * 1000 + "checkpoint: recovery\n" * 500
    selected = research_excerpt(body, "checkpoint SQLITE_BUSY")
    assert "SQLITE_BUSY" in selected and "checkpoint" in selected
    assert r"SQLITE\_BUSY" in research_excerpt(body.replace("SQLITE_BUSY", r"SQLITE\_BUSY"), "checkpoint SQLITE_BUSY")


def test_overload_response_is_not_research_evidence():
    from app.services.knowledge.web_search_service import _usable_scrape_text
    assert not _usable_scrape_text("# Server Load Too High\n" + "The server load is currently too high. Please try again later. " * 4)
    assert _usable_scrape_text("# 运维分析\n" + "本文讨论如何诊断 server load too high 报错，并对连接池与负载情况进行分析。" * 5)
