"""Advance the authoritative Plan from committed tool observations."""

from __future__ import annotations

import re
from typing import Any, Iterable

from . import plan_store
from .contracts import (
    ObservationStatus,
    PlanSnapshot,
    PlanStepSnapshot,
    PlanStepStatus,
    ToolObservation,
    ToolSpec,
)
from .plan_binding import (
    _ARTIFACT_DELIVERY_ACTION_RE,
    _INVESTIGATION_STEP_RE,
    infer_completion_stage,
    ready_steps,
)


_SUCCESS_MILESTONES = frozenset({
    "investigation",
    "asset_collection",
    "artifact_build",
    "artifact_repair",
    "artifact_delivery",
    "state_change",
})
_PPT_PUBLISH_STEP_RE = re.compile(
    r"(发布|publish).{0,20}(pptx?|演示|幻灯|课件|文件|成品)|"
    r"交付最终|"
    r"校验并发布",
    re.I,
)
PLAN_CHURN_NOTICE = (
    "连续多次只改计划、没有新的工具回执。计划无需再改。"
    "若文件已在「我的文件」，直接向用户交付并收尾；"
    "若还没写入，去调用 bash/write_file，不要再 update_plan。"
)


def step_requires_persisted_artifact(step: PlanStepSnapshot) -> bool:
    """Return whether this semantic step can only finish from a persisted file receipt."""
    text = "\n".join([
        step.title,
        step.detail,
        *[str(item) for item in step.acceptance_criteria],
    ])
    return bool(_ARTIFACT_DELIVERY_ACTION_RE.search(text))


def step_is_ppt_publish(step: PlanStepSnapshot) -> bool:
    """True when this row is the PPT export/publish gate, not a generic Word/file save."""
    text = "\n".join([
        step.title,
        step.detail,
        *[str(item) for item in step.acceptance_criteria],
    ])
    return bool(_PPT_PUBLISH_STEP_RE.search(text))


def _records_have_file_id(records: Iterable[Any]) -> bool:
    return any(
        isinstance(record, dict)
        and str(record.get("file_id") or record.get("id") or "").strip()
        for record in records or ()
    )


def _ref_has_durable_file(ref: Any) -> bool:
    if not isinstance(ref, dict):
        return False
    if str(ref.get("status") or "") not in {"", ObservationStatus.SUCCEEDED.value, "succeeded"}:
        return False
    records = [*(ref.get("artifact_refs") or []), *(ref.get("receipts") or [])]
    return _records_have_file_id(records)


def step_has_persisted_artifact_evidence(step: PlanStepSnapshot) -> bool:
    """True when this step already holds a durable file identifier.

    Interactive Word/docx saves arrive as ``artifact_build`` from bash. PPT publish
    still requires the ``artifact.export`` / ``artifact_delivery`` milestone so a
    staging bash cannot skip ``publish_ppt_artifact``.
    """
    ppt_publish = step_is_ppt_publish(step)
    for ref in step.evidence_refs:
        if not isinstance(ref, dict):
            continue
        if ref.get("status") != ObservationStatus.SUCCEEDED.value:
            continue
        if not _ref_has_durable_file(ref):
            continue
        milestone = str(ref.get("milestone") or "")
        if milestone == "artifact_delivery":
            return True
        if milestone == "artifact_build" and not ppt_publish:
            return True
    return False


def _sibling_has_persisted_artifact(
    step: PlanStepSnapshot,
    plan_steps: Iterable[PlanStepSnapshot] = (),
) -> bool:
    if step_is_ppt_publish(step):
        return False
    return any(
        other.step_id != step.step_id and step_has_persisted_artifact_evidence(other)
        for other in plan_steps or ()
    )


def plan_rows_have_file_receipt(rows: Iterable[Any]) -> bool:
    """Compact LoopState / SSE rows, or canonical snapshots."""
    for item in rows or ():
        if isinstance(item, PlanStepSnapshot):
            if step_has_persisted_artifact_evidence(item):
                return True
            continue
        if not isinstance(item, dict):
            continue
        for ref in item.get("evidence") or item.get("evidence_refs") or []:
            if not _ref_has_durable_file(ref):
                continue
            milestone = str(ref.get("milestone") or "")
            if milestone in {"artifact_delivery", "artifact_build", ""}:
                return True
    return False


def completion_gap_ack(requested: Any, committed: Any) -> str:
    """Tool ack after update_plan: say so when completed was quietly demoted."""
    committed_rows = [row for row in (committed or []) if isinstance(row, dict)]
    requested_rows = [row for row in (requested or []) if isinstance(row, dict)]
    by_key = {
        str(row.get("key") or row.get("step_id") or "").strip(): row
        for row in committed_rows
        if str(row.get("key") or row.get("step_id") or "").strip()
    }
    by_title = {
        str(row.get("title") or "").strip(): row
        for row in committed_rows
        if str(row.get("title") or "").strip()
    }
    demoted: list[str] = []
    for row in requested_rows:
        status = str(row.get("status") or "").strip().lower()
        if status not in {"completed", "done", "finished", "complete", "ok"}:
            continue
        key = str(row.get("key") or row.get("step_key") or "").strip()
        title = str(row.get("title") or "").strip()
        cap = by_key.get(key) or by_title.get(title)
        if cap is None:
            continue
        cap_status = str(cap.get("status") or "").strip().lower()
        if cap_status in {"completed", "skipped", "invalidated", "done"}:
            continue
        demoted.append(title or key)
    if not demoted:
        return "计划已更新。"
    files: list[str] = []
    for row in committed_rows:
        for ref in row.get("evidence") or row.get("evidence_refs") or []:
            if not isinstance(ref, dict):
                continue
            for record in [*(ref.get("artifact_refs") or []), *(ref.get("receipts") or [])]:
                if not isinstance(record, dict):
                    continue
                name = str(
                    record.get("filename") or record.get("file_id") or record.get("id") or ""
                ).strip()
                if name and name not in files:
                    files.append(name)
    parts = [
        "计划已更新，但以下步骤未被勾选（空口 completed 无效）：",
        "；".join(f"「{item}」" for item in demoted[:6]),
        "。",
    ]
    if files:
        parts.append(
            f"本 Run 已有文件回执（{', '.join(files[:3])}）。"
            "不要再反复 update_plan，直接向用户交付并收尾。"
        )
    else:
        parts.append(
            "不要把还没落盘的步骤标成 completed。"
            "请直接用 bash 把最终文件写到 /workspace/files/"
            "（Word/PPT 不要走 write_file）；不要只改计划。"
        )
    return "".join(parts)


def _unique_source_urls(step: PlanStepSnapshot) -> set[str]:
    urls: set[str] = set()
    for ref in step.evidence_refs:
        if not isinstance(ref, dict):
            continue
        for item in ref.get("urls") or []:
            url = str(item or "").strip()
            if url.startswith("http"):
                urls.add(url)
        nested = ref.get("evidence_refs") or []
        if isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    url = str(item.get("url") or "").strip()
                    if url.startswith("http"):
                        urls.add(url)
    return urls


def constrain_model_step_status(
    step: PlanStepSnapshot,
    requested: PlanStepStatus,
    *,
    research_mode: bool = False,
    min_sources: int = 2,
    plan_steps: Iterable[PlanStepSnapshot] = (),
) -> PlanStepStatus:
    """Model may nominate the cursor; completed is owned by tool receipts."""
    if requested is not PlanStepStatus.COMPLETED:
        return requested
    own_file = step_has_persisted_artifact_evidence(step)
    sibling_file = _sibling_has_persisted_artifact(step, plan_steps)
    if (
        step_requires_persisted_artifact(step)
        and not own_file
        and not sibling_file
    ):
        return PlanStepStatus.IN_PROGRESS
    evidence = _success_milestone_evidence(step)
    if not evidence:
        if infer_completion_stage(step) in {"assets", "design", "source", "export", "validation"}:
            return PlanStepStatus.IN_PROGRESS
        if own_file:
            return requested
        if sibling_file and step_requires_persisted_artifact(step):
            return requested
        return PlanStepStatus.IN_PROGRESS
    text = "\n".join([
        step.title,
        step.detail,
        *[str(item) for item in step.acceptance_criteria],
    ])
    investigation = bool(_INVESTIGATION_STEP_RE.search(text))
    if step.acceptance_criteria:
        if investigation and len(evidence) < 2:
            return PlanStepStatus.IN_PROGRESS
    if research_mode and investigation:
        needed = max(2, int(min_sources or 2))
        if len(evidence) < needed:
            return PlanStepStatus.IN_PROGRESS
        if len(_unique_source_urls(step)) < needed:
            return PlanStepStatus.IN_PROGRESS
    return requested


def _success_milestone_evidence(step: PlanStepSnapshot) -> list[dict]:
    stage = infer_completion_stage(step)
    if stage in {"design", "source", "export", "validation"}:
        # A fresh project observation supersedes older snapshots. Otherwise an
        # earlier exported deck could prove completion after its source changed.
        for ref in reversed(step.evidence_refs):
            if not isinstance(ref, dict):
                continue
            for receipt in reversed(ref.get("receipts") or []):
                if not isinstance(receipt, dict) or receipt.get("kind") != "artifact_progress":
                    continue
                if receipt.get("artifact_type") != "pptx":
                    continue
                if (
                    ref.get("status") == ObservationStatus.SUCCEEDED.value
                    and receipt.get("checked") is True
                    and isinstance(receipt.get("stages"), list)
                    and stage in (receipt.get("stages") or [])
                ):
                    return [ref]
                return []
    found: list[dict] = []
    for ref in step.evidence_refs:
        if not isinstance(ref, dict):
            continue
        if ref.get("status") != ObservationStatus.SUCCEEDED.value:
            continue
        milestone = str(ref.get("milestone") or "")
        if milestone not in _SUCCESS_MILESTONES:
            continue
        if stage == "assets" and milestone != "asset_collection":
            continue
        if stage in {"design", "source"} and milestone not in {
            "artifact_build", "artifact_repair", "artifact_delivery", "state_change",
        }:
            continue
        if stage in {"export", "validation"} and not _ref_has_durable_file(ref):
            continue
        found.append(ref)
    return found


def _evidence_ref(
    spec: ToolSpec,
    observation: ToolObservation,
    *,
    milestone: str = "",
) -> dict:
    ref = {
        "kind": "tool_observation",
        "call_id": observation.call_id,
        "tool_name": observation.tool_name,
        "status": observation.status.value,
        "capability": spec.capability,
        "effect_scope": spec.effect_scope.value,
    }
    if milestone:
        ref["milestone"] = milestone
    if observation.error_code:
        ref["error_code"] = observation.error_code
    if observation.artifact_refs:
        ref["artifact_refs"] = list(observation.artifact_refs[:8])
    if observation.receipts:
        ref["receipts"] = list(observation.receipts[:8])
    urls = []
    for item in observation.evidence_refs:
        if isinstance(item, dict):
            url = str(item.get("url") or "").strip()
            if url.startswith("http") and url not in urls:
                urls.append(url)
    if urls:
        ref["urls"] = urls[:12]
    return ref


def _has_failed_export_attempt(plan: PlanSnapshot) -> bool:
    return any(
        isinstance(ref, dict)
        and ref.get("status") == ObservationStatus.FAILED.value
        and ref.get("capability") == "artifact.export"
        for step in plan.steps
        for ref in step.evidence_refs
    )


def _milestone_for(
    plan: PlanSnapshot,
    spec: ToolSpec,
    observation: ToolObservation,
) -> str:
    if observation.status is not ObservationStatus.SUCCEEDED:
        return ""
    tags = spec.semantic_tags
    if (
        spec.capability == "artifact.export"
        and any(
            isinstance(record, dict)
            and str(record.get("file_id") or record.get("id") or "").strip()
            for record in [*observation.artifact_refs, *observation.receipts]
        )
    ):
        return "artifact_delivery"
    if "revision_mutation" in tags and _has_failed_export_attempt(plan):
        return "artifact_repair"
    if "download" in tags:
        return "asset_collection"
    if "revision_mutation" in tags or "productive" in tags:
        return "artifact_build"
    if "investigate" in tags:
        return "investigation"
    if "mutate" in tags:
        return "state_change"
    return ""


def project_observation(
    plan: PlanSnapshot,
    spec: ToolSpec,
    observation: ToolObservation,
) -> tuple[PlanStepSnapshot, ...]:
    """Reduce only the observation's bound step. Status is never guessed from cursor."""
    bound_id = str(observation.plan_step_id or "").strip()
    if not bound_id:
        return plan.steps
    active_index = next(
        (index for index, step in enumerate(plan.steps) if step.step_id == bound_id),
        None,
    )
    if active_index is None:
        return plan.steps

    current = plan.steps[active_index]
    if current.status in {
        PlanStepStatus.SKIPPED,
        PlanStepStatus.INVALIDATED,
    }:
        return plan.steps

    milestone = _milestone_for(plan, spec, observation)
    evidence = _evidence_ref(spec, observation, milestone=milestone)
    refs = [*current.evidence_refs, evidence][-24:]
    updated = current.model_copy(update={"evidence_refs": refs})
    if current.status is PlanStepStatus.COMPLETED:
        next_status = PlanStepStatus.COMPLETED
    elif observation.status is ObservationStatus.SUCCEEDED:
        next_status = constrain_model_step_status(
            updated,
            PlanStepStatus.COMPLETED,
            plan_steps=plan.steps,
        )
        if (
            next_status is PlanStepStatus.COMPLETED
            and step_requires_persisted_artifact(updated)
            and milestone != "artifact_delivery"
            and step_is_ppt_publish(updated)
        ):
            next_status = PlanStepStatus.IN_PROGRESS
    else:
        next_status = PlanStepStatus.IN_PROGRESS
    steps = list(plan.steps)
    steps[active_index] = updated.model_copy(update={"status": next_status})
    if next_status is PlanStepStatus.COMPLETED:
        steps = _advance_unique_ready(steps)
    return tuple(steps)


def _advance_unique_ready(steps: list[PlanStepSnapshot]) -> list[PlanStepSnapshot]:
    """After a bound step completes, nominate the only remaining ready step."""
    if any(step.status is PlanStepStatus.IN_PROGRESS for step in steps):
        return steps
    ready = ready_steps(steps)
    if len(ready) != 1:
        return steps
    target = ready[0]
    return [
        step.model_copy(update={"status": PlanStepStatus.IN_PROGRESS})
        if step.step_id == target.step_id
        else step
        for step in steps
    ]


async def record_tool_observation(
    run_id: str,
    spec: ToolSpec,
    observation: ToolObservation,
) -> PlanSnapshot | None:
    """CAS-commit observation-driven progress and return the committed full snapshot."""
    for _ in range(4):
        current = await plan_store.get_plan_snapshot(run_id)
        if current is None:
            return None
        steps = project_observation(current, spec, observation)
        if steps == current.steps:
            return current
        try:
            return await plan_store.update_plan(
                run_id,
                expected_plan_version=current.plan_version,
                expected_goal_revision=current.goal_revision,
                goal=current.goal,
                steps=steps,
            )
        except plan_store.PlanConflict:
            continue
    raise plan_store.PlanConflict("plan_observation_update_conflict")
