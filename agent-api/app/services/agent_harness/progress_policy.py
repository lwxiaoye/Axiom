"""Single source for tools an unfinished artifact task must keep visible.

Pins, force_product narrowing, and resume search blocks all call
``required_progress_tools``.  Plan ``requires`` stays an identity-bind gate;
this module prevents that gate from combining with a shrunk registry.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from app.services.skills.ppt_agentic_adapter import is_agentic_ppt_profile


PPT_BASE_TOOLS = frozenset({
    "update_plan", "bash", "read_file", "write_file", "edit_file", "glob",
    "publish_ppt_artifact",
})
PPT_PHOTO_TOOLS = frozenset({"search_web", "fetch_ppt_asset"})


def checkpoint_meta_from_profile(profile: Any) -> dict[str, Any]:
    if hasattr(profile, "model_dump"):
        profile = profile.model_dump(mode="python")
    if not isinstance(profile, Mapping):
        return {}
    policy = profile.get("workspace_policy") or {}
    if not isinstance(policy, Mapping):
        return {}
    if "checkpoint_media_count" not in policy:
        return {}
    try:
        count = int(policy.get("checkpoint_media_count") or 0)
    except (TypeError, ValueError):
        count = 0
    return {"media_count": count, "restored": bool(policy.get("checkpoint_restored"))}


def searched_photo_minimum(profile: Any) -> int:
    if not is_agentic_ppt_profile(profile):
        return 0
    if hasattr(profile, "model_dump"):
        profile = profile.model_dump(mode="python")
    qa = (profile or {}).get("qa_contract") or {}
    requirement = qa.get("image_requirement") if isinstance(qa, Mapping) else {}
    if not isinstance(requirement, Mapping):
        return 0
    if str(requirement.get("mode") or "") != "searched_photos":
        return 0
    try:
        return max(0, int(requirement.get("min_images") or 0))
    except (TypeError, ValueError):
        return 0


def _trace_fetch_count(trace: Any) -> int:
    got = 0
    for item in trace or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "") != "fetch_ppt_asset":
            continue
        if item.get("failed"):
            continue
        status = str(item.get("status") or "").lower()
        if status and status not in {"completed", "success", "succeeded", "ok"}:
            continue
        got += 1
    return got


def photos_needed_unmet(
    profile: Any,
    *,
    checkpoint_meta: Optional[Mapping[str, Any]] = None,
    trace: Any = None,
) -> bool:
    """True when searched_photos are required and not present in restored media or this-run fetches."""
    needed = searched_photo_minimum(profile)
    if needed <= 0:
        return False
    meta = dict(checkpoint_meta or {})
    if "media_count" in meta:
        try:
            return int(meta.get("media_count") or 0) < needed
        except (TypeError, ValueError):
            return True
    return _trace_fetch_count(trace) < needed


def _plan_cursor_requires(plan_rows: Any) -> frozenset[str]:
    from app.services.agent_harness.contracts import PlanStepStatus
    from app.services.agent_harness.plan_binding import ready_steps, snapshots_from_projection

    steps = snapshots_from_projection(plan_rows or ())
    in_progress = [step for step in steps if step.status is PlanStepStatus.IN_PROGRESS]
    target = in_progress[0] if len(in_progress) == 1 else None
    if target is None:
        ready = ready_steps(steps)
        if len(ready) == 1:
            target = ready[0]
    if target is None:
        return frozenset()
    return frozenset(str(tag) for tag in (target.requires or ()) if str(tag).strip())


def required_progress_tools(
    profile: Any,
    plan_rows: Any = None,
    *,
    checkpoint_meta: Optional[Mapping[str, Any]] = None,
    publish_receipt: bool = False,
    trace: Any = None,
) -> frozenset[str]:
    """Tools the registry must keep until a PPT publish receipt exists."""
    if publish_receipt or not is_agentic_ppt_profile(profile):
        return frozenset()
    names = set(PPT_BASE_TOOLS)
    if photos_needed_unmet(profile, checkpoint_meta=checkpoint_meta, trace=trace):
        names |= PPT_PHOTO_TOOLS
    if "investigate" in _plan_cursor_requires(plan_rows):
        names |= PPT_PHOTO_TOOLS
    return frozenset(names)
