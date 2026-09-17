"""Profile and ToolSpec driven capability policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contracts import AgentMode, EffectScope, ExecutionProfileId, RunSnapshot, ToolSpec
from .profiles import get_profile


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason_code: str


class HarnessPolicy:
    def evaluate(
        self,
        spec: ToolSpec,
        run: RunSnapshot,
        *,
        export_authorized: bool = False,
    ) -> PolicyDecision:
        if run.agent_mode not in spec.allowed_profiles:
            return PolicyDecision(False, "profile_not_allowed")
        execution_profile = run.execution_profile
        if not isinstance(execution_profile, dict):
            return PolicyDecision(False, "execution_profile_unresolved")
        try:
            execution_profile_id = ExecutionProfileId(
                str(execution_profile.get("id") or "")
            )
        except ValueError:
            return PolicyDecision(False, "execution_profile_invalid")
        if execution_profile_id not in spec.allowed_execution_profiles:
            return PolicyDecision(False, "execution_profile_not_allowed")
        if run.phase not in spec.allowed_phases:
            return PolicyDecision(False, "phase_not_allowed")
        if run.capability_scope in {"inspect", "planning", "revision_pending"}:
            if spec.effect_scope in {
                EffectScope.USER_FILES,
                EffectScope.EXTERNAL,
                EffectScope.MEMORY,
            } or "artifact_producer" in spec.semantic_tags:
                return PolicyDecision(False, "capability_scope_read_only")
        if run.capability_scope == "revision":
            if spec.effect_scope in {EffectScope.EXTERNAL, EffectScope.MEMORY}:
                return PolicyDecision(False, "revision_scope_denied")
            if (
                spec.effect_scope is EffectScope.USER_FILES
                and "revision_targeted" not in spec.semantic_tags
            ):
                return PolicyDecision(False, "revision_target_required")
        scopes = set(get_profile(run.agent_mode).effect_scopes_for_phase(run.phase))
        if (
            run.agent_mode is AgentMode.RESEARCH
            and export_authorized
            and spec.capability == "artifact.export"
        ):
            scopes.add(EffectScope.USER_FILES)
        if spec.effect_scope not in scopes:
            return PolicyDecision(False, "effect_scope_not_allowed")
        return PolicyDecision(True, "allowed")

    def visible_specs(
        self,
        specs: Iterable[ToolSpec],
        run: RunSnapshot,
        *,
        export_authorized: bool = False,
    ) -> tuple[ToolSpec, ...]:
        return tuple(
            spec for spec in specs
            if self.evaluate(spec, run, export_authorized=export_authorized).allowed
        )
