import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.services.agent_harness.research import kernel, team
from app.services.agent_harness.public_errors import ResearchEvidenceMissing, ResearchSourcesUnavailable
from tests.test_research_team import runtime  # noqa: F401
from tests.test_research_rejection_lifecycle import pump, run_failure  # noqa: F401


def finding_driver():
    async def driver(**kwargs):
        data = json.loads(kwargs["user_input"])
        topic = data["研究主题"][0]["id"]
        await kwargs["tools"][0].observe({"query": topic, "topic_id": topic})
        yield {"type": "final", "answer": "尚未取得可核验来源。"}
    return driver


@pytest.mark.asyncio
async def test_source_recovery_reuses_frozen_queries_and_delivers_receipt_evidence(runtime, monkeypatch):
    calls = []

    async def search(query, **kwargs):
        calls.append(query)
        if len(calls) <= 3:
            return {"results": [], "error": "ReadTimeout", "error_code": "timeout"}
        return {"results": [{"url": "https://example.org/report", "content": "实际取得的正文",
                             "scraped": True}]}

    monkeypatch.setattr(team, "drive_model", finding_driver())
    monkeypatch.setattr("app.services.knowledge.web_search_service.search_web", search)
    _ = [e async for e in team.run_team(runtime.env, runtime.ledger)]
    assert len(calls) == 4
    assert calls[-1] in calls[:3]
    assert runtime.state["research"]["sources"][0]["snippet"] == "实际取得的正文"
    assert len(runtime.state["research_team"]["sourceRecovery"]) <= 2
    before = list(calls)
    _ = [e async for e in team.run_team(runtime.env, runtime.ledger)]
    assert calls == before  # reopening/recovery cannot start another collection round


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_no_evidence_stops_before_quality_writer_and_review(runtime, monkeypatch, failed):
    calls = []
    async def search(query, **kwargs):
        calls.append(query)
        return {"results": [], "error": "timeout" if failed else ""}
    assessment = AsyncMock()
    synthesis = AsyncMock()
    monkeypatch.setattr(team, "drive_model", finding_driver())
    monkeypatch.setattr(team, "assess", assessment)
    monkeypatch.setattr("app.services.knowledge.web_search_service.search_web", search)
    monkeypatch.setattr("app.services.chat.main_tool_turn.run_agent_turn", synthesis)
    _ = [e async for e in team.run_team(runtime.env, runtime.ledger)]
    assert len(calls) == 5
    assert all(query in calls[:3] for query in calls[3:])
    assessment.assert_not_called()
    with pytest.raises(ResearchSourcesUnavailable if failed else ResearchEvidenceMissing):
        _ = [e async for e in kernel._deliver(runtime.env)]
    synthesis.assert_not_called()
    assert not runtime.state.get("research_report_review_failure")
    assert runtime.state["loop_checkpoint"] == {"root": True}


@pytest.mark.asyncio
async def test_cancelled_run_does_not_enter_source_gate(runtime, monkeypatch):
    monkeypatch.setattr(kernel.run_store, "get_run_snapshot", AsyncMock(
        return_value=runtime.parent.model_copy(update={"cancel_requested": True})))
    with pytest.raises(asyncio.CancelledError):
        _ = [e async for e in kernel._deliver(runtime.env)]


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [ResearchEvidenceMissing, ResearchSourcesUnavailable])
async def test_exhausted_source_failure_uses_the_shared_terminal_boundary(pump, error_type):
    await run_failure(pump, error_type())
    assert pump.state["status"] == pump.state["phase"] == "failed"
    assert pump.state["terminal_reason"] == error_type.public_message
    failures = [event for event in pump.recorded if event["type"] == "run.failed"]
    assert len(failures) == 1
    assert failures[0]["data"]["message"] == error_type.public_message
    pump.context_quarantine.assert_not_awaited()
    pump.recovery.assert_not_awaited()
