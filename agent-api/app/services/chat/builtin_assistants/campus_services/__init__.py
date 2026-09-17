"""Campus module. Import policy/config explicitly; identity stays lightweight."""

from .definition import (
    CAMPUS_APP_ID,
    CAMPUS_IDENTITY,
    CAMPUS_PRESET,
    CAMPUS_THREAD_ORIGIN,
    is_campus_preset,
)

__all__ = [
    "CAMPUS_APP_ID",
    "CAMPUS_IDENTITY",
    "CAMPUS_PRESET",
    "CAMPUS_THREAD_ORIGIN",
    "is_campus_preset",
]
