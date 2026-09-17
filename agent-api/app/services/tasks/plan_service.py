"""Compatibility projection over the authoritative Harness Plan Store.

Execution code may still consume the compact list shape while migration is in progress, but all
writes and reads go through ``agent_harness.plan_store``. There is no second plan persistence path.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from app.services.agent_harness import plan_store, run_store
from app.services.agent_harness.contracts import (
    PlanStepSnapshot,
    PlanStepStatus,
)
from app.services.agent_harness.plan_binding import (
    infer_requires,
    is_conversational_empty_step,
    normalize_depends_on,
    normalize_requires,
    step_is_blocked,
)
from app.services.agent_harness.plan_controller import constrain_model_step_status
from app.services.chat.tools.plan import _normalize_plan_steps


_TO_CANONICAL = {
    "pending": PlanStepStatus.PENDING,
    "running": PlanStepStatus.IN_PROGRESS,
    "in_progress": PlanStepStatus.IN_PROGRESS,
    "completed": PlanStepStatus.COMPLETED,
    "skipped": PlanStepStatus.SKIPPED,
    "invalidated": PlanStepStatus.INVALIDATED,
    "failed": PlanStepStatus.INVALIDATED,
}


def _enforce_single_in_progress(
    steps: list[PlanStepSnapshot],
) -> tuple[PlanStepSnapshot, ...]:
    """Keep model plan updates valid without discarding the whole authoritative snapshot.

    Models occasionally mark both the current and next step active in one update. The Harness
    contract permits one active cursor, so preserve the earliest claim and conservatively return
    later active steps to pending instead of inventing completion or rejecting every other status
    change in the update.
    """
    active_seen = False
    normalized: list[PlanStepSnapshot] = []
    for step in steps:
        if step.status is PlanStepStatus.IN_PROGRESS:
            if active_seen:
                step = step.model_copy(update={"status": PlanStepStatus.PENDING})
            else:
                active_seen = True
        normalized.append(step)
    return tuple(normalized)


_APPROVE_PLAN_RE = re.compile(
    r"开始执行|批准修订|批准这次|确认执行|"
    r"执行此计划|执行计划|实施此计划|实施计划|"
    r"按计划执行|按这个计划|跟着计划|开始干活"
)
_REJECT_PLAN_RE = re.compile(r"不(要|用|必).{0,8}(执行|实施)|先不(执行|实施)|暂不(执行|实施)")
_REVISE_PLAN_RE = re.compile(r"我要改|改一改|修改计划")
_SKIP_PLAN_RE = re.compile(r"^(?:跳过|__PLAN_SKIP__)[。.!！]?$")
_DONE_STATUSES = frozenset({"completed", "skipped", "invalidated", "done"})


def _choice_text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    if isinstance(value, dict):
        return " ".join(str(item) for item in value.values())
    return str(value or "")


def choice_skips_plan(value: Any) -> bool:
    """True when the user keeps the plan but declines to execute it now."""
    return bool(_SKIP_PLAN_RE.match(_choice_text(value).strip()))


def choice_approves_plan(value: Any) -> bool:
    """True only when the HITL answer is an explicit execute/approve choice."""
    if choice_skips_plan(value):
        return False
    text = _choice_text(value)
    if _REJECT_PLAN_RE.search(text):
        return False
    if _REVISE_PLAN_RE.search(text) and not _APPROVE_PLAN_RE.search(text):
        return False
    return bool(_APPROVE_PLAN_RE.search(text))


def classify_plan_update(previous: Any, incoming: Any) -> str:
    """Classify a whole-table plan rewrite: status | content | structure."""
    prev = [row for row in (previous or []) if isinstance(row, dict)]
    nxt = [row for row in (incoming or []) if isinstance(row, dict)]
    used = [False] * len(prev)
    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    added: list[dict[str, Any]] = []
    for candidate in nxt:
        key = str(candidate.get("key") or candidate.get("step_id") or "").strip()
        title = str(candidate.get("title") or "").strip()
        found = None
        if key:
            for index, row in enumerate(prev):
                if used[index]:
                    continue
                if str(row.get("key") or row.get("step_id") or "").strip() == key:
                    found = index
                    break
        if found is None and title:
            for index, row in enumerate(prev):
                if used[index]:
                    continue
                if str(row.get("title") or "").strip() == title:
                    found = index
                    break
        if found is None:
            added.append(candidate)
        else:
            used[found] = True
            matched.append((prev[found], candidate))
    removed = [row for index, row in enumerate(prev) if not used[index]]
    incomplete_removed = [
        row for row in removed
        if str(row.get("status") or "") not in _DONE_STATUSES
    ]
    if added or incomplete_removed:
        return "structure"
    for old, new in matched:
        old_acc = str(old.get("acceptance") or "").strip()
        new_acc = str(new.get("acceptance") or "").strip()
        if (
            str(old.get("title") or "").strip() != str(new.get("title") or "").strip()
            or str(old.get("detail") or "").strip() != str(new.get("detail") or "").strip()
            or old_acc != new_acc
        ):
            return "content"
    return "status"


def compact_review_steps(steps: Any) -> list[dict[str, Any]]:
    """Shape plan rows for the confirmation card (no extra API)."""
    rows: list[dict[str, Any]] = []
    for index, step in enumerate(steps or []):
        if not isinstance(step, dict):
            continue
        title = str(step.get("title") or "").strip()
        if not title:
            continue
        acc = str(step.get("acceptance") or "").strip()[:60]
        if not acc and isinstance(step.get("acceptance_criteria"), list) and step["acceptance_criteria"]:
            acc = str(step["acceptance_criteria"][0] or "").strip()[:60]
        row: dict[str, Any] = {
            "key": str(step.get("key") or step.get("step_id") or f"plan-{index}"),
            "title": title[:80],
            "status": str(step.get("status") or "pending"),
            "detail": str(step.get("detail") or "")[:240] or None,
        }
        if acc:
            row["acceptance"] = acc
        rows.append(row)
    return rows


def _approved_version_from_state(state: dict[str, Any] | None) -> int | None:
    raw = (state or {}).get("approved_plan_version")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _to_projection(
    snapshot,
    goal_contract: dict | None = None,
    *,
    approved_version: int | None = None,
    diverged: bool = False,
) -> list[dict[str, Any]]:
    if snapshot is None:
        return []
    status_map = {
        PlanStepStatus.IN_PROGRESS: "running",
    }
    rows = []
    for step in snapshot.steps:
        row = {
            "key": step.step_id,
            "title": step.title,
            "detail": step.detail or None,
            "status": status_map.get(step.status, step.status.value),
            "required": step.required,
            "verified": bool(step.status is PlanStepStatus.COMPLETED and step.evidence_refs),
            "evidence": list(step.evidence_refs),
            "acceptance_criteria": list(step.acceptance_criteria),
            "reason": step.reason or None,
            "goal_revision": snapshot.goal_revision,
            "plan_version": snapshot.plan_version,
        }
        if step.acceptance_criteria:
            row["acceptance"] = str(step.acceptance_criteria[0])[:60]
        if step.depends_on:
            row["depends_on"] = list(step.depends_on)
        if step.requires:
            row["requires"] = list(step.requires)
        if step_is_blocked(step, snapshot.steps):
            row["blocked"] = True
            detail = str(row.get("detail") or "").strip()
            if "等待上一步" not in detail:
                row["detail"] = f"{detail} · 等待上一步" if detail else "等待上一步"
        rows.append(row)
    extra: dict[str, Any] = {}
    if goal_contract and isinstance(goal_contract, dict):
        extra["goal_contract"] = dict(goal_contract)
    if approved_version is not None:
        extra["approved_version"] = int(approved_version)
    if diverged:
        extra["diverged"] = True
    if extra and rows:
        rows[0] = {**rows[0], **extra}
    return rows


async def _goal_for_run(run_id: str, fallback: str = "") -> str:
    from app.core.runtime_db import runtime_session
    from app.runtime_models import AgentRun

    factory = runtime_session()
    if factory is None:
        return fallback or "完成当前任务"
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        return str((run.goal if run else "") or fallback or "完成当前任务")


async def upsert_plan(run_id: str, raw_steps: Any, *, summary: str = "") -> list[dict[str, Any]]:
    normalized = _normalize_plan_steps(raw_steps)
    for _ in range(4):
        current = await run_store.get_run_state(run_id)
        if current is None:
            return []
        state = current["state"]
        existing = await plan_store.get_plan_snapshot(run_id)
        existing_by_title = {
            step.title: step.step_id for step in (existing.steps if existing else ())
        }
        existing_by_id = {
            step.step_id: step for step in (existing.steps if existing else ())
        }
        known_ids = {
            str(item.get("key") or item.get("step_key") or "").strip()
            for item in normalized
            if str(item.get("key") or item.get("step_key") or "").strip()
        }
        known_ids.update(existing_by_id)
        next_steps: list[PlanStepSnapshot] = []
        for index, item in enumerate(normalized):
            title = str(item.get("title") or "").strip()
            acceptance_criteria = (
                list(item.get("acceptance_criteria") or [])
            )
            raw_requires = normalize_requires(
                item.get("requires"),
                inferred=infer_requires(
                    title,
                    str(item.get("detail") or ""),
                    acceptance_criteria,
                ),
            )
            if is_conversational_empty_step(
                title,
                acceptance=acceptance_criteria,
                requires=raw_requires,
            ):
                continue
            step_id = str(
                item.get("key")
                or item.get("step_key")
                or existing_by_title.get(title)
                or f"step-{index + 1}"
            )
            previous = existing_by_id.get(step_id)
            if previous is not None and not item.get("requires"):
                raw_requires = previous.requires or raw_requires
            if not acceptance_criteria and previous is not None:
                acceptance_criteria = list(previous.acceptance_criteria)
            canonical_status = _TO_CANONICAL.get(
                str(item.get("status") or "pending"),
                PlanStepStatus.PENDING,
            )
            if (
                previous is not None
                and previous.status is PlanStepStatus.COMPLETED
                and previous.evidence_refs
                and canonical_status in {
                    PlanStepStatus.PENDING,
                    PlanStepStatus.IN_PROGRESS,
                }
            ):
                canonical_status = PlanStepStatus.COMPLETED
            candidate = PlanStepSnapshot(
                step_id=step_id,
                order=len(next_steps),
                title=title,
                detail=str(item.get("detail") or ""),
                status=canonical_status,
                required=bool(item.get("required", True)),
                acceptance_criteria=acceptance_criteria,
                # update_plan 的模型参数刻意不暴露 evidence；工具回执由 Harness 持有，
                # 后续整表语义修订不能把已提交证据擦掉。
                evidence_refs=list(previous.evidence_refs if previous else []),
                reason=(
                    str(item.get("reason") or "")
                    or (
                        str(previous.reason if previous else "")
                        if canonical_status in {PlanStepStatus.SKIPPED, PlanStepStatus.INVALIDATED}
                        else ""
                    )
                    or ("tool_reported_failure" if item.get("status") == "failed" else "")
                ),
                depends_on=normalize_depends_on(
                    item.get("depends_on") if item.get("depends_on") is not None
                    else (previous.depends_on if previous else ()),
                    known_ids=known_ids,
                ),
                requires=raw_requires,
            )
            # update_plan owns the semantic checklist shape. Existing receipt evidence is
            # preserved here; subsequent status/evidence transitions are committed by
            # Plan Controller from real ToolObservations rather than another model echo.
            next_steps.append(candidate)
        if not next_steps:
            return _to_projection(
                existing,
                goal_contract=state.get("goal_contract") if isinstance(state.get("goal_contract"), dict) else None,
                approved_version=_approved_version_from_state(state),
            )
        # The model may reshape the checklist or nominate a cursor, but it cannot
        # overwrite receipt-backed truth. In particular, a stale status-only echo
        # must not demote a completed step or complete an unobserved one.
        constrained_steps = [
            step.model_copy(update={
                "status": constrain_model_step_status(
                    step,
                    step.status,
                    plan_steps=next_steps,
                ),
            })
            for step in next_steps
        ]
        steps = _enforce_single_in_progress(constrained_steps)
        try:
            snapshot = await plan_store.update_plan(
                run_id,
                expected_plan_version=int(state.get("plan_version") or 0),
                expected_goal_revision=int(state.get("goal_revision") or 0),
                goal=await _goal_for_run(run_id, summary),
                steps=steps,
            )
            contract = state.get("goal_contract") if isinstance(state.get("goal_contract"), dict) else None
            return _to_projection(
                snapshot,
                goal_contract=contract,
                approved_version=_approved_version_from_state(state),
            )
        except plan_store.PlanConflict:
            continue
    raise plan_store.PlanConflict("plan_update_conflict")


async def get_current_plan(run_id: str) -> list[dict[str, Any]]:
    snapshot = await plan_store.get_plan_snapshot(run_id)
    contract = None
    try:
        current = await run_store.get_run_state(run_id)
        raw = ((current or {}).get("state") or {}).get("goal_contract")
        if isinstance(raw, dict):
            contract = raw
        approved = _approved_version_from_state((current or {}).get("state") or {})
    except Exception:  # noqa: BLE001
        contract = None
        approved = None
    return _to_projection(snapshot, goal_contract=contract, approved_version=approved)


async def inherit_latest_thread_plan(
    run_id: str,
    thread_id: str,
    *,
    source_run_id: str = "",
    require_complete: bool = False,
) -> list[dict[str, Any]]:
    """Clone the latest prior Run plan for an exact bare-continuation handoff.

    The copy is committed to the target Run's authoritative Plan Store before its
    ``plan.updated`` event is emitted, so the frontend never needs a cross-Run fallback.
    """
    existing = await plan_store.get_plan_snapshot(run_id)
    if existing is not None:
        return _to_projection(existing)
    source = (
        await plan_store.get_plan_snapshot(source_run_id)
        if source_run_id
        else await plan_store.get_latest_prior_plan_snapshot(
            thread_id,
            before_run_id=run_id,
            require_complete=require_complete,
        )
    )
    if source is None:
        return []
    if require_complete and any(
        step.required and step.status not in {
            PlanStepStatus.COMPLETED,
            PlanStepStatus.SKIPPED,
            PlanStepStatus.INVALIDATED,
        }
        for step in source.steps
    ):
        return []
    for _ in range(4):
        current = await run_store.get_run_state(run_id)
        if current is None:
            return []
        state = current["state"]
        try:
            inherited = await plan_store.update_plan(
                run_id,
                expected_plan_version=int(state.get("plan_version") or 0),
                expected_goal_revision=int(state.get("goal_revision") or 0),
                goal=source.goal,
                steps=source.steps,
            )
            return _to_projection(inherited)
        except plan_store.PlanConflict:
            existing = await plan_store.get_plan_snapshot(run_id)
            if existing is not None:
                return _to_projection(existing)
    raise plan_store.PlanConflict("plan_inheritance_conflict")


async def set_plan_step_status(
    run_id: str,
    step_key: str,
    status: str,
    *,
    evidence: Optional[dict[str, Any]] = None,
    verified: Optional[bool] = None,
) -> list[dict[str, Any]]:
    current = await plan_store.get_plan_snapshot(run_id)
    if current is None:
        return []
    replacement = []
    found = False
    for step in current.steps:
        if step.step_id != step_key:
            replacement.append(step)
            continue
        found = True
        evidence_refs = list(step.evidence_refs)
        if evidence is not None:
            evidence_refs = [dict(evidence)]
        canonical_status = _TO_CANONICAL.get(status)
        if canonical_status is None:
            raise ValueError(f"invalid plan step status: {status}")
        if verified is False and canonical_status is PlanStepStatus.COMPLETED:
            canonical_status = PlanStepStatus.IN_PROGRESS
        replacement.append(step.model_copy(update={
            "status": canonical_status,
            "evidence_refs": evidence_refs,
            "reason": "tool_reported_failure" if status == "failed" else step.reason,
        }))
    if not found:
        return _to_projection(current)
    snapshot = await plan_store.update_plan(
        run_id,
        expected_plan_version=current.plan_version,
        expected_goal_revision=current.goal_revision,
        goal=current.goal,
        steps=tuple(replacement),
    )
    return _to_projection(snapshot)


async def plan_is_complete(run_id: str) -> bool:
    snapshot = await plan_store.get_plan_snapshot(run_id)
    return bool(snapshot and all(
        not step.required or step.status in {
            PlanStepStatus.COMPLETED,
            PlanStepStatus.SKIPPED,
            PlanStepStatus.INVALIDATED,
        }
        for step in snapshot.steps
    ))
