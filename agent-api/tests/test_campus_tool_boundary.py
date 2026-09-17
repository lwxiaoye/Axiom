import pytest

from app.services.chat.builtin_assistants.campus_services.policy import (
    CAMPUS_ALLOWED_TOOL_NAMES,
    CampusToolBoundaryError,
    bound_campus_tools,
    enforce_campus_tool_boundary,
    retain_campus_allowed_tools,
)


class Tool:
    def __init__(self, name: str):
        self.name = name


def test_campus_allowlist_is_exactly_two_tools():
    assert CAMPUS_ALLOWED_TOOL_NAMES == {"search_knowledge", "search_web"}
    tools = [Tool("search_knowledge"), Tool("search_web")]
    kept = enforce_campus_tool_boundary(tools, require_knowledge=True)
    assert [tool.name for tool in kept] == ["search_knowledge", "search_web"]


def test_future_extra_tool_aborts_campus_run():
    tools = [Tool("search_knowledge"), Tool("search_web"), Tool("new_future_tool")]
    with pytest.raises(CampusToolBoundaryError):
        enforce_campus_tool_boundary(tools, require_knowledge=True)


@pytest.mark.parametrize("name", [
    "bash", "use_skill", "ask_user_choice", "get_current_time",
    "call_subagent", "search_capabilities", "browser_fetch",
])
def test_forbidden_tools_abort(name: str):
    tools = [Tool("search_knowledge"), Tool("search_web"), Tool(name)]
    with pytest.raises(CampusToolBoundaryError):
        enforce_campus_tool_boundary(tools)


def test_retain_drops_extras_then_bound_succeeds():
    tools = [
        Tool("search_knowledge"),
        Tool("search_web"),
        Tool("bash"),
        Tool("browser_act"),
        Tool("write_file"),
        Tool("get_current_time"),
    ]
    kept = retain_campus_allowed_tools(tools)
    assert [tool.name for tool in kept] == ["search_knowledge", "search_web"]
    assert [tool.name for tool in bound_campus_tools(tools, require_knowledge=True)] == [
        "search_knowledge",
        "search_web",
    ]


def test_missing_knowledge_tool_aborts_even_after_retain():
    with pytest.raises(CampusToolBoundaryError):
        bound_campus_tools([Tool("search_web")], require_knowledge=True)


def test_campus_call_sites_filter_before_asserting():
    import inspect

    from app.services.agent_harness import orchestrator
    from app.services.chat import main_tool_turn

    turn_src = inspect.getsource(main_tool_turn)
    orch_src = inspect.getsource(orchestrator)
    for source in (turn_src, orch_src):
        assert "**builtin_tool_build_options(" in source
        assert "tools = runtime_policy.bound_tools(tools)" in source
        assert "tools = runtime_policy.validate_tools(tools)" in source


@pytest.mark.asyncio
async def test_build_tools_campus_allowlist_never_registers_extras():
    from app.services.chat.tools import build_tools

    tools = await build_tools(
        token="",
        knowledge_ids=["kb-campus"],
        web_enabled=True,
        user_id="u-campus",
        allowed_tool_names=CAMPUS_ALLOWED_TOOL_NAMES,
    )
    names = {tool.name for tool in tools}
    assert names == {"search_knowledge", "search_web"}
    assert bound_campus_tools(tools, require_knowledge=True) is not None
