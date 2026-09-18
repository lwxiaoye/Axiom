import pytest
import asyncio

from app.services.chat import turn_prepare
from app.services.chat.builtin_assistants.presentation.policy import (
    PRESENTATION_FORBIDDEN_TOOL_NAMES,
    PRESENTATION_PINNED_TOOL_NAMES,
    presentation_allowed_tools,
    presentation_search_hint,
    presentation_turn_guard,
    resolve_ppt_studio_skill_id,
    without_presentation_forbidden_tools,
)


@pytest.mark.asyncio
async def test_prepare_turn_does_not_preload_platform_ppt_skill(monkeypatch) -> None:
    records = [
        {
            "skillId": "random-ppt-id",
            "name": "pptx",
            "description": "SVG 转可编辑 PPTX",
            "enabled": 1,
        }
    ]

    async def catalog(_token: str) -> list:
        return records

    async def no_memory(*_args, **_kwargs) -> list:
        return []

    async def no_personalization(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(turn_prepare, "_get_catalog_records", catalog)
    monkeypatch.setattr(turn_prepare.memory_service, "recall", no_memory)
    monkeypatch.setattr(turn_prepare.personalization_service, "prompt_block", no_personalization)

    result = await turn_prepare.prepare_turn(
        message="制作一份 8 页产品发布会 PPT",
        user_context=None,
        subagent_id=None,
        knowledge_ids=None,
        selected_knowledge=None,
        web_search=False,
        image_urls=[],
        resolved_model="test-model",
        newapi_key="",
        skill_ids=None,
        token="token",
        user_id="user",
        thread_id="thread",
    )

    assert result.effective_skill_ids == []
    assert result.trusted_skills == []
    assert result.selected_skill_records == []
    assert "自主发现" in result.skill_catalog_block
    assert "use_skill" in result.skill_catalog_block
    assert "不自动注入统一的 PPT 栈" in result.skill_catalog_block


def test_format_skill_block_injects_exact_sandbox_path() -> None:
    from app.services.chat.turn_context_builder import _format_skill_block

    block = _format_skill_block({
        "id": "extract_abc",
        "record_id": "2088139870284460033",
        "name": "ppt studio",
        "instructions": "premium",
    })
    assert "沙箱精确目录：/workspace/skills/ppt_studio_" in block
    assert "禁止 ls/find 猜目录" in block
    assert "/workspace/tmp/ppt-project" in block
    assert "不得改用 spec.json、create_deck.py" in block
    assert "不要默认暗色、黑金、照片铺底" in block


@pytest.mark.asyncio
async def test_prepare_turn_timeout_fallback_keeps_skill_unread(monkeypatch) -> None:
    records = [{
        "skillId": "ppt-studio",
        "name": "ppt studio",
        "description": "创建可编辑 PPTX",
        "enabled": 1,
    }]
    calls = 0

    async def catalog(_token: str) -> list:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(1)
        return records

    async def no_memory(*_args, **_kwargs) -> list:
        return []

    async def no_personalization(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(turn_prepare, "_get_catalog_records", catalog)
    monkeypatch.setattr(turn_prepare.memory_service, "recall", no_memory)
    monkeypatch.setattr(turn_prepare.personalization_service, "prompt_block", no_personalization)
    monkeypatch.setattr(turn_prepare.settings, "TURN_PREPARE_BUDGET_SECONDS", 0.5)
    monkeypatch.setattr(turn_prepare.settings, "AUTO_ROUTE_ENABLED", False)

    result = await turn_prepare.prepare_turn(
        message="做一份高级感产品 PPT",
        user_context=None,
        subagent_id=None,
        knowledge_ids=None,
        selected_knowledge=None,
        web_search=False,
        image_urls=[],
        resolved_model="test-model",
        newapi_key="",
        skill_ids=None,
        token="token",
        user_id="user",
        thread_id="thread",
    )

    assert result.effective_skill_ids == []
    assert result.trusted_skills == []
    assert result.selected_skill_records == []
    assert "自主发现" in result.skill_catalog_block
    assert "use_skill" in result.skill_catalog_block


@pytest.mark.asyncio
async def test_explicit_selection_only_keeps_acl_catalog_metadata(monkeypatch) -> None:
    records = [{"skillId": "ppt-studio", "id": "record-1", "name": "ppt-studio", "description": "PPT", "enabled": 1}]

    async def catalog(_token: str) -> list:
        return records

    async def no_memory(*_args, **_kwargs) -> list:
        return []

    async def no_personalization(*_args, **_kwargs) -> str:
        return ""

    async def no_lesson(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(turn_prepare, "_get_catalog_records", catalog)
    monkeypatch.setattr(turn_prepare.memory_service, "recall", no_memory)
    monkeypatch.setattr(turn_prepare.personalization_service, "prompt_block", no_personalization)
    monkeypatch.setattr(turn_prepare, "_lesson_block", no_lesson)
    result = await turn_prepare.prepare_turn(
        message="做一份 PPT", user_context=None, subagent_id=None, knowledge_ids=None,
        selected_knowledge=None, web_search=False, image_urls=[], resolved_model="test-model",
        newapi_key="", skill_ids=["ppt-studio"], token="token", user_id="user", thread_id="thread",
    )
    assert result.trusted_skills == []
    assert result.selected_skill_records == [{
        "id": "ppt-studio", "record_id": "record-1", "name": "ppt-studio",
        "description": "PPT", "version": None, "selected": True,
    }]


def test_presentation_preset_resolves_only_enabled_exact_ppt_studio() -> None:
    records = [
        {"skillId": "pptx-maker", "name": "PPT 助手", "enabled": 1},
        {"skillId": "disabled-studio", "name": "ppt-studio", "enabled": 0},
        {"skillId": "tenant-ppt-studio", "name": "ppt studio", "enabled": 1},
    ]
    assert resolve_ppt_studio_skill_id(records) == "tenant-ppt-studio"
    assert resolve_ppt_studio_skill_id(records[:2]) == ""


def test_presentation_preset_physically_removes_skill_and_subagent_tools() -> None:
    class Tool:
        def __init__(self, name: str):
            self.name = name

    tools = [Tool(name) for name in (
        "bash", "use_skill", "recommend_agent", "call_subagent", "delegate_task",
        "update_plan", "publish_ppt_artifact",
    )]
    assert [tool.name for tool in without_presentation_forbidden_tools(tools)] == [
        "bash", "update_plan", "publish_ppt_artifact",
    ]


def test_presentation_preset_keeps_search_web_and_fetch_ppt_asset() -> None:
    class Tool:
        def __init__(self, name: str):
            self.name = name

    tools = [Tool(name) for name in (
        "search_web", "fetch_ppt_asset", "bash", "use_skill", "call_subagent",
    )]
    kept = [tool.name for tool in without_presentation_forbidden_tools(tools)]
    assert "search_web" in kept
    assert "fetch_ppt_asset" in kept
    assert "search_web" not in PRESENTATION_FORBIDDEN_TOOL_NAMES
    assert "search_web" in PRESENTATION_PINNED_TOOL_NAMES
    assert "fetch_ppt_asset" in PRESENTATION_PINNED_TOOL_NAMES
    assert "search_web" in presentation_turn_guard()
    assert "fetch_ppt_asset" in presentation_search_hint()


def test_presentation_tool_surface_is_ppt_skill_only() -> None:
    class Tool:
        def __init__(self, name: str):
            self.name = name

    assert PRESENTATION_PINNED_TOOL_NAMES == frozenset({
        "read_file",
        "glob",
        "write_file",
        "edit_file",
        "bash",
        "update_plan",
        "search_web",
        "fetch_ppt_asset",
        "publish_ppt_artifact",
        "ask_user_choice",
    })
    assert PRESENTATION_PINNED_TOOL_NAMES.isdisjoint(PRESENTATION_FORBIDDEN_TOOL_NAMES)
    assert PRESENTATION_FORBIDDEN_TOOL_NAMES >= {
        "use_skill", "recommend_agent", "call_subagent", "delegate_task",
        "search_capabilities",
    }
    names = [
        *PRESENTATION_PINNED_TOOL_NAMES,
        "use_skill", "recommend_agent", "call_subagent", "delegate_task",
        "search_capabilities", "download_url", "search_knowledge", "browser_fetch",
        "remember_fact",
    ]
    kept = [tool.name for tool in presentation_allowed_tools([Tool(n) for n in names])]
    assert set(kept) == set(PRESENTATION_PINNED_TOOL_NAMES)
    assert "call_subagent" not in kept
    assert "recommend_agent" not in kept


def test_presentation_call_sites_close_catalog() -> None:
    import inspect

    from app.services.agent_harness import orchestrator
    from app.services.chat import main_tool_turn

    turn_src = inspect.getsource(main_tool_turn)
    orch_src = inspect.getsource(orchestrator)
    assert turn_src.count("runtime_policy.validate_tools(tools)") >= 2
    assert orch_src.count("runtime_policy.validate_tools(tools)") >= 2
    assert "without_presentation_forbidden_tools(tools)" not in turn_src
    assert "without_presentation_forbidden_tools(tools)" not in orch_src
    rec_idx = turn_src.index("build_recommend_agent_tool")
    pres_idx = turn_src.index("tools = runtime_policy.bound_tools(tools)")
    assert pres_idx < rec_idx


@pytest.mark.asyncio
async def test_presentation_preset_authoritatively_preloads_ppt_studio(monkeypatch) -> None:
    records = [{
        "skillId": "tenant-ppt-studio", "id": "record-1", "name": "ppt-studio",
        "description": "创建可编辑 PPTX", "enabled": 1,
    }]

    async def catalog(_token: str) -> list:
        return records

    async def trusted(skill_ids: list[str], _token: str) -> list[dict]:
        assert skill_ids == ["tenant-ppt-studio"]
        return [{
            "id": "tenant-ppt-studio", "record_id": "record-1",
            "name": "ppt-studio", "instructions": "authoritative skill instructions",
        }]

    async def no_memory(*_args, **_kwargs) -> list:
        return []

    async def no_personalization(*_args, **_kwargs) -> str:
        return ""

    async def no_lesson(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(turn_prepare, "_get_catalog_records", catalog)
    monkeypatch.setattr(turn_prepare, "_fetch_trusted_skills", trusted)
    monkeypatch.setattr(turn_prepare.memory_service, "recall", no_memory)
    monkeypatch.setattr(turn_prepare.personalization_service, "prompt_block", no_personalization)
    monkeypatch.setattr(turn_prepare, "_lesson_block", no_lesson)

    result = await turn_prepare.prepare_turn(
        message="做一份两页功能验证演示文稿",
        user_context=None, subagent_id="must-not-survive", knowledge_ids=None,
        selected_knowledge=None, web_search=False, image_urls=[],
        resolved_model="test-model", newapi_key="", skill_ids=None, token="token",
        user_id="user", thread_id="thread", assistant_preset="presentation",
    )

    assert result.effective_subagent_id is None
    assert result.effective_skill_ids == ["tenant-ppt-studio"]
    assert result.trusted_skills[0]["instructions"] == "authoritative skill instructions"
    assert result.selected_skill_records == [{
        "id": "tenant-ppt-studio", "record_id": "record-1", "name": "ppt-studio",
        "description": "创建可编辑 PPTX", "version": None, "selected": True,
    }]
    assert result.skill_catalog_block == ""


@pytest.mark.asyncio
async def test_presentation_preset_optional_memory_failure_does_not_mask_valid_skill(monkeypatch) -> None:
    records = [{
        "skillId": "tenant-ppt-studio", "id": "record-1", "name": "ppt-studio",
        "description": "创建可编辑 PPTX", "enabled": 1,
    }]

    async def catalog(_token: str) -> list:
        return records

    async def trusted(_skill_ids: list[str], _token: str) -> list[dict]:
        return [{
            "id": "tenant-ppt-studio", "record_id": "record-1",
            "name": "ppt-studio", "instructions": "authoritative skill instructions",
        }]

    async def failed_memory(*_args, **_kwargs) -> list:
        raise RuntimeError("memory backend unavailable")

    async def failed_personalization(*_args, **_kwargs) -> str:
        raise RuntimeError("personalization backend unavailable")

    async def no_lesson(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(turn_prepare, "_get_catalog_records", catalog)
    monkeypatch.setattr(turn_prepare, "_fetch_trusted_skills", trusted)
    monkeypatch.setattr(turn_prepare.memory_service, "recall", failed_memory)
    monkeypatch.setattr(turn_prepare.personalization_service, "prompt_block", failed_personalization)
    monkeypatch.setattr(turn_prepare, "_lesson_block", no_lesson)

    result = await turn_prepare.prepare_turn(
        message="做一份演示文稿", user_context=None, subagent_id=None,
        knowledge_ids=None, selected_knowledge=None, web_search=False, image_urls=[],
        resolved_model="test-model", newapi_key="", skill_ids=None, token="token",
        user_id="user", thread_id="thread", assistant_preset="presentation",
    )

    assert result.effective_skill_ids == ["tenant-ppt-studio"]
    assert result.trusted_skills[0]["instructions"] == "authoritative skill instructions"
    assert result.memory_block == ""


@pytest.mark.asyncio
async def test_presentation_preset_optional_memory_timeout_does_not_mask_valid_skill(monkeypatch) -> None:
    records = [{
        "skillId": "tenant-ppt-studio", "id": "record-1", "name": "ppt-studio",
        "description": "创建可编辑 PPTX", "enabled": 1,
    }]

    async def catalog(_token: str) -> list:
        return records

    async def trusted(_skill_ids: list[str], _token: str) -> list[dict]:
        return [{
            "id": "tenant-ppt-studio", "record_id": "record-1",
            "name": "ppt-studio", "instructions": "authoritative skill instructions",
        }]

    async def slow_memory(*_args, **_kwargs) -> list:
        await asyncio.sleep(2)
        return []

    async def no_personalization(*_args, **_kwargs) -> str:
        return ""

    async def no_lesson(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(turn_prepare, "_get_catalog_records", catalog)
    monkeypatch.setattr(turn_prepare, "_fetch_trusted_skills", trusted)
    monkeypatch.setattr(turn_prepare.memory_service, "recall", slow_memory)
    monkeypatch.setattr(turn_prepare.personalization_service, "prompt_block", no_personalization)
    monkeypatch.setattr(turn_prepare, "_lesson_block", no_lesson)
    monkeypatch.setattr(turn_prepare.settings, "TURN_PREPARE_BUDGET_SECONDS", 0.5)

    result = await turn_prepare.prepare_turn(
        message="做一份演示文稿", user_context=None, subagent_id=None,
        knowledge_ids=None, selected_knowledge=None, web_search=False, image_urls=[],
        resolved_model="test-model", newapi_key="", skill_ids=None, token="token",
        user_id="user", thread_id="thread", assistant_preset="presentation",
    )

    assert result.effective_skill_ids == ["tenant-ppt-studio"]
    assert result.trusted_skills[0]["instructions"] == "authoritative skill instructions"
    assert result.memory_block == ""


@pytest.mark.asyncio
async def test_presentation_preset_fails_closed_when_ppt_studio_is_unavailable(monkeypatch) -> None:
    async def catalog(_token: str) -> list:
        return [{"skillId": "pptx-maker", "name": "PPT 助手", "enabled": 1}]

    async def no_memory(*_args, **_kwargs) -> list:
        return []

    async def no_personalization(*_args, **_kwargs) -> str:
        return ""

    async def no_lesson(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(turn_prepare, "_get_catalog_records", catalog)
    monkeypatch.setattr(turn_prepare.memory_service, "recall", no_memory)
    monkeypatch.setattr(turn_prepare.personalization_service, "prompt_block", no_personalization)
    monkeypatch.setattr(turn_prepare, "_lesson_block", no_lesson)

    # 目录里没有 ppt-studio 是配置性错误：必须是终态类型（ConfigurationRunError），
    # 否则 pump 会把它当瞬时故障送进 waiting_system 无限自动恢复，用户永远看不到原因。
    from app.services.agent_harness.public_errors import ConfigurationRunError, TerminalRunError

    with pytest.raises(ConfigurationRunError, match="没有已启用的 ppt-studio") as error:
        await turn_prepare.prepare_turn(
            message="做一份演示文稿", user_context=None, subagent_id=None,
            knowledge_ids=None, selected_knowledge=None, web_search=False, image_urls=[],
            resolved_model="test-model", newapi_key="", skill_ids=None, token="token",
            user_id="user", thread_id="thread", assistant_preset="presentation",
        )
    assert isinstance(error.value, TerminalRunError)
    assert error.value.public_message.startswith("演示文稿助手暂不可用")
