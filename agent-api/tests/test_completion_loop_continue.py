"""Codex turn complete: an assistant message without tool calls ends the turn."""

import pytest

from app.services.agent_harness.completion import CompletionDecision
from app.services.agent_harness.contracts import RunPhase
from tests.test_tool_loop_circuit_breakers import DONE, FakeAsyncClient, _drive, sse


@pytest.mark.asyncio
async def test_unverified_completion_does_not_continue_same_loop(monkeypatch):
    patched = []

    async def fake_verify(_run_id, claim):
        assert claim.summary
        return CompletionDecision(
            accepted=False,
            phase=RunPhase.VERIFYING,
            reason_codes=("artifact_evidence_missing",),
            resolution="continue",
            continuation_required=True,
            observation={
                "kind": "completion_verification_gap",
                "unmet_conditions": ["artifact_evidence_missing"],
                "evidence": [{"tool_name": "write_file", "status": "succeeded"}],
            },
        )

    async def fake_record(*_args, **_kwargs):
        return 0

    async def fake_patch(_run_id, patch, **kwargs):
        patched.append((patch, kwargs))
        return {"state": patch}

    monkeypatch.setattr(
        "app.services.agent_harness.completion.verify_run_completion", fake_verify,
    )
    monkeypatch.setattr(
        "app.services.tasks.task_run_service.record_tool_observations", fake_record,
    )
    monkeypatch.setattr(
        "app.services.agent_harness.run_store.patch_run_state", fake_patch,
    )

    FakeAsyncClient.responses = [
        [sse({"content": "文件已经写好了。"}), DONE],
        [sse({"content": "已保存到我的文件。"}), DONE],
    ]

    events = await _drive(
        model="m",
        api_key="k",
        user_input="写一份说明.md",
        tools=[],
        gateway={"run_id": "run-gap", "thread_id": "t1", "user_id": "u1"},
    )

    finals = [event for event in events if event.get("type") == "final"]
    assert len(finals) == 1
    assert finals[0]["answer"] == "文件已经写好了。"
    assert len(FakeAsyncClient.requests) == 1
    assert not any(
        isinstance(item[0], dict) and "completion_observation" in item[0]
        for item in patched
    )
    assert all(
        "completion_verification_gap" not in str((message.get("content") or ""))
        for request in FakeAsyncClient.requests
        for message in request.get("messages") or []
    )
