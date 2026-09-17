from unittest.mock import patch

import pytest

from app.services.agent_harness import model_driver
from app.services.chat.turn_finalizer import persist_assistant_turn
from tests.test_main_chat_state_contracts import _Client, _sse, DONE


@pytest.mark.asyncio
@pytest.mark.parametrize("research", [True, False])
async def test_model_final_keeps_research_evidence_markers_only(research):
    _Client.requests = []
    _Client.responses = [[_sse({"content": "核心事实有正文支持[1]，边界由另一个来源说明[2]。"}), DONE]]
    _Client.last_response = []
    with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", _Client):
        events = [event async for event in model_driver.drive_model(
            model="m", api_key="k", user_input="整理结论", tools=[], research_profile=research,
            gateway={"research_synthesis_only": research},
        )]
    answer = next(e["answer"] for e in events if e["type"] == "final")
    assert ("[1]" in answer and "[2]" in answer) is research


@pytest.mark.asyncio
@pytest.mark.parametrize("research", [True, False])
async def test_persisted_research_report_keeps_same_references(research):
    rows = []
    class Session:
        def add(self, row):
            rows.append(row)
        async def commit(self):
            pass
    row = await persist_assistant_turn(
        Session(), None, thread_id="test", run_id="test",
        content="# 研究报告\n核心事实有正文支持[1]。", preserve_source_markers=research,
    )
    assert rows == [row]
    assert ("[1]" in row.content) is research
