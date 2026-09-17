"""Aggregate one structured death record at every Run terminal boundary.

Cause-of-death facts used to live in logs, agent_steps and Run columns separately.
Operators could not explain a single "it just stopped" report. This module is the
one-line aggregation; it prefers logging over schema changes.
"""
from __future__ import annotations

import json
import logging
import traceback
from typing import Any, Optional

logger = logging.getLogger(__name__)

REPORT_LOG_EVENT = "run_terminal_report"
REPORT_SCHEMA_VERSION = 2


def _bounded_rows(value: Any, *, limit: int = 8, text_limit: int = 240) -> list[dict[str, Any]]:
    """Keep report/context evidence small and JSON-shaped."""
    if not isinstance(value, (list, tuple)):
        return []
    rows: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        row: dict[str, Any] = {}
        for key in ("call_id", "tool_name", "status", "error_code", "id", "file_id", "filename", "url"):
            if raw.get(key) not in (None, ""):
                row[key] = str(raw[key])[:text_limit]
        if raw.get("summary"):
            row["summary"] = str(raw["summary"])[:text_limit]
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def compact_terminal_fact(raw: Any) -> dict[str, Any] | None:
    """Project a current or legacy terminal report into a short, fact-labelled summary.

    The projection intentionally ignores free-form model text.  Historical reports without the
    v2 ``facts`` envelope remain readable, but are marked ``legacy_terminal_report`` rather than
    being promoted to newly verified evidence.
    """
    if not isinstance(raw, dict):
        return None
    fact = raw.get("facts") if isinstance(raw.get("facts"), dict) else raw
    resolution = str(
        fact.get("resolution")
        or raw.get("run_disposition")
        or raw.get("phase")
        or ""
    ).strip()
    source = str(fact.get("source") or "legacy_terminal_report").strip() or "legacy_terminal_report"
    reason = str(fact.get("reason") or raw.get("terminal_reason") or "").strip()
    if source == "model":
        reason = ""
    reasons = fact.get("reason_codes") or fact.get("unmet_conditions") or (
        [fact.get("reason_code")] if fact.get("reason_code") else []
    )
    if not isinstance(reasons, (list, tuple)):
        reasons = [reasons]
    evidence = fact.get("existing_evidence") or fact.get("evidence") or []
    unmet = fact.get("unmet_conditions") or []
    if not isinstance(unmet, (list, tuple)):
        unmet = [unmet]
    return {
        "source": source,
        "verified": bool(fact.get("verified")) if "verified" in fact else False,
        "resolution": resolution,
        "reason": reason[:500],
        "reason_codes": [str(item)[:160] for item in reasons if str(item).strip()][:8],
        "existing_evidence": _bounded_rows(evidence),
        "unmet_conditions": [str(item)[:240] for item in unmet if str(item).strip()][:8],
    }


def _run_state_terminal_projection(report: dict[str, Any]) -> dict[str, Any]:
    fact = compact_terminal_fact(report) or {
        "source": "harness",
        "verified": False,
        "resolution": "",
        "reason": "",
        "reason_codes": [],
        "existing_evidence": [],
        "unmet_conditions": [],
    }
    return {"kind": "run_terminal_report", **fact}


def attach_plan_titles(identity: dict[str, Any], titles: Any) -> dict[str, Any]:
    """Copy human plan titles onto the terminal-report identity for Skill drafts."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in titles or []:
        if isinstance(item, dict):
            text = str(item.get("title") or item.get("description") or "").strip()
        else:
            text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
        if len(cleaned) >= 8:
            break
    if cleaned:
        identity["plan_titles"] = cleaned
    return identity


def build_run_terminal_report(
    *,
    run_id: str,
    phase: str = "",
    terminal_reason: str = "",
    run_disposition: str = "",
    loop: Optional[dict[str, Any]] = None,
    trail: Optional[dict[str, Any]] = None,
    exception: Optional[dict[str, Any]] = None,
    resume: Optional[dict[str, Any]] = None,
    extra: Optional[dict[str, Any]] = None,
    fact_source: str = "harness",
    verified: bool = False,
    reason_code: str = "",
    evidence: Optional[list[dict[str, Any]]] = None,
    unmet_conditions: Optional[list[str]] = None,
    model_claim: str = "",
) -> dict[str, Any]:
    source = str(fact_source or "harness").strip()[:80] or "harness"
    resolution = str(run_disposition or phase or "").strip()[:80]
    safe_reason = "" if source == "model" else str(terminal_reason or "").strip()[:500]
    if not safe_reason and source != "model":
        safe_reason = {
            "completed": "completion_verified",
            "cancelled": "user_cancelled",
            "waiting_user": "structured_user_input_required",
            "waiting_confirmation": "structured_confirmation_required",
            "waiting_system": "external_dependency_temporarily_unavailable",
        }.get(resolution, "")
    extra_payload = dict(extra or {})
    verification = extra_payload.get("completion_verification")
    if not isinstance(verification, dict):
        verification = {}
    evidence_rows = evidence if evidence is not None else (
        verification.get("evidence") or verification.get("existing_evidence") or []
    )
    unmet = unmet_conditions if unmet_conditions is not None else (
        verification.get("unmet_conditions") or verification.get("reason_codes") or []
    )
    report = {
        "event": REPORT_LOG_EVENT,
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": str(run_id or ""),
        "phase": str(phase or ""),
        # Kept for old readers; the authoritative new value is facts.reason.  Never fall back to
        # a model answer here: a claim is not a stop reason.
        "terminal_reason": safe_reason,
        "run_disposition": str(run_disposition or ""),
        "loop": dict(loop or {}),
        "trail": dict(trail or {}),
        "exception": dict(exception or {}),
        "resume": dict(resume or {}),
        "facts": {
            "source": source,
            "verified": bool(verified),
            "resolution": resolution,
            "reason_code": str(reason_code or "")[:160],
            "reason": safe_reason,
            "reason_codes": [str(item)[:160] for item in unmet if str(item).strip()][:8],
            "existing_evidence": _bounded_rows(evidence_rows),
            "unmet_conditions": [str(item)[:240] for item in unmet if str(item).strip()][:8],
        },
    }
    if model_claim:
        # Preserve the claim for audit/debugging, but keep it outside ``facts`` so it cannot be
        # re-injected as an authoritative stop reason on the next segment.
        report["model_claim"] = str(model_claim)[:2_000]
    if extra_payload:
        report["extra"] = extra_payload
    return report


def exception_brief(exc: BaseException | None) -> dict[str, Any]:
    if exc is None:
        return {}
    tb = traceback.format_exception_only(type(exc), exc)
    first = (tb[0] if tb else f"{type(exc).__name__}: {exc}").strip()
    return {
        "type": type(exc).__name__,
        "message": str(exc)[:300],
        "first_line": first[:300],
    }


def log_run_terminal_report(report: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(report, ensure_ascii=False, default=str)
    logger.info("%s %s", REPORT_LOG_EVENT, payload)
    return report


async def emit_run_terminal_report(
    run_id: str,
    *,
    phase: str = "",
    terminal_reason: str = "",
    run_disposition: str = "",
    loop: Optional[dict[str, Any]] = None,
    exception: Optional[BaseException] = None,
    resume_flag: Optional[bool] = None,
    extra: Optional[dict[str, Any]] = None,
    fact_source: str = "harness",
    verified: bool = False,
    reason_code: str = "",
    evidence: Optional[list[dict[str, Any]]] = None,
    unmet_conditions: Optional[list[str]] = None,
    model_claim: str = "",
) -> dict[str, Any]:
    """Build, persist and log a structured report. Fail-open: never raise.

    The log remains useful for operators, but the compact ``completion_observation`` projection
    is also committed to RunState so a later segment can read the real reason/evidence without
    trusting a model-authored explanation.
    """
    try:
        trail, resume_info, loop_from_state, contract_invalid, identity = await _enrich_from_runtime(run_id)
        merged_loop = dict(loop_from_state)
        merged_loop.update(loop or {})
        if contract_invalid:
            merged_loop["observation_contract_invalid"] = contract_invalid
        resume = dict(resume_info)
        if resume_flag is not None:
            resume["resumed"] = bool(resume_flag)
        merged_extra = dict(identity)
        merged_extra.update(extra or {})
        report = build_run_terminal_report(
            run_id=run_id,
            phase=phase,
            terminal_reason=terminal_reason,
            run_disposition=run_disposition,
            loop=merged_loop,
            trail=trail,
            exception=exception_brief(exception),
            resume=resume,
            extra=merged_extra,
            fact_source=fact_source,
            verified=verified,
            reason_code=reason_code,
            evidence=evidence,
            unmet_conditions=unmet_conditions,
            model_claim=model_claim,
        )
        log_run_terminal_report(report)
        await _persist_run_state_fact(run_id, report)
        try:
            from app.services.agent_harness import task_lesson
            await task_lesson.maybe_store_from_report(report)
        except Exception:  # noqa: BLE001
            logger.debug("task lesson persist skipped", exc_info=True)
        try:
            from app.services.agent_harness import skill_draft
            await skill_draft.maybe_collect_from_report(report)
        except Exception:  # noqa: BLE001
            logger.debug("skill draft persist skipped", exc_info=True)
        return report
    except Exception:  # noqa: BLE001
        logger.warning("run_terminal_report emit failed run=%s", run_id, exc_info=True)
        return build_run_terminal_report(
            run_id=run_id,
            phase=phase,
            terminal_reason=terminal_reason,
            run_disposition=run_disposition,
            exception=exception_brief(exception),
            fact_source=fact_source,
            verified=verified,
            reason_code=reason_code,
            evidence=evidence,
            unmet_conditions=unmet_conditions,
            model_claim=model_claim,
        )


async def _persist_run_state_fact(run_id: str, report: dict[str, Any]) -> None:
    """Persist a bounded report plus its model-safe projection in the existing RunState JSON."""
    if not str(run_id or "").strip():
        return
    try:
        from app.services.agent_harness import run_store

        # JSONB drivers reject arbitrary Python objects in legacy ``extra`` payloads.  The report
        # is an audit record, so stringifying those rare values is safer than dropping the fact.
        safe_report = json.loads(json.dumps(report, ensure_ascii=False, default=str))
        await run_store.patch_run_state(
            str(run_id),
            {
                "terminal_report": safe_report,
                # RunSnapshot already projects completion_observation.  Keeping this compact
                # compatibility projection avoids changing the recovery Job/RunStore schema and
                # lets old/new Context Compilers consume the same RunState key.
                "completion_observation": _run_state_terminal_projection(safe_report),
            },
        )
    except Exception:  # noqa: BLE001 - reporting must never break the terminal path
        logger.warning("run_terminal_report RunState persist skipped run=%s", run_id, exc_info=True)


async def _enrich_from_runtime(
    run_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], int, dict[str, Any]]:
    trail: dict[str, Any] = {"event_types": [], "last_tool": {}}
    resume: dict[str, Any] = {"resumed": False, "reclaimed": False}
    loop_state: dict[str, Any] = {}
    identity: dict[str, Any] = {}
    contract_invalid = 0
    if not run_id:
        return trail, resume, loop_state, contract_invalid, identity
    from app.core.runtime_db import runtime_session
    factory = runtime_session()
    if factory is None:
        return trail, resume, loop_state, contract_invalid, identity
    from sqlalchemy import select
    from app.runtime_models import AgentPlan, AgentPlanStep, AgentRun, AgentRunEvent, AgentToolCall

    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        if run is not None:
            identity = {
                "user_id": str(run.user_id or ""),
                "thread_id": str(run.thread_id or ""),
                "goal": str(run.goal or ""),
            }
            state = dict(run.state or {})
            contract = state.get("goal_contract") if isinstance(state.get("goal_contract"), dict) else {}
            if contract.get("deliverable"):
                identity["deliverable"] = str(contract.get("deliverable") or "")
            if contract.get("goal"):
                identity["goal"] = str(contract.get("goal") or identity.get("goal") or "")
            safety = state.get("loop_safety") if isinstance(state.get("loop_safety"), dict) else {}
            control = (
                state.get("execution_control")
                if isinstance(state.get("execution_control"), dict)
                else {}
            )
            completion_observation = (
                state.get("completion_observation")
                if isinstance(state.get("completion_observation"), dict)
                else None
            )
            loop_state = {
                "steps_used": int(safety.get("steps_used") or 0),
                "extra_budget": int(safety.get("extra_budget") or 0),
                # Kept as telemetry for historical reports only.  It is never a terminal
                # decision by itself in the target-driven lifecycle.
                "force_converge": str(safety.get("force_converge") or ""),
                "budget_notice_level": int(safety.get("budget_notice_level") or 0),
                "execution_control": {
                    "segment_index": int(control.get("segment_index") or 0),
                    "checkpoint_sequence": int(control.get("checkpoint_sequence") or 0),
                    "recovery_reason": str(control.get("recovery_reason") or "") or None,
                    "last_progress_at": control.get("last_progress_at"),
                    "recovery_count": int(control.get("recovery_count") or 0),
                },
            }
            if completion_observation:
                loop_state["completion_observation"] = dict(completion_observation)
            historical_report = state.get("terminal_report")
            historical_fact = compact_terminal_fact(historical_report)
            if historical_fact:
                loop_state["terminal_report"] = historical_fact
            orch = state.get("orchestration") if isinstance(state.get("orchestration"), dict) else {}
            resume["resumed"] = bool(
                orch.get("mode") in {"task_recovery", "resume"}
                or state.get("resume_count")
            )
            resume["reclaimed"] = str(orch.get("mode") or "") == "task_recovery"
        events = (await session.execute(
            select(AgentRunEvent.type)
            .where(AgentRunEvent.run_id == run_id)
            .order_by(AgentRunEvent.sequence.desc())
            .limit(5)
        )).scalars().all()
        trail["event_types"] = list(reversed([str(item) for item in events]))
        last_call = (await session.execute(
            select(AgentToolCall)
            .where(AgentToolCall.run_id == run_id)
            .order_by(AgentToolCall.created_at.desc())
            .limit(1)
        )).scalars().first()
        if last_call is not None:
            obs = last_call.observation if isinstance(last_call.observation, dict) else {}
            trail["last_tool"] = {
                "name": str(last_call.name or ""),
                "status": str(last_call.status or obs.get("status") or ""),
                "error_code": str(obs.get("error_code") or "") or None,
            }
        names = (await session.execute(
            select(AgentToolCall.name)
            .where(AgentToolCall.run_id == run_id)
            .order_by(AgentToolCall.created_at.asc())
            .limit(24)
        )).scalars().all()
        if names:
            identity["tool_names"] = [str(name) for name in names if name]
        plan_id = await session.scalar(
            select(AgentPlan.id)
            .where(AgentPlan.run_id == run_id, AgentPlan.status == "active")
            .order_by(AgentPlan.updated_at.desc())
            .limit(1)
        )
        if plan_id:
            titles = (await session.execute(
                select(AgentPlanStep.description)
                .where(AgentPlanStep.plan_id == plan_id)
                .order_by(AgentPlanStep.seq.asc())
                .limit(8)
            )).scalars().all()
            attach_plan_titles(identity, titles)
        recent = (await session.execute(
            select(AgentToolCall.observation)
            .where(AgentToolCall.run_id == run_id)
            .order_by(AgentToolCall.created_at.desc())
            .limit(40)
        )).scalars().all()
        for raw in recent:
            if isinstance(raw, dict) and str(raw.get("error_code") or "") == "observation_contract_invalid":
                contract_invalid += 1
    return trail, resume, loop_state, contract_invalid, identity
