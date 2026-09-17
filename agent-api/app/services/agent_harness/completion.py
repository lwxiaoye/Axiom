"""Evidence-based completion decisions; model completion text is only a claim."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import AgentMode, ObservationStatus, PlanStepStatus, RunPhase, RunSnapshot, ToolObservation


CompletionResolution = Literal[
    "continue",
    "waiting_user",
    "waiting_system",
    "completed",
    "partial",
    "failed",
]


_RETRYABLE_ERROR_CODES = frozenset({
    "timeout",
    "tool_timeout",
    "provider_timeout",
    "service_unavailable",
    "model_unavailable",
    "network_error",
    "connection_error",
    "approval_gateway_unavailable",
})
_COMPLETION_FACT_SOURCES = frozenset({
    "gateway",
    "policy",
    "external_system",
    "harness",
    "verifier",
})

class CompletionClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = ""
    requires_artifact: bool = False
    requires_citations: bool = False
    response_interrupted: bool = False


class CompletionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    phase: RunPhase
    reason_codes: tuple[str, ...] = ()
    # ``resolution`` is the verifier's structured decision.  It is deliberately separate from
    # the public RunPhase because a non-terminal ``continue``/wait must be expressible without
    # pretending that an interrupted model sentence is a terminal outcome.
    resolution: CompletionResolution = "completed"
    terminal: bool = False
    verified: bool = False
    continuation_required: bool = False
    observation: dict[str, Any] = Field(default_factory=dict)


def fold_research_coverage(
    observations: list[ToolObservation],
    state: dict[str, Any] | None,
    *,
    run_id: str,
) -> list[ToolObservation]:
    """Expose Research source coverage without inventing a persisted artifact."""
    blob = (
        state.get("research")
        if isinstance(state, dict) and isinstance(state.get("research"), dict)
        else {}
    )
    evidence_refs: list[dict[str, Any]] = []
    for item in blob.get("sources") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        evidence_refs.append({
            "url": url,
            "title": str(item.get("title") or url)[:400],
        })
        if len(evidence_refs) >= 16:
            break
    if not evidence_refs:
        return observations
    observations.append(ToolObservation(
        call_id=f"{run_id}-research-coverage",
        tool_name="search_web",
        status=ObservationStatus.SUCCEEDED,
        summary="research kernel coverage",
        evidence_refs=evidence_refs,
    ))
    return observations


class CompletionVerifier:
    @staticmethod
    def _unresolved_tool_failures(
        observations: tuple[ToolObservation, ...],
    ) -> list[ToolObservation]:
        """Return the latest unresolved failure for each tool capability.

        A later successful receipt for the same tool resolves its earlier attempt.  The complete
        audit trail stays intact, but only the latest unresolved failures influence the next
        verifier decision.
        """
        recovered_tools: set[str] = set()
        unresolved: list[ToolObservation] = []
        for item in reversed(observations):
            tool_name = item.tool_name.strip()
            if item.status is ObservationStatus.SUCCEEDED:
                recovered_tools.add(tool_name)
            elif tool_name not in recovered_tools:
                unresolved.append(item)
        return list(reversed(unresolved))

    @staticmethod
    def _has_unresolved_tool_failure(
        observations: tuple[ToolObservation, ...],
    ) -> bool:
        """A later success of the same capability resolves its earlier failed attempt.

        Completion still fails closed when the latest attempt for a tool failed, or when
        only a different tool later succeeded. This preserves the full audit trail without
        treating a repaired publish/validation attempt as a permanent Run failure.
        """
        return bool(CompletionVerifier._unresolved_tool_failures(observations))

    @staticmethod
    def _pending_user_fact(run: RunSnapshot) -> tuple[RunPhase, dict[str, Any]] | None:
        """Read an authoritative user-wait fact, never infer one from prose.

        ``pending_input`` is written by the HITL/authorization boundary.  A model sentence such
        as "还是要不要继续？" is only a claim and cannot create this fact.
        """
        pending = run.pending_input if isinstance(run.pending_input, dict) else {}
        if not pending:
            return None
        if any(pending.get(key) is True for key in ("resolved", "answered", "consumed", "confirmed")):
            return None
        kind = str(pending.get("kind") or pending.get("type") or "").strip().lower()
        status = str(pending.get("status") or "").strip().lower()
        authorization = pending.get("authorization")
        confirmation = pending.get("confirmation")
        authorization_wait = isinstance(authorization, dict) and not bool(
            authorization.get("granted") is True or authorization.get("authorized") is True
        )
        confirmation_wait = isinstance(confirmation, dict) and not bool(
            confirmation.get("confirmed") is True or confirmation.get("approved") is True
        )
        known_wait_kind = kind in {
            "clarification",
            "user_input",
            "user_questions",
            "approval",
            "authorization",
            "confirmation",
            "plan_confirmation",
        }
        if not (known_wait_kind or status in {"waiting_user", "waiting_confirmation", "needs_input"}
                or authorization_wait or confirmation_wait):
            return None
        is_confirmation = (
            kind in {"approval", "authorization", "confirmation", "plan_confirmation"}
            or status == "waiting_confirmation"
            or authorization_wait
            or confirmation_wait
        )
        phase = RunPhase.WAITING_CONFIRMATION if is_confirmation else RunPhase.WAITING_CLARIFICATION
        fact = {
            "kind": "user_input_required",
            "source": "run_state.pending_input",
            "wait_kind": "confirmation" if is_confirmation else "clarification",
            "pending_kind": kind or None,
            "question_id": str(pending.get("question_id") or pending.get("resume_id") or "")[:160],
        }
        return phase, fact

    @staticmethod
    def _observation_user_wait(
        observations: tuple[ToolObservation, ...],
    ) -> dict[str, Any] | None:
        """Recognize structured HITL/approval receipts from the tool boundary."""
        for item in reversed(observations):
            data = item.structured_data if isinstance(item.structured_data, dict) else {}
            raw_status = str(data.get("tool_result_status") or data.get("status") or "").lower()
            if item.tool_name == "ask_user_choice" and (
                raw_status in {"needs_input", "waiting_user", "waiting_confirmation"}
                or item.status is ObservationStatus.REJECTED
            ):
                return {
                    "kind": "user_input_required",
                    "source": "tool_observation",
                    "tool_name": item.tool_name,
                    "wait_kind": "clarification",
                    "call_id": item.call_id,
                }
            if item.error_code in {"approval_required", "authorization_required"}:
                return {
                    "kind": "authorization_required",
                    "source": "tool_observation",
                    "tool_name": item.tool_name,
                    "wait_kind": "confirmation",
                    "call_id": item.call_id,
                    "error_code": item.error_code,
                }
        return None

    @staticmethod
    def _completion_fact(item: ToolObservation) -> dict[str, Any] | None:
        """Extract only a server/tool-produced completion fact.

        The model can describe a desired outcome, but it cannot manufacture this envelope.  The
        source and ``verified`` bit are required so a natural-language stop explanation remains a
        claim rather than becoming a failure/partial terminal decision.
        """
        data = item.structured_data if isinstance(item.structured_data, dict) else {}
        for key in ("completion_fact", "terminal_fact", "completion"):
            candidate = data.get(key)
            if isinstance(candidate, dict):
                source = str(candidate.get("source") or "").strip().lower()
                outcome = str(candidate.get("resolution") or candidate.get("outcome") or "").strip().lower()
                if bool(candidate.get("verified")) and source in _COMPLETION_FACT_SOURCES and outcome:
                    return {**candidate, "source": source, "resolution": outcome}
        return None

    def _verified_terminal_decision(
        self,
        run: RunSnapshot,
        observations: tuple[ToolObservation, ...],
    ) -> CompletionDecision | None:
        """Turn an independently produced completion fact into a terminal decision."""
        for item in reversed(observations):
            fact = self._completion_fact(item)
            if fact is None:
                continue
            outcome = str(fact.get("resolution") or "").lower()
            if outcome not in {"failed", "partial", "waiting_system"}:
                continue
            fact_revision = fact.get("goal_revision")
            if fact_revision is not None and str(fact_revision) != str(run.goal_revision):
                continue
            evidence = fact.get("evidence")
            has_evidence = bool(
                (isinstance(evidence, list) and evidence)
                or item.evidence_refs
                or item.artifact_refs
                or item.receipts
            )
            if outcome == "waiting_system":
                if not bool(fact.get("retryable") or fact.get("temporary")):
                    continue
                return CompletionDecision(
                    accepted=False,
                    phase=RunPhase.WAITING_SYSTEM,
                    reason_codes=(str(fact.get("reason_code") or "external_dependency_temporarily_unavailable"),),
                    resolution="waiting_system",
                    continuation_required=True,
                    observation={
                        "kind": "completion_waiting_system",
                        "source": fact["source"],
                        "retryable": True,
                        "reason_codes": [str(fact.get("reason_code") or "external_dependency_temporarily_unavailable")],
                        "evidence": self._bounded_fact_evidence(item, fact),
                    },
                )
            if outcome == "failed":
                if not bool(fact.get("no_viable_alternative")) or not has_evidence:
                    continue
                reason = str(fact.get("reason_code") or "no_viable_alternative")
                return CompletionDecision(
                    accepted=False,
                    phase=RunPhase.FAILED,
                    reason_codes=(reason,),
                    resolution="failed",
                    terminal=True,
                    verified=True,
                    observation={
                        "kind": "completion_verification_terminal",
                        "resolution": "failed",
                        "source": fact["source"],
                        "verified": True,
                        "unmet_conditions": [reason],
                        "evidence": self._bounded_fact_evidence(item, fact),
                    },
                )
            if outcome == "partial":
                if not bool(fact.get("allowed_by_contract")) or not has_evidence:
                    continue
                reason = str(fact.get("reason_code") or "verified_partial_evidence")
                return CompletionDecision(
                    accepted=False,
                    phase=RunPhase.PARTIAL,
                    reason_codes=(reason,),
                    resolution="partial",
                    terminal=True,
                    verified=True,
                    observation={
                        "kind": "completion_verification_terminal",
                        "resolution": "partial",
                        "source": fact["source"],
                        "verified": True,
                        "unmet_conditions": [reason],
                        "evidence": self._bounded_fact_evidence(item, fact),
                    },
                )
        return None

    @staticmethod
    def _bounded_fact_evidence(
        item: ToolObservation,
        fact: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows = fact.get("evidence") if isinstance(fact.get("evidence"), list) else []
        if not rows:
            rows = [
                *item.evidence_refs[:4],
                *item.artifact_refs[:4],
                *item.receipts[:4],
            ]
        return [row for row in rows if isinstance(row, dict)][:8]

    @staticmethod
    def _artifact_id(value: object) -> str:
        if not isinstance(value, dict):
            return ""
        return str(value.get("file_id") or value.get("id") or "").strip()

    @staticmethod
    def _artifact_filename(value: object) -> str:
        if not isinstance(value, dict):
            return ""
        return str(value.get("filename") or value.get("name") or "").strip()

    def _has_persisted_artifact(
        self,
        observations: tuple[ToolObservation, ...],
    ) -> bool:
        return any(
            item.status is ObservationStatus.SUCCEEDED
            and any(
                self._artifact_id(artifact) and self._artifact_filename(artifact)
                for artifact in item.artifact_refs
            )
            for item in observations
        )

    def _presentation_artifact_reasons(
        self,
        observations: tuple[ToolObservation, ...],
    ) -> list[str]:
        publishes = [
            item for item in observations
            if item.tool_name == "publish_ppt_artifact"
            and item.status is ObservationStatus.SUCCEEDED
        ]
        if not publishes:
            return ["presentation_publish_receipt_missing"]

        for item in publishes:
            rows = [row for row in item.artifact_refs if isinstance(row, dict)]
            pptx = [row for row in rows if self._artifact_filename(row).lower().endswith(".pptx")]
            if any(self._artifact_id(row) for row in pptx):
                return []

        latest = publishes[-1]
        rows = [row for row in latest.artifact_refs if isinstance(row, dict)]
        pptx = [row for row in rows if self._artifact_filename(row).lower().endswith(".pptx")]
        reasons: list[str] = []
        if not pptx:
            reasons.append("presentation_pptx_receipt_missing")
        if pptx and not any(self._artifact_id(row) for row in pptx):
            reasons.append("presentation_artifact_file_id_missing")
        return reasons or ["presentation_publish_receipt_invalid"]

    def verify(
        self,
        run: RunSnapshot,
        claim: CompletionClaim,
        observations: tuple[ToolObservation, ...],
        research_ledger: Any = None,
        research_report_review: dict[str, Any] | None = None,
    ) -> CompletionDecision:
        # Only server-side pending-input/approval facts may put a Run into a user wait.  A
        # question mark or Chinese choice word in ``claim.summary`` is deliberately ignored.
        pending_user = self._pending_user_fact(run)
        observation_user = self._observation_user_wait(observations)
        if pending_user is not None or observation_user is not None:
            phase, fact = pending_user or (
                RunPhase.WAITING_CONFIRMATION
                if observation_user and observation_user.get("wait_kind") == "confirmation"
                else RunPhase.WAITING_CLARIFICATION,
                observation_user or {},
            )
            return CompletionDecision(
                accepted=False,
                phase=phase,
                reason_codes=("structured_user_input_required",),
                resolution="waiting_user",
                continuation_required=True,
                observation={
                    **fact,
                    "kind": "completion_waiting_user",
                    "reason_codes": ["structured_user_input_required"],
                    "unmet_conditions": ["structured_user_input_required"],
                },
            )

        verified_terminal = self._verified_terminal_decision(run, observations)
        if verified_terminal is not None:
            return verified_terminal

        unresolved_failures = self._unresolved_tool_failures(observations)
        review = research_report_review or {}
        report_reviewed = (
            run.agent_mode is AgentMode.RESEARCH
            and not claim.response_interrupted
            and review.get("goalRevision") == run.goal_revision
            and (review.get("decision") or {}).get("verdict") in {"accept", "edit"}
            and bool(str(review.get("answer") or "").strip())
            and str(review["answer"]).strip() == claim.summary.strip()
        )
        budget_partial = (
            run.agent_mode is AgentMode.RESEARCH
            and getattr(research_ledger, "coverage_outcome", "") == "partial"
            and "research_report_budget_partial" in (getattr(research_ledger, "reason_codes", None) or [])
        )
        if unresolved_failures and not budget_partial and not report_reviewed and all(
            item.retryable or item.error_code in _RETRYABLE_ERROR_CODES
            for item in unresolved_failures
        ):
            codes = [str(item.error_code or "external_dependency_temporarily_unavailable")
                     for item in unresolved_failures[-4:]]
            return CompletionDecision(
                accepted=False,
                phase=RunPhase.WAITING_SYSTEM,
                reason_codes=("external_dependency_temporarily_unavailable",),
                resolution="waiting_system",
                continuation_required=True,
                observation={
                    "kind": "completion_waiting_system",
                    "source": "tool_observation",
                    "retryable": True,
                    "reason_codes": ["external_dependency_temporarily_unavailable"],
                    "error_codes": codes,
                    "unmet_conditions": ["external_dependency_temporarily_unavailable"],
                    "evidence": [
                        {
                            "call_id": item.call_id,
                            "tool_name": item.tool_name,
                            "status": item.status.value,
                            "error_code": item.error_code,
                            "summary": item.summary[:240],
                        }
                        for item in unresolved_failures[-4:]
                    ],
                },
            )

        # Research kernel owns the bounded coverage pass. When that pass exhausts its safety
        # budget, a model-written report may still be useful, but the platform must publish a
        # truthful partial terminal instead of letting the generic Codex stop boundary coerce the
        # remaining evidence gaps to completed. A later successful search clears this marker.
        if (
            run.agent_mode is AgentMode.RESEARCH
            and research_ledger is not None
            and str(getattr(research_ledger, "coverage_outcome", "") or "") == "partial"
        ):
            codes = tuple(
                str(item)[:160]
                for item in (getattr(research_ledger, "reason_codes", None) or [])
                if str(item).strip()
            ) or ("research_coverage_partial",)
            source_rows = []
            for item in list(getattr(research_ledger, "sources", None) or [])[:8]:
                url = str(getattr(item, "url", "") or "")
                if url.startswith("http"):
                    source_rows.append({
                        "url": url,
                        "title": str(getattr(item, "title", "") or url)[:400],
                    })
            return CompletionDecision(
                accepted=False,
                phase=RunPhase.COMPLETED,
                reason_codes=codes,
                resolution="partial",
                terminal=True,
                verified=True,
                observation={
                    "kind": "research_coverage_partial",
                    "source": "research_kernel",
                    "verified": True,
                    "unmet_conditions": list(codes),
                    "evidence": source_rows,
                },
            )

        if report_reviewed:
            return CompletionDecision(
                accepted=True, phase=RunPhase.COMPLETED, resolution="completed",
                terminal=True, verified=True,
                observation={"kind": "research_report_reviewed", "source": "research_kernel", "verified": True},
            )

        reasons: list[str] = []
        if run.plan is not None:
            incomplete = [
                step for step in run.plan.steps
                if step.required and (
                    step.status not in {
                        PlanStepStatus.COMPLETED,
                        PlanStepStatus.SKIPPED,
                        PlanStepStatus.INVALIDATED,
                    }
                    or (
                        step.status in {
                            PlanStepStatus.SKIPPED,
                            PlanStepStatus.INVALIDATED,
                        }
                        and not step.reason.strip()
                    )
                )
            ]
            if incomplete:
                reasons.append("required_plan_steps_incomplete")
        if self._has_unresolved_tool_failure(observations):
            reasons.append("tool_failure_unresolved")
        execution_profile = run.execution_profile or {}
        artifact_coding = execution_profile.get("id") == "artifact_coding"
        if artifact_coding and execution_profile.get("artifact_kind") == "presentation":
            reasons.extend(self._presentation_artifact_reasons(observations))
        elif (claim.requires_artifact or artifact_coding) and not self._has_persisted_artifact(
            observations,
        ):
            reasons.append("artifact_evidence_missing")
        if claim.requires_citations and not any(item.evidence_refs for item in observations):
            ledger_urls = []
            if research_ledger is not None:
                unique = getattr(research_ledger, "unique_urls", None)
                ledger_urls = list(unique()) if callable(unique) else []
            if not ledger_urls:
                reasons.append("citation_evidence_missing")
        if claim.response_interrupted:
            reasons.append("final_response_interrupted")
        reasons.extend(self._goal_contract_reasons(run, observations))
        reasons.extend(self._research_reasons(run, observations, research_ledger))
        if reasons:
            return CompletionDecision(
                accepted=False,
                phase=RunPhase.VERIFYING,
                reason_codes=tuple(reasons),
                resolution="continue",
                continuation_required=True,
                observation=self._gap_observation(run, claim, observations, reasons),
            )
        return CompletionDecision(
            accepted=True,
            phase=RunPhase.COMPLETED,
            resolution="completed",
            terminal=True,
            verified=True,
        )

    @staticmethod
    def _gap_observation(
        run: RunSnapshot,
        claim: CompletionClaim,
        observations: tuple[ToolObservation, ...],
        reasons: list[str],
    ) -> dict[str, Any]:
        """Build the bounded fact payload fed back to the next segment.

        This is evidence, not a model instruction: the next segment receives the unmet
        conditions and the receipts already observed, while the Run remains non-terminal.
        """
        evidence: list[dict[str, Any]] = []
        for item in observations[-32:]:
            evidence.append({
                "call_id": item.call_id,
                "tool_name": item.tool_name,
                "status": item.status.value,
                "summary": item.summary[:1_000],
                "error_code": item.error_code,
                "result_handle": item.result_handle,
                "plan_step_id": item.plan_step_id,
                "evidence_refs": item.evidence_refs[:8],
                "artifact_refs": item.artifact_refs[:8],
                "receipts": item.receipts[:8],
            })
        return {
            "kind": "completion_verification_gap",
            "action": "continue_same_goal",
            "goal_revision": run.goal_revision,
            "plan_version": run.plan_version,
            "unmet_conditions": list(reasons),
            "reason_codes": list(reasons),
            "claim": {
                "summary": claim.summary[:2_000],
                "requires_artifact": claim.requires_artifact,
                "requires_citations": claim.requires_citations,
                "response_interrupted": claim.response_interrupted,
            },
            "claim_is_unverified": True,
            "evidence": evidence,
        }

    def _goal_contract_reasons(
        self,
        run: RunSnapshot,
        observations: tuple[ToolObservation, ...],
    ) -> list[str]:
        contract = run.goal_contract if isinstance(run.goal_contract, dict) else {}
        if not contract:
            return []
        deliverable = str(contract.get("deliverable") or "")
        criteria = [str(item) for item in (contract.get("success_criteria") or []) if str(item).strip()]
        reasons: list[str] = []
        wants_file = deliverable.startswith("文件") or any(
            any(token in item for token in ("文件", "保存", "交付", "写入我的文件"))
            for item in criteria
        )
        if wants_file and "对话" not in deliverable and not self._has_persisted_artifact(observations):
            reasons.append("goal_contract_artifact_unmet")
        if deliverable.startswith("修改既有"):
            mutated = any(
                item.status is ObservationStatus.SUCCEEDED
                and item.tool_name in {
                    "write_file", "edit_file", "bash", "publish_ppt_artifact",
                }
                for item in observations
            )
            if not mutated:
                reasons.append("goal_contract_revision_unmet")
        if any(any(token in item for token in ("引用", "来源", "链接")) for item in criteria):
            searched = any(
                item.tool_name in {"search_web", "browser_fetch"}
                and item.status is ObservationStatus.SUCCEEDED
                for item in observations
            )
            cited = any(item.evidence_refs for item in observations)
            if searched and not cited:
                reasons.append("goal_contract_citation_unmet")
        return reasons

    def _research_reasons(
        self,
        run: RunSnapshot,
        observations: tuple[ToolObservation, ...],
        research_ledger: Any = None,
    ) -> list[str]:
        from app.services.agent_harness.research.engine import completion_reasons

        return completion_reasons(run, observations, research_ledger)


async def verify_run_completion(run_id: str, claim: CompletionClaim) -> CompletionDecision:
    """Read HITL / wait facts at the terminal boundary.

    Callers must not feed ``resolution=continue`` back into the same Loop or patch
    ``phase=verifying``. Codex: an assistant message without tool calls completes.
    """
    from sqlalchemy import select

    from app.core.runtime_db import runtime_session
    from app.runtime_models import AgentRun, AgentToolCall
    from app.services.agent_harness.plan_store import get_plan_snapshot
    from app.services.agent_harness.run_store import get_run_snapshot

    run = await get_run_snapshot(run_id)
    if run is None:
        return CompletionDecision(
            accepted=False,
            phase=RunPhase.VERIFYING,
            reason_codes=("run_snapshot_temporarily_unavailable",),
            resolution="waiting_system",
            continuation_required=True,
            observation={
                "kind": "completion_verification_dependency_unavailable",
                "action": "retry_same_goal",
                "unmet_conditions": ["run_snapshot_temporarily_unavailable"],
                "reason_codes": ["run_snapshot_temporarily_unavailable"],
            },
        )
    plan = await get_plan_snapshot(run_id)
    if plan is not None:
        run = run.model_copy(update={"plan": plan, "plan_version": plan.plan_version})

    observations: list[ToolObservation] = []
    run_state: dict[str, Any] = {}
    factory = runtime_session()
    if factory is not None:
        async with factory() as session:
            rows = (
                await session.execute(
                    select(AgentToolCall)
                    .where(AgentToolCall.run_id == run_id)
                    .order_by(AgentToolCall.created_at.asc())
                )
            ).scalars().all()
            run_row = await session.get(AgentRun, run_id)
            run_state = dict(run_row.state or {}) if run_row is not None else {}
        for row in rows:
            raw = dict(row.observation or {})
            try:
                observations.append(ToolObservation.model_validate(raw))
            except Exception:  # noqa: BLE001 - malformed persisted evidence must fail closed
                observations.append(ToolObservation(
                    call_id=str(row.id),
                    tool_name=str(row.name),
                    status=ObservationStatus.FAILED,
                    summary="persisted observation does not match the Harness contract",
                    error_code="observation_contract_invalid",
                ))
        observations = fold_research_coverage(
            observations, run_state, run_id=run_id,
        )
    research_blob = run_state.get("research") if isinstance(run_state.get("research"), dict) else None
    research_ledger = None
    if research_blob is not None:
        from app.services.agent_harness.research.report import ledger_from_state

        research_ledger = ledger_from_state(research_blob)
    return CompletionVerifier().verify(
        run, claim, tuple(observations), research_ledger=research_ledger,
        research_report_review=run_state.get("research_report_review"),
    )
