"""Explicit runtime registrations. The identity registry never imports this module."""

from __future__ import annotations

from .campus_services.runtime import CAMPUS_RUNTIME_POLICY
from .presentation.runtime import PRESENTATION_RUNTIME_POLICY
from .interview.runtime import INTERVIEW_RUNTIME_POLICY
from .registry import SUPPORTED_PRESETS, normalize_preset
from .runtime_types import BuiltinRuntimePolicy

BUILTIN_RUNTIME_POLICIES = (CAMPUS_RUNTIME_POLICY, PRESENTATION_RUNTIME_POLICY, INTERVIEW_RUNTIME_POLICY)
_POLICIES = {policy.preset: policy for policy in BUILTIN_RUNTIME_POLICIES}
if len(_POLICIES) != len(BUILTIN_RUNTIME_POLICIES) or set(_POLICIES) != SUPPORTED_PRESETS:
    raise RuntimeError("Builtin identity and runtime registrations must match exactly")


def get_builtin_runtime_policy(value: object) -> BuiltinRuntimePolicy | None:
    key = normalize_preset(value)
    if not key:
        return None
    try:
        return _POLICIES[key]
    except KeyError as exc:
        raise ValueError(f"Unknown builtin assistant preset: {key}") from exc


def builtin_tool_build_options(
    policy: BuiltinRuntimePolicy | None,
    *,
    snapshot: dict | None = None,
    image_delivery_mode: str | None = None,
) -> dict:
    """Identical builder inputs for first turns and HITL/recovery turns."""
    return {
        **({"additional_tools": policy.additional_tools} if policy and policy.additional_tools else {}),
        "image_delivery_mode": (
            policy.image_delivery_mode
            if policy and policy.image_delivery_mode is not None
            else image_delivery_mode
        ),
        "allowed_domains": (
            (snapshot or {}).get("official_domains")
            if policy and policy.official_domain_scope
            else None
        ),
        "allowed_tool_names": (
            set(policy.build_allowed_tool_names)
            if policy and policy.build_allowed_tool_names is not None
            else None
        ),
    }
