import ast
import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from app.services.agent_harness.completion import (
    CompletionClaim,
    CompletionDecision,
    CompletionVerifier,
)
from app.services.agent_harness.contracts import (
    AgentMode,
    ObservationStatus,
    PlanSnapshot,
    PlanStepSnapshot,
    PlanStepStatus,
    RunPhase,
    RunSnapshot,
    ToolObservation,
)
from app.services.chat import turn_finalizer
from app.services.sse_protocol import HARNESS, SSEChannel


def _run_with_step(
    status: PlanStepStatus,
    *,
    reason: str = "",
    execution_profile: dict | None = None,
    pending_input: dict | None = None,
) -> RunSnapshot:
    plan = PlanSnapshot(
        run_id="run-1",
        goal_revision=0,
        plan_version=1,
        goal="finish the task",
        steps=(PlanStepSnapshot(
            step_id="step-1",
            order=0,
            title="Finish the required work",
            status=status,
            reason=reason,
        ),),
        updated_at=datetime.utcnow(),
    )
    return RunSnapshot(
        run_id="run-1",
        thread_id="thread-1",
        user_id="user-1",
        agent_mode=AgentMode.STANDARD,
        phase=RunPhase.VERIFYING,
        state_version=1,
        goal_revision=0,
        plan_version=1,
        event_cursor=0,
        execution_profile=execution_profile,
        pending_input=pending_input,
        plan=plan,
    )


def _successful_observation() -> ToolObservation:
    return ToolObservation(
        call_id="call-1",
        tool_name="bash",
        status=ObservationStatus.SUCCEEDED,
        summary="command completed",
    )


def _failed_observation(tool_name: str = "bash") -> ToolObservation:
    return ToolObservation(
        call_id=f"{tool_name}-failed",
        tool_name=tool_name,
        status=ObservationStatus.FAILED,
        summary="attempt failed",
        error_code="tool_failed",
    )


def test_required_pending_plan_step_is_a_verifier_gap_after_tool_success():
    decision = CompletionVerifier().verify(
        _run_with_step(PlanStepStatus.PENDING),
        CompletionClaim(summary="done"),
        (_successful_observation(),),
    )
    assert decision.phase is RunPhase.VERIFYING
    assert decision.continuation_required is True
    assert "required_plan_steps_incomplete" in decision.reason_codes


def test_interrupted_final_response_is_a_verifier_gap_even_when_tool_succeeded():
    decision = CompletionVerifier().verify(
        _run_with_step(PlanStepStatus.COMPLETED),
        CompletionClaim(summary="partial summary", response_interrupted=True),
        (_successful_observation(),),
    )
    assert decision.phase is RunPhase.VERIFYING
    assert decision.continuation_required is True
    assert "final_response_interrupted" in decision.reason_codes


def test_later_success_of_same_tool_resolves_earlier_failure():
    decision = CompletionVerifier().verify(
        _run_with_step(PlanStepStatus.COMPLETED),
        CompletionClaim(summary="published"),
        (
            _failed_observation("publish_ppt_artifact"),
            ToolObservation(
                call_id="publish-succeeded",
                tool_name="publish_ppt_artifact",
                status=ObservationStatus.SUCCEEDED,
                summary="published",
                artifact_refs=[{"filename": "deck.pptx"}],
            ),
        ),
    )
    assert decision.phase is RunPhase.COMPLETED
    assert "tool_failure_unresolved" not in decision.reason_codes


def test_latest_failure_or_success_of_another_tool_does_not_resolve_failure():
    run = _run_with_step(PlanStepStatus.COMPLETED)
    claim = CompletionClaim(summary="done")
    latest_failure = CompletionVerifier().verify(
        run,
        claim,
        (_successful_observation(), _failed_observation()),
    )
    unrelated_success = CompletionVerifier().verify(
        run,
        claim,
        (_failed_observation("publish_ppt_artifact"), _successful_observation()),
    )
    assert latest_failure.phase is RunPhase.VERIFYING
    assert unrelated_success.phase is RunPhase.VERIFYING
    assert latest_failure.continuation_required is True
    assert "tool_failure_unresolved" in latest_failure.reason_codes
    assert "tool_failure_unresolved" in unrelated_success.reason_codes


def test_invalidated_required_step_needs_a_public_reason():
    accepted = CompletionVerifier().verify(
        _run_with_step(PlanStepStatus.INVALIDATED, reason="goal changed"),
        CompletionClaim(summary="done"),
        (_successful_observation(),),
    )
    rejected = CompletionVerifier().verify(
        _run_with_step(PlanStepStatus.INVALIDATED),
        CompletionClaim(summary="done"),
        (_successful_observation(),),
    )
    assert accepted.phase is RunPhase.COMPLETED
    assert rejected.phase is RunPhase.VERIFYING
    assert rejected.continuation_required is True


def _artifact_profile() -> dict:
    return {"id": "artifact_coding", "artifact_kind": "presentation"}


def _publish_observation(*artifacts: dict) -> ToolObservation:
    return ToolObservation(
        call_id="publish-1",
        tool_name="publish_ppt_artifact",
        status=ObservationStatus.SUCCEEDED,
        summary="published",
        artifact_refs=list(artifacts),
    )


@pytest.mark.parametrize(
    ("artifacts", "reason"),
    [
        (
            ({"filename": "deck-source.zip", "file_id": "source-1"},),
            "presentation_pptx_receipt_missing",
        ),
        (
            ({"filename": "deck.pptx", "file_id": "pptx-1"},),
            None,
        ),
        (
            ({"filename": "deck.pptx", "file_id": ""},),
            "presentation_artifact_file_id_missing",
        ),
    ],
)
def test_presentation_profile_rejects_incomplete_publish_receipts(artifacts, reason):
    decision = CompletionVerifier().verify(
        _run_with_step(
            PlanStepStatus.COMPLETED,
            execution_profile=_artifact_profile(),
        ),
        CompletionClaim(summary="done"),
        (_publish_observation(*artifacts),),
    )
    if reason is None:
        assert decision.phase is RunPhase.COMPLETED
    else:
        assert decision.phase is RunPhase.VERIFYING
        assert decision.continuation_required is True
        assert reason in decision.reason_codes


def test_presentation_profile_requires_publish_tool_and_accepts_real_pptx():
    run = _run_with_step(
        PlanStepStatus.COMPLETED,
        execution_profile=_artifact_profile(),
    )
    wrong_tool = CompletionVerifier().verify(
        run,
        CompletionClaim(summary="done"),
        (ToolObservation(
            call_id="bash-1",
            tool_name="bash",
            status=ObservationStatus.SUCCEEDED,
            artifact_refs=[
                {"filename": "deck.pptx", "file_id": "pptx-1"},
            ],
        ),),
    )
    accepted = CompletionVerifier().verify(
        run,
        CompletionClaim(summary="done"),
        (_publish_observation(
            {"filename": "deck.pptx", "file_id": "pptx-1"},
        ),),
    )
    assert "presentation_publish_receipt_missing" in wrong_tool.reason_codes
    assert accepted.phase is RunPhase.COMPLETED


def test_partial_verdict_emits_run_partial(monkeypatch):
    from app.services.agent_harness import completion, run_store
    from app.services.tasks import task_run_service

    async def fake_verify(_run_id, _claim):
        return CompletionDecision(
            accepted=False,
            phase=RunPhase.PARTIAL,
            reason_codes=("required_plan_steps_incomplete",),
            resolution="partial",
            terminal=True,
            verified=True,
        )

    async def fake_finalize(_run_id, status, **kwargs):
        assert status == "completed"
        assert kwargs["outcome"] == "partial"
        return True

    async def fake_patch(_run_id, _patch, **kwargs):
        assert kwargs["phase"] == "partial"
        return True

    async def fake_status(_run_id):
        return "completed"

    monkeypatch.setattr(completion, "verify_run_completion", fake_verify)
    monkeypatch.setattr(task_run_service, "finalize_run", fake_finalize)
    monkeypatch.setattr(task_run_service, "get_run_status", fake_status)
    monkeypatch.setattr(run_store, "patch_run_state", fake_patch)

    async def collect():
        channel = SSEChannel(HARNESS, "thread-1", "run-1")
        return [
            frame async for frame in turn_finalizer.finalize_terminal(
                channel, "run-1", {}, None, "partial answer", "error",
            )
        ]

    frames = asyncio.run(collect())
    assert any('"type": "run.partial"' in frame for frame in frames)
    assert not any('"type": "run.completed"' in frame for frame in frames)


def test_uncommitted_terminal_verdict_is_recovered_without_terminal_frame(monkeypatch):
    from app.services.agent_harness import completion
    from app.services.tasks import task_run_service

    recovery_calls = []

    async def fake_verify(_run_id, _claim):
        return CompletionDecision(
            accepted=False,
            phase=RunPhase.PARTIAL,
            reason_codes=("required_plan_steps_incomplete",),
            resolution="partial",
            terminal=True,
            verified=True,
        )

    async def fake_finalize(_run_id, _status, **_kwargs):
        return False

    async def fake_status(_run_id):
        return "running"

    async def fake_recover(run_id, **kwargs):
        recovery_calls.append((run_id, kwargs))
        return True

    monkeypatch.setattr(completion, "verify_run_completion", fake_verify)
    monkeypatch.setattr(task_run_service, "finalize_run", fake_finalize)
    monkeypatch.setattr(task_run_service, "get_run_status", fake_status)
    monkeypatch.setattr(task_run_service, "recover_run_after_fault", fake_recover)

    async def collect():
        channel = SSEChannel(HARNESS, "thread-1", "run-1")
        return [
            frame async for frame in turn_finalizer.finalize_terminal(
                channel, "run-1", {}, None, "partial answer", "error",
            )
        ]

    frames = asyncio.run(collect())
    assert not any('"type": "run.partial"' in frame for frame in frames)
    assert not any('"type": "run.completed"' in frame for frame in frames)
    assert not any('"type": "run.failed"' in frame for frame in frames)
    assert [item[0] for item in recovery_calls] == ["run-1"]


def test_verifier_gap_completes_when_model_already_stopped(monkeypatch):
    from app.services.agent_harness import completion, run_store
    from app.services.tasks import task_run_service

    calls = []

    async def fake_verify(_run_id, _claim):
        return CompletionDecision(
            accepted=False,
            phase=RunPhase.VERIFYING,
            reason_codes=("required_plan_steps_incomplete",),
            resolution="continue",
            continuation_required=True,
            observation={
                "kind": "completion_verification_gap",
                "unmet_conditions": ["required_plan_steps_incomplete"],
                "evidence": [],
            },
        )

    async def fake_finalize(_run_id, status, **kwargs):
        calls.append(("finalize", status, kwargs))
        assert status == "completed"
        return True

    async def fake_patch(_run_id, patch, **kwargs):
        calls.append(("patch", patch, kwargs))
        return {"state": patch}

    async def fake_status(_run_id):
        return "completed"

    async def fake_recover(_run_id, **kwargs):
        calls.append(("recover", kwargs))
        return True

    monkeypatch.setattr(completion, "verify_run_completion", fake_verify)
    monkeypatch.setattr(task_run_service, "finalize_run", fake_finalize)
    monkeypatch.setattr(run_store, "patch_run_state", fake_patch)
    monkeypatch.setattr(task_run_service, "get_run_status", fake_status)
    monkeypatch.setattr(task_run_service, "recover_run_after_fault", fake_recover)

    async def collect():
        channel = SSEChannel(HARNESS, "thread-1", "run-1")
        return [
            frame async for frame in turn_finalizer.finalize_terminal(
                channel, "run-1", {}, None, "done", "error",
            )
        ]

    frames = asyncio.run(collect())
    assert any('"type": "run.completed"' in frame for frame in frames)
    assert not any('"type": "run.partial"' in frame for frame in frames)
    assert calls[0][:2] == ("finalize", "completed")
    assert any(item[0] == "patch" and item[2].get("phase") == "completed" for item in calls)
    assert "recover" not in [item[0] for item in calls]


def test_retryable_completion_fault_never_coerces_to_completed(monkeypatch):
    from app.services.agent_harness import completion
    from app.services.tasks import task_run_service

    async def fake_verify(_run_id, _claim):
        return CompletionDecision(
            accepted=False,
            phase=RunPhase.WAITING_SYSTEM,
            reason_codes=("external_dependency_temporarily_unavailable",),
            resolution="waiting_system",
            continuation_required=True,
            observation={
                "kind": "completion_waiting_system",
                "retryable": True,
                "unmet_conditions": ["external_dependency_temporarily_unavailable"],
            },
        )

    async def forbidden_finalize(*_args, **_kwargs):
        raise AssertionError("waiting_system must not finalize the Run")

    monkeypatch.setattr(completion, "verify_run_completion", fake_verify)
    monkeypatch.setattr(task_run_service, "finalize_run", forbidden_finalize)

    async def collect():
        channel = SSEChannel(HARNESS, "thread-1", "run-1")
        return [
            frame async for frame in turn_finalizer.finalize_terminal(
                channel, "run-1", {}, None, "dependency unavailable", "error",
            )
        ]

    with pytest.raises(turn_finalizer.CompletionWaitingSystem):
        asyncio.run(collect())


def test_main_tool_turn_has_no_direct_success_terminal_bypass():
    forbidden_calls = []
    for relative in (
        "app/services/chat/main_tool_turn.py",
        "app/services/chat/plain_turn.py",
    ):
        tree = ast.parse(Path(relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in {"complete_run", "run_completed"}:
                forbidden_calls.append((relative, node.func.attr, node.lineno))
    assert forbidden_calls == []


def test_plain_text_user_clarification_does_not_complete_research():
    plan = PlanSnapshot(
        run_id="run-1",
        goal_revision=0,
        plan_version=1,
        goal="研究威少生涯数据",
        steps=(PlanStepSnapshot(
            step_id="step-1",
            order=0,
            title="检索生涯数据",
            status=PlanStepStatus.PENDING,
        ),),
        updated_at=datetime.utcnow(),
    )
    run = RunSnapshot(
        run_id="run-1",
        thread_id="thread-1",
        user_id="user-1",
        agent_mode=AgentMode.RESEARCH,
        phase=RunPhase.VERIFYING,
        state_version=1,
        goal_revision=0,
        plan_version=1,
        event_cursor=0,
        plan=plan,
        goal_contract={"deliverable": "研究报告", "success_criteria": ["写入我的文件"]},
    )
    summary = (
        "在正式开始系统检索前，我想确认一个会直接影响研究方向的问题："
        "你想研究威少生涯数据时，是否需要与特定球员做横向对比，"
        "还是纯粹聚焦威少本人的生涯数据梳理？"
    )
    decision = CompletionVerifier().verify(
        run,
        CompletionClaim(summary=summary, requires_citations=True),
        (),
    )
    assert decision.accepted is False
    assert decision.phase is RunPhase.VERIFYING
    assert decision.resolution == "continue"
    assert decision.continuation_required is True
    assert decision.reason_codes


def test_structured_pending_clarification_enters_waiting_user():
    decision = CompletionVerifier().verify(
        _run_with_step(
            PlanStepStatus.PENDING,
            pending_input={
                "kind": "clarification",
                "status": "waiting_user",
                "resume_id": "resume-1",
            },
        ),
        CompletionClaim(summary="还是要不要继续？"),
        (),
    )
    assert decision.accepted is False
    assert decision.phase is RunPhase.WAITING_CLARIFICATION
    assert decision.resolution == "waiting_user"
    assert decision.continuation_required is True
    assert decision.observation["source"] == "run_state.pending_input"


def test_structured_confirmation_enters_waiting_confirmation():
    decision = CompletionVerifier().verify(
        _run_with_step(
            PlanStepStatus.COMPLETED,
            pending_input={
                "kind": "plan_confirmation",
                "status": "waiting_confirmation",
                "plan_version": 3,
            },
        ),
        CompletionClaim(summary="计划已经准备好。"),
        (_successful_observation(),),
    )
    assert decision.phase is RunPhase.WAITING_CONFIRMATION
    assert decision.resolution == "waiting_user"


def test_verified_terminal_facts_are_the_only_failed_or_partial_path():
    failed = ToolObservation(
        call_id="gateway-failed",
        tool_name="external_check",
        status=ObservationStatus.FAILED,
        summary="external system rejected the operation",
        error_code="external_rejected",
        structured_data={
            "completion_fact": {
                "source": "gateway",
                "verified": True,
                "resolution": "failed",
                "no_viable_alternative": True,
                "reason_code": "external_rejected_no_alternative",
            },
        },
        evidence_refs=[{"system": "billing", "request_id": "req-1"}],
    )
    decision = CompletionVerifier().verify(
        _run_with_step(PlanStepStatus.COMPLETED),
        CompletionClaim(summary="已失败，请确认是否重试。"),
        (failed,),
    )
    assert decision.resolution == "failed"
    assert decision.phase is RunPhase.FAILED
    assert decision.terminal is True
    assert decision.verified is True

    partial = ToolObservation(
        call_id="gateway-partial",
        tool_name="batch_export",
        status=ObservationStatus.SUCCEEDED,
        summary="two of three requested records exported",
        structured_data={
            "completion_fact": {
                "source": "gateway",
                "verified": True,
                "resolution": "partial",
                "allowed_by_contract": True,
                "reason_code": "two_of_three_records_exported",
            },
        },
        artifact_refs=[{"id": "file-1", "filename": "partial.csv"}],
    )
    partial_decision = CompletionVerifier().verify(
        _run_with_step(PlanStepStatus.COMPLETED),
        CompletionClaim(summary="部分结果已经生成。"),
        (partial,),
    )
    assert partial_decision.resolution == "partial"
    assert partial_decision.phase is RunPhase.PARTIAL
    assert partial_decision.verified is True
