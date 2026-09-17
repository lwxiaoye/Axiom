"""Canonical, framework-neutral contracts for the main-chat Agent Harness."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class AgentMode(_StringEnum):
    STANDARD = "standard"
    PLAN = "plan"
    RESEARCH = "research"


class ExecutionProfileId(_StringEnum):
    INTERACTIVE = "interactive"
    ARTIFACT_CODING = "artifact_coding"


class RunPhase(_StringEnum):
    PLANNING = "planning"
    WAITING_CLARIFICATION = "waiting_clarification"
    WAITING_USER = "waiting_user"
    WAITING_SYSTEM = "waiting_system"
    PLAN_READY = "plan_ready"
    WAITING_CONFIRMATION = "waiting_confirmation"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_RUN_PHASES = frozenset({
    RunPhase.COMPLETED,
    RunPhase.PARTIAL,
    RunPhase.FAILED,
    RunPhase.CANCELLED,
})


class PlanStepStatus(_StringEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    INVALIDATED = "invalidated"


class EffectScope(_StringEnum):
    NONE = "none"
    SCRATCH = "scratch"
    USER_FILES = "user_files"
    EXTERNAL = "external"
    MEMORY = "memory"


class ApprovalPolicy(_StringEnum):
    NEVER = "never"
    CONDITIONAL = "conditional"
    REQUIRED = "required"


class ObservationStatus(_StringEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: int = Field(default=1, ge=1, le=5)
    backoff_ms: int = Field(default=0, ge=0, le=60_000)
    retryable_error_codes: frozenset[str] = Field(default_factory=frozenset)


class ResultSizePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    inline_chars: int = Field(default=8_000, ge=256, le=64_000)
    # Token limits are opt-in during the observe-first rollout.  ``inline_chars`` remains the
    # compatibility boundary for tools that have not collected enough projection telemetry yet.
    inline_token_limit: int | None = Field(default=None, gt=0, le=128_000)
    persist_full_result: bool = True


class AppliedToolResultPolicy(BaseModel):
    """The immutable projection decision attached to one concrete tool result.

    Defaults may change between deploys.  A resumed Run must consume this recorded decision and
    the already-projected text instead of re-projecting an old result with the new defaults.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    inline_chars: int = Field(ge=256, le=64_000)
    inline_token_limit: int | None = Field(default=None, gt=0, le=128_000)
    persist_full_result: bool = True
    serialization_reserve_ratio: float = Field(default=0.20, ge=0.0, lt=1.0)
    raw_chars: int = Field(ge=0)
    raw_tokens: int = Field(ge=0)
    projected_chars: int = Field(ge=0)
    projected_tokens: int = Field(ge=0)
    truncated: bool = False
    full_available: bool = False
    result_handle: str | None = None
    content_hash: str = Field(default="", max_length=64)


class ToolSpec(BaseModel):
    """One source of truth for capability, side effects and execution policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    capability: str = Field(min_length=1, max_length=128)
    semantic_tags: frozenset[str] = Field(default_factory=frozenset)
    effect_scope: EffectScope = EffectScope.NONE
    idempotent: bool = True
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    timeout_seconds: float = Field(default=60.0, gt=0, le=3_600)
    cancellable: bool = True
    resource_locks: tuple[str, ...] = ()
    parallel_safe: bool = False
    approval_policy: ApprovalPolicy = ApprovalPolicy.NEVER
    visible_to_user: bool = True
    public_action: str = Field(default="完成当前操作", min_length=1, max_length=80)
    control_command: bool = False
    allowed_profiles: frozenset[AgentMode] = Field(
        default_factory=lambda: frozenset(AgentMode),
    )
    allowed_execution_profiles: frozenset[ExecutionProfileId] = Field(
        default_factory=lambda: frozenset(ExecutionProfileId),
    )
    allowed_phases: frozenset[RunPhase] = Field(
        default_factory=lambda: frozenset({RunPhase.EXECUTING}),
    )
    result_size_policy: ResultSizePolicy = Field(default_factory=ResultSizePolicy)

    @model_validator(mode="after")
    def validate_execution_policy(self) -> "ToolSpec":
        if self.effect_scope in {EffectScope.USER_FILES, EffectScope.EXTERNAL, EffectScope.MEMORY}:
            if self.parallel_safe:
                raise ValueError("side-effecting tools cannot be parallel_safe")
            if self.idempotent and not self.resource_locks:
                raise ValueError("idempotent side-effecting tools require a resource lock")
        if self.approval_policy is ApprovalPolicy.REQUIRED and self.effect_scope is EffectScope.NONE:
            raise ValueError("read-only tools must not require unconditional approval")
        if self.control_command and self.effect_scope is not EffectScope.NONE:
            raise ValueError("control commands cannot carry tool side effects")
        if self.control_command and "control" not in self.semantic_tags:
            raise ValueError("control commands require the control semantic tag")
        return self


class ToolCallContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    goal_revision: int = Field(ge=0)
    plan_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1)


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolObservation(BaseModel):
    """The only Action -> Observation boundary consumed by the Harness loop."""

    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    status: ObservationStatus
    summary: str = ""
    structured_data: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    receipts: list[dict[str, Any]] = Field(default_factory=list)
    retryable: bool = False
    error_code: str | None = None
    result_handle: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    plan_step_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_failure_shape(self) -> "ToolObservation":
        failed = self.status in {
            ObservationStatus.FAILED,
            ObservationStatus.CANCELLED,
            ObservationStatus.REJECTED,
        }
        if failed and not self.error_code:
            raise ValueError("non-success observations require error_code")
        if self.status is ObservationStatus.SUCCEEDED and self.error_code:
            raise ValueError("successful observations cannot carry error_code")
        return self


class PlanStepSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: str = Field(min_length=1, max_length=128)
    order: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=2_000)
    status: PlanStepStatus = PlanStepStatus.PENDING
    required: bool = True
    acceptance_criteria: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    reason: str = Field(default="", max_length=1_000)
    depends_on: tuple[str, ...] = Field(default_factory=tuple)
    requires: tuple[str, ...] = Field(default_factory=tuple)


class PlanSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    goal_revision: int = Field(ge=0)
    plan_version: int = Field(ge=1)
    goal: str = Field(min_length=1)
    steps: tuple[PlanStepSnapshot, ...]
    updated_at: datetime

    @model_validator(mode="after")
    def validate_steps(self) -> "PlanSnapshot":
        ids = [step.step_id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("plan step ids must be unique")
        orders = [step.order for step in self.steps]
        if len(orders) != len(set(orders)):
            raise ValueError("plan step order values must be unique")
        if sum(step.status is PlanStepStatus.IN_PROGRESS for step in self.steps) > 1:
            raise ValueError("a plan can have at most one in_progress step")
        return self


class RunSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    agent_mode: AgentMode
    phase: RunPhase
    capability_scope: str = Field(default="default", min_length=1, max_length=32)
    state_version: int = Field(ge=0)
    goal_revision: int = Field(ge=0)
    plan_version: int = Field(ge=0)
    event_cursor: int = Field(ge=0)
    active_tool_calls: tuple[str, ...] = ()
    pending_input: dict[str, Any] | None = None
    cancel_requested: bool = False
    terminal_reason: str | None = None
    execution_profile: dict[str, Any] | None = None
    goal_contract: dict[str, Any] | None = None
    # Internal lifecycle facts.  Older RunState JSON does not contain this field; the
    # RunStore supplies a normalized default so the public contract remains readable.
    execution_control: dict[str, Any] = Field(default_factory=dict)
    completion_observation: dict[str, Any] | None = None
    approved_plan_version: int | None = None
    plan: PlanSnapshot | None = None

    @model_validator(mode="after")
    def validate_terminal_state(self) -> "RunSnapshot":
        if self.phase in TERMINAL_RUN_PHASES and self.active_tool_calls:
            raise ValueError("terminal runs cannot have active tool calls")
        if self.plan is not None:
            if self.plan.run_id != self.run_id:
                raise ValueError("embedded plan belongs to a different run")
            if self.plan.plan_version != self.plan_version:
                raise ValueError("run and embedded plan versions differ")
            if self.plan.goal_revision != self.goal_revision:
                raise ValueError("run and embedded plan goal revisions differ")
        return self


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    timestamp: datetime
    schema_version: int = Field(default=1, ge=1)
    type: str = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)
