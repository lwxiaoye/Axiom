"""Deterministic, immutable execution-profile resolution for retired runtime Runs.

The HTTP accept path freezes only the evidence available at acceptance.  A Worker resolves that
evidence exactly once before model/tool execution and persists the resulting snapshot through the
RunState CAS API.  This module is deliberately pure: it never reads mutable thread state, model
output, credentials, or provider configuration.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


PROFILE_RESOLVER_VERSION = "execution-profile-v1"
PROFILE_SNAPSHOT_VERSION = 1

ProfileId = Literal["interactive", "artifact_coding"]

_PPT_SKILL_RE = re.compile(
    r"(?:pptx?|power\s*point|slides?|ppt[\s_-]*studio|open[\s_-]*kimi[\s_-]*ppt|"
    r"幻灯片|演示文稿|演示稿|课件)",
    re.I,
)
_PPTD_SKILL_RE = re.compile(r"^(?:ppt[\s_-]*studio|open[\s_-]*kimi[\s_-]*ppt)$", re.I)


class ExecutionProfileError(RuntimeError):
    """Base error for missing, corrupt, or non-finalizable profile state."""


class ExecutionProfileSnapshot(BaseModel):
    """Immutable policy snapshot consumed by every Worker for one Run."""

    model_config = ConfigDict(extra="forbid")

    id: ProfileId
    version: int = PROFILE_SNAPSHOT_VERSION
    policy_hash: str
    artifact_kind: Optional[str] = None
    authoring_backend: Optional[str] = None
    capability_policy: dict[str, Any] = Field(default_factory=dict)
    loop_policy: dict[str, Any] = Field(default_factory=dict)
    workspace_policy: dict[str, Any] = Field(default_factory=dict)
    qa_contract: dict[str, Any] = Field(default_factory=dict)
    fallback_policy: dict[str, Any] = Field(default_factory=dict)
    resolver_version: str = PROFILE_RESOLVER_VERSION
    evidence_hash: str
    resolution_reason: str


def _field(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def freeze_profile_evidence(
    *,
    message: str,
    skill_ids: Optional[list[Any]] = None,
    selected_skills: Optional[list[Any]] = None,
    attachments: Optional[list[Any]] = None,
    inherited_profile_id: Optional[ProfileId] = None,
    inherited_image_requirement: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Return the credential-free evidence snapshot persisted by the accept path."""
    skills = []
    for item in list(selected_skills or [])[:32]:
        sid = str(_field(item, "id") or _field(item, "skill_id") or "").strip()
        name = str(_field(item, "name") or "").strip()
        skills.append({"id": sid[:128], "name": name[:128]})

    attachment_names = []
    for item in list(attachments or [])[:32]:
        name = str(_field(item, "filename") or _field(item, "name") or "").strip()
        if name:
            attachment_names.append(name[:255])

    payload = {
        "resolver_version": PROFILE_RESOLVER_VERSION,
        "message": str(message or ""),
        "skill_ids": [
            str(value).strip()[:128]
            for value in list(skill_ids or [])[:32]
            if str(value).strip()
        ],
        "selected_skills": skills,
        "attachment_names": attachment_names,
        "inherited_profile_id": inherited_profile_id,
        "inherited_image_requirement": _normalized_image_requirement(
            inherited_image_requirement,
        ),
    }
    # Persist the deterministic routing facts themselves, not merely the raw strings.  A Worker
    # may start after an application rollout; re-running a changed shared PPT policy would make
    # the same accepted Run drift even though its resolver_version and request are unchanged.
    from app.services.skills.ppt_policy import (
        is_ppt_artifact_request,
        ppt_photo_requirement,
    )

    explicit_ppt_backend = _selected_ppt_backend(payload)
    payload["explicit_ppt_skill"] = bool(explicit_ppt_backend)
    payload["explicit_ppt_backend"] = explicit_ppt_backend
    payload["ppt_artifact_intent"] = is_ppt_artifact_request(
        payload["message"], payload["attachment_names"],
    )
    payload["ppt_image_requirement"] = ppt_photo_requirement(payload["message"])
    payload["evidence_hash"] = _sha256(payload)
    return payload


def _validated_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    frozen = dict(evidence or {})
    expected_hash = str(frozen.pop("evidence_hash", "") or "")
    if str(frozen.get("resolver_version") or "") != PROFILE_RESOLVER_VERSION:
        raise ExecutionProfileError("unsupported execution profile resolver version")
    if not expected_hash or _sha256(frozen) != expected_hash:
        raise ExecutionProfileError("execution profile evidence hash mismatch")
    frozen["evidence_hash"] = expected_hash
    return frozen


def _selected_ppt_skill(evidence: dict[str, Any]) -> bool:
    return bool(_selected_ppt_backend(evidence))


def _selected_ppt_backend(evidence: dict[str, Any]) -> Optional[str]:
    candidates = [str(value or "") for value in evidence.get("skill_ids") or []]
    for item in evidence.get("selected_skills") or []:
        if isinstance(item, dict):
            candidates.extend((str(item.get("id") or ""), str(item.get("name") or "")))
    if any(_PPTD_SKILL_RE.fullmatch(value.strip()) for value in candidates):
        return "pptd"
    if any(_PPT_SKILL_RE.search(value) for value in candidates):
        return "skill_native"
    return None


def _normalized_image_requirement(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    mode = str(value.get("mode") or "").strip()
    if mode not in {"none", "searched_photos", "skill_default"}:
        return None
    try:
        count = min(12, max(0, int(value.get("min_images") or 0)))
    except (TypeError, ValueError):
        count = 0
    try:
        source_count = min(count, max(0, int(value.get("min_sources") or 0)))
    except (TypeError, ValueError):
        source_count = 0
    return {
        "mode": mode,
        "min_images": count,
        "min_sources": source_count,
        "brief": str(value.get("brief") or "")[:400],
    }


def _policy_payload(
    profile_id: ProfileId,
    *,
    image_requirement: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if profile_id == "artifact_coding":
        return {
            "id": profile_id,
            "version": PROFILE_SNAPSHOT_VERSION,
            "artifact_kind": "presentation",
            "authoring_backend": "pptd",
            "capability_policy": {
                "mode": "artifact_coding",
                "trusted_skill_resources": "declared_only",
            },
            "loop_policy": {
                "mode": "artifact_coding",
                "progress_basis": "project_and_render_delta",
                # A polished deck can legitimately need several render-review-repair cycles.
                # Keep these as runaway guards, not as a normal delivery deadline; provider 504
                # retries must not consume the only chance to publish an already repaired deck.
                "max_steps": 48,
                "max_wall_seconds": 3600,
                # 思考模型一轮就能烧掉全局 150k 输出预算，未发布前不得因此 tool_choice=none。
                "max_output_tokens": 400_000,
            },
            "workspace_policy": {
                "mode": "staging",
                "project_root": "/workspace/tmp/ppt-project",
                "publish_root": "/workspace/files",
                "disposable_run": True,
            },
            "qa_contract": {
                "require_editable_pptx": True,
                "require_source_project": True,
                "require_rendered_pages": True,
                "require_structural_qa": True,
                # Rendering self-check is optional model/Skill work; the Harness
                # completion contract only requires deterministic structural QA.
                "require_visual_qa": False,
                "visual_qa_optional": True,
                "image_requirement": image_requirement or {
                    "mode": "skill_default", "min_images": 0,
                    "min_sources": 0, "brief": "",
                },
            },
            "fallback_policy": {
                "mode": "disabled",
                "backend": None,
            },
        }
    return {
        "id": "interactive",
        "version": PROFILE_SNAPSHOT_VERSION,
        "artifact_kind": None,
        "authoring_backend": None,
        "capability_policy": {"mode": "default"},
        "loop_policy": {"mode": "default"},
        "workspace_policy": {"mode": "default"},
        "qa_contract": {"mode": "default"},
        "fallback_policy": {"mode": "default"},
    }


def resolve_profile_snapshot(evidence: dict[str, Any]) -> dict[str, Any]:
    """Resolve one frozen evidence snapshot without consulting mutable external state."""
    frozen = _validated_evidence(evidence)
    selected_ppt = bool(frozen.get("explicit_ppt_skill"))
    selected_backend = str(frozen.get("explicit_ppt_backend") or "")
    if selected_ppt and selected_backend == "skill_native":
        # An explicitly selected third-party PPT Skill owns its authoring stack.
        # Do not inject the ppt-studio-only PPTD/source-project/visual-QA contract;
        # the normal artifact path persists its generated PPTX from /workspace/files.
        profile_id = "interactive"
        reason = "explicit_skill_native_ppt"
    elif selected_ppt:
        profile_id: ProfileId = "artifact_coding"
        reason = "explicit_ppt_skill"
    elif frozen.get("inherited_profile_id") == "artifact_coding":
        profile_id = "artifact_coding"
        reason = "resume_inherited_artifact_coding"
    else:
        # A PPT-shaped user request is an intent fact, not a Skill selection.  Keep the
        # initial Run on the neutral profile so the model can inspect the catalog and call
        # use_skill; only an explicit first-party selection (or an inherited profile on
        # resume) may activate the PPTD-specific contract.
        profile_id = "interactive"
        reason = "default_interactive"

    image_requirement = _normalized_image_requirement(frozen.get("ppt_image_requirement"))
    if image_requirement is None:
        image_requirement = _normalized_image_requirement(
            frozen.get("inherited_image_requirement"),
        )
    policy = _policy_payload(profile_id, image_requirement=image_requirement)
    snapshot = ExecutionProfileSnapshot(
        **policy,
        policy_hash=_sha256(policy),
        resolver_version=PROFILE_RESOLVER_VERSION,
        evidence_hash=str(frozen["evidence_hash"]),
        resolution_reason=reason,
    )
    return snapshot.model_dump(mode="json")


def is_first_party_ppt_skill(skill: Any) -> bool:
    """Return whether an authoritative Skill fact names the first-party PPTD Skill.

    This deliberately checks the trusted Skill id/name only.  User wording, a model command,
    or a broad ``ppt`` keyword cannot activate the first-party profile.
    """
    values = []
    if isinstance(skill, dict):
        values.extend((skill.get("id"), skill.get("skill_id"), skill.get("name")))
    else:
        values.extend((getattr(skill, "id", None), getattr(skill, "skill_id", None),
                       getattr(skill, "name", None)))
    return any(_PPTD_SKILL_RE.fullmatch(str(value or "").strip()) for value in values)


def profile_id_for_skill(skill: Any) -> ProfileId:
    """Map a trusted Skill fact to its actual authoring profile without routing by intent."""
    return "artifact_coding" if is_first_party_ppt_skill(skill) else "interactive"


def dynamic_profile_for_skill(base_profile: Any, skill: Any) -> dict[str, Any]:
    """Build the same-Run profile overlay after a model-authorized Skill load.

    The original accepted snapshot remains immutable; this returned snapshot is a durable
    execution fact for the loaded first-party Skill and is safe to use for subsequent tools or
    recovery.  Third-party/native Skills return the original interactive profile unchanged.
    """
    current = dict(unwrap_profile_dict(base_profile))
    if not is_first_party_ppt_skill(skill):
        return current
    if current.get("id") == "artifact_coding":
        return current
    qa = current.get("qa_contract") if isinstance(current.get("qa_contract"), dict) else {}
    image_requirement = _normalized_image_requirement(qa.get("image_requirement"))
    policy = _policy_payload("artifact_coding", image_requirement=image_requirement)
    evidence_hash = str(current.get("evidence_hash") or "")
    if not evidence_hash:
        evidence_hash = _sha256({
            "skill_id": str((skill or {}).get("id") if isinstance(skill, dict) else ""),
            "skill_name": str((skill or {}).get("name") if isinstance(skill, dict) else ""),
        })
    return ExecutionProfileSnapshot(
        **policy,
        policy_hash=_sha256(policy),
        resolver_version=PROFILE_RESOLVER_VERSION,
        evidence_hash=evidence_hash,
        resolution_reason="model_use_skill_first_party_ppt",
    ).model_dump(mode="json")


def validate_profile_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    """Validate persisted data and prove its policy payload has not been revised."""
    snapshot = ExecutionProfileSnapshot.model_validate(value)
    policy = {
        key: getattr(snapshot, key)
        for key in (
            "id", "version", "artifact_kind", "authoring_backend", "capability_policy",
            "loop_policy", "workspace_policy", "qa_contract", "fallback_policy",
        )
    }
    if _sha256(policy) != snapshot.policy_hash:
        raise ExecutionProfileError("execution profile policy hash mismatch")
    return snapshot.model_dump(mode="json")


def unwrap_profile_dict(profile: Any) -> dict[str, Any]:
    """Accept a snapshot, a {snapshot:...} record, or an ExecutionProfileSnapshot."""
    if hasattr(profile, "model_dump"):
        profile = profile.model_dump(mode="python")
    if not isinstance(profile, dict):
        return {}
    snap = profile.get("snapshot")
    if isinstance(snap, dict) and (snap.get("id") or snap.get("loop_policy")):
        return snap
    return profile


def effective_loop_policy(profile: Any) -> dict[str, Any]:
    """loop_policy that actually governs the Run; nested records must not fall back to global 150k."""
    policy = unwrap_profile_dict(profile).get("loop_policy")
    return dict(policy) if isinstance(policy, dict) else {}
