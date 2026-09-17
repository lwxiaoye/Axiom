"""S6: registered tool success observations must pass ToolObservation.model_validate.

Positive control first: a diagnostic that cannot catch a known-bad fixture is blind.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.agent_harness.contracts import ObservationStatus, ToolObservation
from app.services.agent_harness.model_driver import _to_harness_observation
from app.services.chat.tools import build_tools
from app.services.chat.tools.base import ToolExecutionResult


def test_invalid_failure_observation_is_rejected():
    with pytest.raises(ValidationError, match="error_code"):
        ToolObservation(
            call_id="call-bad",
            tool_name="bash",
            status=ObservationStatus.FAILED,
            summary="failed without a machine-readable code",
        )


@pytest.mark.asyncio
async def test_registered_tool_success_observations_validate():
    tools = await build_tools(
        token="t",
        knowledge_ids=["kb-1"],
        web_enabled=True,
        user_id="u1",
        thread_id="th1",
        newapi_key="k",
        run_id="r-s6",
        user_message="写一份报告保存到我的文件里",
        turn_intent="execute",
        action_authority="mutate",
        research_profile=True,
    )
    assert tools, "positive control: the production registry must not be empty"
    names = {tool.name for tool in tools}
    assert {"search_web", "update_plan"} <= names

    for tool in tools:
        dumped = _to_harness_observation(
            ToolExecutionResult(
                status="succeeded",
                model_content=f"{tool.name} ok",
                ui={"summary": tool.name},
            ),
            call_id=f"call-{tool.name}",
            tool_name=tool.name,
        )
        observation = ToolObservation.model_validate(dumped)
        assert observation.tool_name == tool.name
        assert observation.status is ObservationStatus.SUCCEEDED
        assert observation.error_code is None

        failed = _to_harness_observation(
            ToolExecutionResult(
                status="failed",
                model_content=f"{tool.name} failed",
                error={"code": "tool_failed", "retryable": False},
            ),
            call_id=f"call-{tool.name}-fail",
            tool_name=tool.name,
        )
        failed_obs = ToolObservation.model_validate(failed)
        assert failed_obs.status is ObservationStatus.FAILED
        assert failed_obs.error_code == "tool_failed"
