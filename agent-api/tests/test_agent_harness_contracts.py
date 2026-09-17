from datetime import datetime

import pytest
from pydantic import ValidationError

from app.services.agent_harness import (
    AgentMode,
    ApprovalPolicy,
    EffectScope,
    ExecutionProfileId,
    EventEnvelope,
    ObservationStatus,
    PlanSnapshot,
    PlanStepSnapshot,
    PlanStepStatus,
    RunPhase,
    RunSnapshot,
    ToolObservation,
    ToolSpec,
    get_profile,
    validate_event,
)


def test_profiles_are_data_driven_and_research_is_physically_read_only():
    standard = get_profile(AgentMode.STANDARD)
    plan = get_profile(AgentMode.PLAN)
    research = get_profile(AgentMode.RESEARCH)

    assert standard.initial_phase is RunPhase.EXECUTING
    assert plan.requires_initial_plan_confirmation is True
    assert plan.effect_scopes_for_phase(RunPhase.PLANNING) == {
        EffectScope.NONE, EffectScope.SCRATCH,
    }
    assert EffectScope.USER_FILES in plan.effect_scopes_for_phase(RunPhase.EXECUTING)
    assert research.auto_research_plan is True
    assert research.explicit_export_only is True
    assert research.effect_scopes_for_phase(RunPhase.EXECUTING) == {
        EffectScope.NONE, EffectScope.SCRATCH,
    }


def test_side_effecting_tool_cannot_claim_parallel_safety():
    with pytest.raises(ValidationError, match="parallel_safe"):
        ToolSpec(
            name="write_file",
            description="write",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            capability="files.write",
            effect_scope=EffectScope.USER_FILES,
            idempotent=False,
            parallel_safe=True,
            approval_policy=ApprovalPolicy.CONDITIONAL,
        )


def test_tool_specs_default_to_both_execution_profiles():
    spec = ToolSpec(
        name="read_file",
        description="read",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        capability="files.read",
    )
    assert spec.allowed_execution_profiles == frozenset(ExecutionProfileId)


def test_failed_observation_requires_machine_readable_error_code():
    with pytest.raises(ValidationError, match="error_code"):
        ToolObservation(
            call_id="call-1",
            tool_name="bash",
            status=ObservationStatus.FAILED,
            summary="failed",
        )


def test_plan_snapshot_has_single_in_progress_step_and_stable_ids():
    now = datetime.utcnow()
    with pytest.raises(ValidationError, match="at most one"):
        PlanSnapshot(
            run_id="run-1",
            goal_revision=1,
            plan_version=1,
            goal="deliver",
            updated_at=now,
            steps=(
                PlanStepSnapshot(step_id="a", order=0, title="A", status=PlanStepStatus.IN_PROGRESS),
                PlanStepSnapshot(step_id="b", order=1, title="B", status=PlanStepStatus.IN_PROGRESS),
            ),
        )


def test_run_and_embedded_plan_versions_cannot_diverge():
    plan = PlanSnapshot(
        run_id="run-1",
        goal_revision=2,
        plan_version=3,
        goal="deliver",
        updated_at=datetime.utcnow(),
        steps=(),
    )
    with pytest.raises(ValidationError, match="versions differ"):
        RunSnapshot(
            run_id="run-1",
            thread_id="thread-1",
            user_id="user-1",
            agent_mode=AgentMode.PLAN,
            phase=RunPhase.EXECUTING,
            state_version=4,
            goal_revision=2,
            plan_version=2,
            event_cursor=5,
            plan=plan,
        )


def test_event_catalog_rejects_unregistered_events():
    event = EventEnvelope(
        event_id="event-1",
        run_id="run-1",
        sequence=1,
        timestamp=datetime.utcnow(),
        type="run.accepted",
        data={"route": "agent"},
    )
    assert validate_event(event) is event
    with pytest.raises(ValueError, match="unknown Harness event"):
        validate_event(event.model_copy(update={"type": "invented.event"}))
