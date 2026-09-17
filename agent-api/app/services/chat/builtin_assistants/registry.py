"""Small identity registry for built-in main-chat assistants.

Product/UI identity only: stable app id, preset, Thread.origin and uiPolicy key.
Execution strategy lives in each assistant module and a separate runtime registry.
"""

from __future__ import annotations

from typing import Optional

from .campus_services.definition import (
    CAMPUS_APP_ID,
    CAMPUS_DEFINITION,
    CAMPUS_IDENTITY,
    CAMPUS_PRESET,
    CAMPUS_THREAD_ORIGIN,
    is_campus_preset,
)
from .presentation.definition import (
    PRESENTATION_APP_ID,
    PRESENTATION_DEFINITION,
    PRESENTATION_IDENTITY,
    PRESENTATION_PRESET,
    PRESENTATION_THREAD_ORIGIN,
    is_presentation_preset,
)
from app.services.chat.builtin_assistants.types import BuiltinAssistantIdentity
from .interview.definition import INTERVIEW_DEFINITION

BUILTIN_ASSISTANT_DEFINITIONS = (CAMPUS_DEFINITION, PRESENTATION_DEFINITION, INTERVIEW_DEFINITION)
_ASSISTANTS = tuple(definition.identity for definition in BUILTIN_ASSISTANT_DEFINITIONS)
SUPPORTED_PRESETS = frozenset(item["preset"] for item in _ASSISTANTS)
PRESET_THREAD_ORIGINS = {
    item["preset"]: item["origin"] for item in _ASSISTANTS
}
ORIGIN_TO_PRESET = {origin: preset for preset, origin in PRESET_THREAD_ORIGINS.items()}


def normalize_preset(value: object) -> str:
    return str(value or "").strip().lower()


def is_supported_preset(value: object) -> bool:
    return normalize_preset(value) in SUPPORTED_PRESETS


def origin_for_preset(value: object) -> str:
    return PRESET_THREAD_ORIGINS.get(normalize_preset(value), "")


def preset_from_thread_origin(origin: object) -> str:
    return ORIGIN_TO_PRESET.get(str(origin or "").strip(), "")


def get_builtin_assistant_by_preset(value: object) -> Optional[BuiltinAssistantIdentity]:
    key = normalize_preset(value)
    for item in _ASSISTANTS:
        if item["preset"] == key:
            return item
    return None


def get_builtin_assistant_by_id(value: object) -> Optional[BuiltinAssistantIdentity]:
    key = str(value or "").strip()
    if not key:
        return None
    for item in _ASSISTANTS:
        if item["app_id"] == key:
            return item
    return None


def is_builtin_assistant_id(value: object) -> bool:
    return get_builtin_assistant_by_id(value) is not None
