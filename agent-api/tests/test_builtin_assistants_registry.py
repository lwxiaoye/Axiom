import inspect

from app.services.chat.builtin_assistants import registry
from app.services.chat.builtin_assistants.presentation.policy import (
    PRESENTATION_FORBIDDEN_TOOL_NAMES,
    PRESENTATION_PINNED_TOOL_NAMES,
    presentation_allowed_tools,
    resolve_ppt_studio_skill_id,
    without_presentation_forbidden_tools,
)
from app.services.chat.builtin_assistants.presentation.policy import (
    PRESENTATION_PRESET,
    is_presentation_preset,
    presentation_turn_guard,
)


def test_registry_identifies_presets_by_id_not_display_name() -> None:
    campus = registry.get_builtin_assistant_by_preset("campus_services")
    presentation = registry.get_builtin_assistant_by_id("builtin:presentation")
    assert campus is not None
    assert campus["app_id"] == "builtin:campus-services"
    assert campus["ui_policy_key"] == "campus_readonly"
    assert presentation is not None
    assert presentation["preset"] == "presentation"
    assert presentation["ui_policy_key"] == "presentation_authoring"
    assert registry.is_builtin_assistant_id("builtin:presentation")
    assert not registry.is_builtin_assistant_id("校园百事通")
    assert registry.preset_from_thread_origin("presentation") == "presentation"
    assert registry.origin_for_preset("presentation") == "presentation"


def test_registry_does_not_embed_campus_tool_allowlist_or_ppt_hooks() -> None:
    source = inspect.getsource(registry)
    assert "search_knowledge" not in source
    assert "search_web" not in source
    assert "ppt-studio" not in source
    assert "CAMPUS_ALLOWED" not in source
    from app.services.chat.builtin_assistants import campus_services
    campus_identity_src = inspect.getsource(campus_services)
    assert "search_knowledge" not in campus_identity_src
    assert "CAMPUS_ALLOWED" not in campus_identity_src


def test_presentation_acceptance_forces_standard_not_plan_or_research() -> None:
    from app.services.agent_harness import orchestrator as harness_orchestrator
    from app.services.chat.builtin_assistants.runtime import get_builtin_runtime_policy

    source = inspect.getsource(harness_orchestrator.HarnessOrchestrator)
    assert 'if is_presentation_preset(assistant_preset) and route != "planning":' not in source
    assert 'if runtime_policy:\n            route = "agent"' in source
    assert 'agent_mode = runtime_policy.forced_resume_mode' in source
    assert get_builtin_runtime_policy("presentation").forced_resume_mode == "standard"
    assert "不要切换到计划模式或深度研究" in presentation_turn_guard()
    assert "若本轮是计划模式" not in presentation_turn_guard()


def test_presentation_module_keeps_execution_contract() -> None:
    assert PRESENTATION_PRESET == "presentation"
    assert is_presentation_preset("presentation")
    assert "ppt-studio" in presentation_turn_guard()
    assert "use_skill" in PRESENTATION_FORBIDDEN_TOOL_NAMES
    assert resolve_ppt_studio_skill_id([
        {"skillId": "ppt-studio", "name": "ppt-studio", "enabled": 1},
    ]) == "ppt-studio"

    class Tool:
        def __init__(self, name: str) -> None:
            self.name = name

    kept = without_presentation_forbidden_tools([Tool("read_file"), Tool("use_skill")])
    assert [tool.name for tool in kept] == ["read_file"]
    assert "search_web" in PRESENTATION_PINNED_TOOL_NAMES
    assert "search_web" not in PRESENTATION_FORBIDDEN_TOOL_NAMES
    assert PRESENTATION_PINNED_TOOL_NAMES.isdisjoint({
        "use_skill", "search_capabilities",
    })
    dropped = presentation_allowed_tools([
        Tool("bash"), Tool("use_skill"), Tool("search_capabilities"),
    ])
    assert [tool.name for tool in dropped] == ["bash"]
