"""Data-driven Harness profiles; mode differences must not fork the loop."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .contracts import AgentMode, EffectScope, RunPhase


class HarnessProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: AgentMode
    initial_phase: RunPhase
    requires_initial_plan_confirmation: bool = False
    auto_research_plan: bool = False
    persistent_scratch: bool = False
    explicit_export_only: bool = False
    max_model_rounds: int = Field(ge=1, le=100)
    max_wall_seconds: int = Field(ge=1, le=7_200)
    max_output_tokens: int = Field(ge=1)
    planning_effect_scopes: frozenset[EffectScope]
    execution_effect_scopes: frozenset[EffectScope]

    def effect_scopes_for_phase(self, phase: RunPhase) -> frozenset[EffectScope]:
        if self.mode is AgentMode.RESEARCH:
            return self.planning_effect_scopes
        if phase in {
            RunPhase.PLANNING,
            RunPhase.WAITING_CLARIFICATION,
            RunPhase.PLAN_READY,
            RunPhase.WAITING_CONFIRMATION,
        }:
            return self.planning_effect_scopes
        return self.execution_effect_scopes


_READ_ONLY = frozenset({EffectScope.NONE, EffectScope.SCRATCH})
_FULL = frozenset(EffectScope)

_PROFILES = {
    AgentMode.STANDARD: HarnessProfile(
        mode=AgentMode.STANDARD,
        initial_phase=RunPhase.EXECUTING,
        max_model_rounds=30,
        max_wall_seconds=900,
        max_output_tokens=150_000,
        planning_effect_scopes=_FULL,
        execution_effect_scopes=_FULL,
    ),
    AgentMode.PLAN: HarnessProfile(
        mode=AgentMode.PLAN,
        initial_phase=RunPhase.PLANNING,
        requires_initial_plan_confirmation=True,
        max_model_rounds=30,
        max_wall_seconds=900,
        max_output_tokens=150_000,
        planning_effect_scopes=_READ_ONLY,
        execution_effect_scopes=_FULL,
    ),
    AgentMode.RESEARCH: HarnessProfile(
        mode=AgentMode.RESEARCH,
        initial_phase=RunPhase.PLANNING,
        auto_research_plan=True,
        explicit_export_only=True,
        max_model_rounds=60,
        max_wall_seconds=1_800,
        max_output_tokens=250_000,
        planning_effect_scopes=_READ_ONLY,
        execution_effect_scopes=_READ_ONLY,
    ),
}


def get_profile(mode: AgentMode | str) -> HarnessProfile:
    return _PROFILES[AgentMode(mode)]
