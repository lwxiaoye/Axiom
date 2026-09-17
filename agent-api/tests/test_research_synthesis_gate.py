"""A finished team's report cannot reopen the tool/search loop."""
from unittest.mock import patch

import pytest

from app.services.agent_harness import model_driver
from app.services.chat.capability_broker import CapabilityBroker
from app.services.chat.tools.base import MainTool, ToolValue
from app.services.agents.agent_service import agent_service
from tests.test_main_chat_state_contracts import _Client, _sse, _tool_call, DONE


@pytest.mark.asyncio
@pytest.mark.parametrize("responses", [True, False])
async def test_report_tool_surface_is_empty_even_with_broker_and_unsolicited_call(responses):
    calls = []
    async def search(args):
        calls.append(args)
        return ToolValue(model_content="should not search")
    tool = MainTool("search_web", "search", {"type": "object", "properties": {}},
                    search, readonly=True, internal=True, output_model=ToolValue)
    broker = CapabilityBroker([tool], pinned={"search_web"})
    _Client.requests = []
    _Client.responses = [
        [_tool_call("stale-search", "search_web", {}), DONE],
        [_sse({"content": "# 研究报告\n依据已有来源形成结论；未取得全文的部分保留明确局限。"}), DONE],
    ]
    _Client.last_response = []
    with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", _Client), \
            patch.object(agent_service, "model_responses_capability", return_value=responses):
        events = [event async for event in model_driver.drive_model(
            model="m", api_key="k", user_input="根据团队讨论直接给出研究报告",
            tools=[tool], research_profile=True,
            gateway={"research_synthesis_only": True, "capability_broker": broker},
        )]
    assert not calls
    assert len(_Client.requests) == 2
    assert _Client.requests and all(not request.get("tools") for request in _Client.requests)
    if not responses:
        assert all(not any(str(key).startswith("_") for key in message)
                   for request in _Client.requests for message in request.get("messages", []))
    assert any(event.get("type") == "final" and "研究报告" in event.get("answer", "") for event in events)
