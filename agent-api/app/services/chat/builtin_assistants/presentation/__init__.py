"""Presentation module. Import policy/runtime explicitly, not through identity."""

from .definition import (
    PRESENTATION_APP_ID,
    PRESENTATION_IDENTITY,
    PRESENTATION_PRESET,
    PRESENTATION_THREAD_ORIGIN,
    is_presentation_preset,
)

__all__ = [
    "PRESENTATION_APP_ID",
    "PRESENTATION_IDENTITY",
    "PRESENTATION_PRESET",
    "PRESENTATION_THREAD_ORIGIN",
    "is_presentation_preset",
]
