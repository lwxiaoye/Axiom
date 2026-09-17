"""Identity-only contract for the shared builtin-assistant registry."""

from app.services.chat.builtin_assistants import (
    CAMPUS_PRESET,
    PRESENTATION_PRESET,
    get_builtin_assistant_by_id,
    get_builtin_assistant_by_preset,
    is_campus_preset,
    origin_for_preset,
    preset_from_thread_origin,
)


def test_builtin_registry_is_identity_only():
    campus = get_builtin_assistant_by_preset("campus_services")
    presentation = get_builtin_assistant_by_id("builtin:presentation")
    assert campus is not None
    assert campus["preset"] == CAMPUS_PRESET
    assert campus["origin"] == "campus_services"
    assert campus["ui_policy_key"] == "campus_readonly"
    assert presentation is not None
    assert presentation["preset"] == PRESENTATION_PRESET
    assert presentation["ui_policy_key"] == "presentation_authoring"
    assert "system_prompt" not in campus
    assert is_campus_preset("campus_services")
    assert origin_for_preset("presentation") == "presentation"
    assert preset_from_thread_origin("campus_services") == "campus_services"
