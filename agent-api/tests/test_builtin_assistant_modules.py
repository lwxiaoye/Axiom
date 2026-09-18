"""Module registration, request/restore parity and shared-Harness integration."""

import ast
import inspect
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.core.auth import UserContext
from app.services.chat.builtin_assistants import registry
from app.services.chat.builtin_assistants.campus_services import runtime_service
from app.services.chat.builtin_assistants.campus_services.policy import CampusToolBoundaryError
from app.services.chat.builtin_assistants.runtime import (
    BUILTIN_RUNTIME_POLICIES,
    builtin_tool_build_options,
    get_builtin_runtime_policy,
)


def test_identity_runtime_catalog_and_request_enums_match():
    from app.schemas.schemas import ChatRequest, ThreadItem
    from app.services.chat.builtin_app_access import BUILTIN_APP_SPECS

    presets = registry.SUPPORTED_PRESETS
    assert {policy.preset for policy in BUILTIN_RUNTIME_POLICIES} == presets
    assert {spec.preset for spec in BUILTIN_APP_SPECS} == presets
    for model in (ChatRequest, ThreadItem):
        variants = model.model_json_schema()["properties"]["assistant_preset"]["anyOf"]
        assert set(next(item["enum"] for item in variants if "enum" in item)) == presets
    for key in ("preset", "app_id", "origin", "ui_policy_key"):
        values = [item.identity[key] for item in registry.BUILTIN_ASSISTANT_DEFINITIONS]
        assert len(values) == len(set(values))
    assert len({item.route for item in BUILTIN_APP_SPECS}) == len(presets)


def test_identity_import_does_not_load_execution_or_database_modules():
    script = (
        "import sys; from app.services.chat.builtin_assistants import registry; "
        "assert registry.SUPPORTED_PRESETS == {'campus_services', 'presentation', 'interview'}; "
        "assert not any(name.endswith(('.runtime', '.policy', '.prepare', '.config_service')) "
        "for name in sys.modules if name.startswith('app.services.chat.builtin_assistants')); "
        "assert 'app.core.database' not in sys.modules; "
        "assert 'app.services.agent_harness.orchestrator' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("value", ["unknown", "校园百事通", "builtin:presentation"])
def test_unknown_runtime_policy_never_falls_back_to_unrestricted_main(value):
    with pytest.raises(ValueError):
        get_builtin_runtime_policy(value)


def test_ordinary_main_keeps_its_existing_builder_options():
    assert get_builtin_runtime_policy(None) is None
    assert get_builtin_runtime_policy("") is None
    assert builtin_tool_build_options(None, image_delivery_mode="artifact_only") == {
        "image_delivery_mode": "artifact_only",
        "allowed_domains": None,
        "allowed_tool_names": None,
    }


@pytest.mark.parametrize("preset", ["campus_services", "presentation"])
def test_first_and_resume_tool_policies_keep_exact_existing_surface(preset):
    policy = get_builtin_runtime_policy(preset)
    allowed = (
        {"search_knowledge", "search_web"}
        if preset == "campus_services"
        else {
            "read_file", "glob", "write_file", "edit_file", "bash", "update_plan",
            "search_web", "fetch_ppt_asset", "publish_ppt_artifact", "ask_user_choice",
        }
    )
    tools = [SimpleNamespace(name=name) for name in sorted(allowed)]
    extras = [SimpleNamespace(name=name) for name in ["use_skill", "call_subagent", "future_tool"]]
    first = policy.bound_tools(tools + extras)
    resumed = policy.validate_tools(policy.bound_tools(tools + extras))
    assert [tool.name for tool in first] == [tool.name for tool in resumed] == sorted(allowed)
    assert policy.pinned_tool_names == allowed
    assert all(item is original for item, original in zip(first, tools))
    if preset == "campus_services":
        with pytest.raises(CampusToolBoundaryError):
            policy.validate_tools(first + extras)
        with pytest.raises(CampusToolBoundaryError):
            policy.bound_tools([SimpleNamespace(name="search_web")])


@pytest.mark.asyncio
async def test_registered_campus_options_build_only_official_knowledge_and_web_tools():
    from app.services.chat.tools import build_tools

    policy = get_builtin_runtime_policy("campus_services")
    domains = [{"host": "school.edu.cn", "include_subdomains": True}]
    options = builtin_tool_build_options(
        policy,
        snapshot={"official_domains": domains},
        image_delivery_mode="artifact_only",
    )
    assert options == {
        "image_delivery_mode": "chat_inline",
        "allowed_domains": domains,
        "allowed_tool_names": {"search_web", "search_knowledge"},
    }
    tools = await build_tools(
        token="", knowledge_ids=["kb-campus"], web_enabled=True, user_id="module-test",
        **options,
    )
    assert {tool.name for tool in policy.bound_tools(tools)} == {"search_knowledge", "search_web"}
    assert builtin_tool_build_options(
        policy, snapshot={"official_domains": []},
    )["allowed_domains"] == []
    assert builtin_tool_build_options(
        get_builtin_runtime_policy("presentation"), image_delivery_mode="artifact_only",
    ) == {
        "image_delivery_mode": "artifact_only", "allowed_domains": None, "allowed_tool_names": None,
    }


@pytest.mark.parametrize("preset", ["campus_services", "presentation"])
@pytest.mark.parametrize("field", ["subagent_id", "skill_ids", "selected_skills"])
@pytest.mark.asyncio
async def test_request_module_preserves_rejection_of_client_skill_overrides(preset, field):
    with pytest.raises(HTTPException) as error:
        await get_builtin_runtime_policy(preset).prepare_request({field: ["not-allowed"]})
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_campus_request_keeps_uploaded_image_and_published_snapshot(monkeypatch):
    snapshot = {
        "code": "campus_services", "release_id": "release-1", "knowledge_ids": ["kb-1"],
        "official_domains": [{"host": "school.edu.cn"}], "model_id": "published-model",
    }
    resolve = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(runtime_service, "resolve_published_snapshot", resolve)
    image = {"kind": "image", "file_id": "image-1", "filename": "notice.png"}
    kwargs = {"attachments": [image], "user_context": SimpleNamespace(tenant_id="tenant-1")}
    await get_builtin_runtime_policy("campus_services").prepare_request(kwargs)
    resolve.assert_awaited_once_with("tenant-1")
    assert kwargs["attachments"] == [image]
    assert kwargs["assistant_preset_snapshot"] is snapshot
    assert kwargs["model"] == "published-model"
    assert kwargs["knowledge_ids"] == ["kb-1"]
    assert kwargs["skill_ids"] == [] and kwargs["selected_knowledge"] == []
    assert kwargs["agent_mode"] == "standard"


@pytest.mark.asyncio
async def test_campus_config_failure_remains_an_explicit_error(monkeypatch):
    from app.services.chat.builtin_assistants.campus_services.config_service import CampusConfigError

    monkeypatch.setattr(runtime_service, "resolve_published_snapshot", AsyncMock(
        side_effect=CampusConfigError(503, "校园百事通尚未发布配置"),
    ))
    with pytest.raises(HTTPException) as error:
        await get_builtin_runtime_policy("campus_services").prepare_request({})
    assert error.value.status_code == 503
    assert "尚未发布" in error.value.detail


def test_presentation_resume_still_requires_live_skill_instructions():
    from app.services.agent_harness.public_errors import ConfigurationRunError

    policy = get_builtin_runtime_policy("presentation")
    for skills in ([], [{"instructions": ""}]):
        # 续接时 ppt-studio 消失/说明为空同样是配置性错误：终态 + 原因可见，不进自动恢复
        with pytest.raises(ConfigurationRunError, match="演示文稿助手暂不可用"):
            policy.validate_resume_skills(skills)
    policy.validate_resume_skills([{"instructions": "Trusted instructions"}])


@pytest.mark.parametrize(("preset", "requested_mode", "expected_mode", "expected_route"), [
    ("", "standard", "standard", "direct_answer"),
    ("", "plan", "plan", "planning"),
    ("presentation", "plan", "standard", "agent"),
    ("presentation", "research", "standard", "agent"),
    ("campus_services", "standard", "standard", "agent"),
])
@pytest.mark.asyncio
async def test_real_acceptance_uses_module_policy_and_preserves_shared_job_path(
    monkeypatch, preset, requested_mode, expected_mode, expected_route,
):
    from app.core import runtime_db
    from app.services.agent_harness import goal_contract, orchestrator, run_store
    from app.services.tasks import task_run_service

    service = orchestrator.HarnessOrchestrator()
    monkeypatch.setattr(runtime_db, "runtime_enabled", lambda: True)
    monkeypatch.setattr(service, "_ensure_thread", AsyncMock(return_value="module-thread"))
    monkeypatch.setattr(service, "get_thread_model_setting", AsyncMock(return_value={}))
    monkeypatch.setattr(service, "prepare_chat", AsyncMock(side_effect=lambda _user, model: ("key", model)))
    monkeypatch.setattr(service, "_record_thread_run_model", AsyncMock())
    monkeypatch.setattr(orchestrator, "_decide_turn_with_attachment_intent", lambda *_args, **_kwargs: SimpleNamespace(
        route=lambda **_kwargs: "direct_answer", authority="mutate",
    ))
    monkeypatch.setattr(goal_contract, "seed_goal_contract_for_resume", AsyncMock(
        return_value=SimpleNamespace(to_state=lambda: {}),
    ))
    for name in ("initialize_run_state", "store_execution_profile_evidence", "store_pending_input", "enqueue_job", "patch_run_state"):
        monkeypatch.setattr(run_store, name, AsyncMock(return_value=True))
    for name in ("create_run", "save_run_state", "record_sse_payload"):
        monkeypatch.setattr(task_run_service, name, AsyncMock(return_value=True))
    monkeypatch.setattr(runtime_service, "resolve_published_snapshot", AsyncMock(return_value={
        "code": "campus_services", "release_id": "release-1", "knowledge_ids": ["kb-1"],
        "official_domains": [{"host": "school.edu.cn"}], "model_id": "campus-model",
    }))

    def no_database():
        raise AssertionError("The test must not write real chat rows")

    monkeypatch.setattr(orchestrator, "async_session", no_database)
    result = await service.accept_harness_run(
        user_id="module-user", assistant_preset=preset, agent_mode=requested_mode,
        message="你好", model="main-model", regenerate=True,
    )
    assert result["thread_id"] == "module-thread"
    initialized = run_store.initialize_run_state.await_args.kwargs
    assert initialized["agent_mode"] == expected_mode
    assert initialized["decision_route"] == expected_route
    pending = run_store.store_pending_input.await_args.args[1]
    assert pending["assistant_preset"] == preset
    assert pending["precreated_run"] is True
    if preset == "presentation":
        assert pending["skill_ids"] == ["ppt-studio"]
    if preset == "campus_services":
        assert pending["knowledge_ids"] == ["kb-1"]
        assert pending["model"] == "campus-model"
    run_store.enqueue_job.assert_awaited_once()


@pytest.mark.parametrize(("method", "path", "payload"), [
    ("GET", "/campus-assistant/admin/config", {}),
    ("GET", "/campus-assistant/admin/skins", {}),
    ("GET", "/campus-assistant/admin/skins/mcs_1", {}),
    ("PATCH", "/campus-assistant/admin/skins/mcs_1", {"json": {"name": "name"}}),
    ("DELETE", "/campus-assistant/admin/skins/mcs_1", {}),
    ("POST", "/campus-assistant/admin/skins/import", {
        "files": {"file": ("skin.axiomskin", b"invalid", "application/zip")},
    }),
])
@pytest.mark.asyncio
async def test_admin_boundary_is_unchanged_using_asgi_transport(method, path, payload):
    from app.routers import campus_assistant

    app = FastAPI()
    app.include_router(campus_assistant.router)

    async def student():
        return UserContext(user_id="student", username="student", tenant_id="1", role_ids=[])

    app.dependency_overrides[campus_assistant.current_user] = student
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path, **payload)
    assert response.status_code == 403


def test_modules_cannot_import_or_duplicate_model_vision_or_execution_loops():
    root = Path(inspect.getfile(registry)).parent
    forbidden = (
        "app.services.agent_harness.orchestrator", "app.services.agent_harness.kernel",
        "app.services.agent_harness.model_driver", "app.services.chat.plain_turn",
        "app.services.files.user_file_service", "app.services.files.document_parse_service",
    )
    for folder in ("campus_services", "presentation"):
        for path in (root / folder).glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert not (node.module or "").startswith(forbidden), str(path)
                elif isinstance(node, ast.Import):
                    assert not any(item.name.startswith(forbidden) for item in node.names), str(path)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    assert node.name not in {"stream_chat", "drive_model", "run_agent_turn", "resume_chat"}, str(path)


@pytest.mark.parametrize(("preset", "side_chat", "thread_id", "expected_origin"), [
    (None, False, None, None),
    (None, True, None, "side_chat"),
    (None, True, "existing-thread", None),
    ("presentation", False, None, "presentation"),
    ("campus_services", False, None, "campus_services"),
])
@pytest.mark.asyncio
async def test_actual_run_api_keeps_origin_attachments_and_acl(
    monkeypatch, preset, side_chat, thread_id, expected_origin,
):
    from app.api import router as api

    app = FastAPI()
    app.include_router(api.api_router)

    async def user():
        return UserContext(user_id="module-user", username="student", tenant_id="1")

    app.dependency_overrides[api.current_user] = user
    access = AsyncMock(return_value={})
    thread_access = AsyncMock(return_value={})
    accept = AsyncMock(return_value={"run_id": "module-run", "thread_id": "module-thread"})
    monkeypatch.setattr(api.builtin_app_access, "require_builtin_app_access", access)
    monkeypatch.setattr(api.builtin_app_access, "require_thread_access", thread_access)
    monkeypatch.setattr(api.harness_orchestrator, "accept_harness_run", accept)
    payload = {
        "message": "这张图片是什么", "stream": True, "assistant_preset": preset,
        "side_chat": side_chat, "thread_id": thread_id,
        "attachments": [{"filename": "test.png", "file_id": "image-1", "kind": "image"}],
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/chat/runs", json=payload)
    assert response.status_code == 202, response.text
    assert response.json()["events_url"] == "/chat/runs/module-run/events"
    accepted = accept.await_args.kwargs
    assert accepted["thread_origin"] == expected_origin
    assert accepted["assistant_preset"] == preset
    assert accepted["attachments"][0]["file_id"] == "image-1"
    assert accepted["attachments"][0]["kind"] == "image"
    if thread_id:
        thread_access.assert_awaited_once()
        assert thread_access.await_args.kwargs["expected_scope"] is None
    elif preset:
        access.assert_awaited_once()
        assert access.await_args.args[1] == preset
    else:
        access.assert_not_awaited()


@pytest.mark.parametrize("preset", ["presentation", "campus_services"])
@pytest.mark.asyncio
async def test_actual_run_api_still_denies_revoked_builtin_access(monkeypatch, preset):
    from app.api import router as api

    app = FastAPI()
    app.include_router(api.api_router)

    async def user():
        return UserContext(user_id="module-user", username="student", tenant_id="1")

    app.dependency_overrides[api.current_user] = user
    monkeypatch.setattr(api.builtin_app_access, "require_builtin_app_access", AsyncMock(
        side_effect=HTTPException(status_code=403, detail="没有此应用的访问权限"),
    ))
    accept = AsyncMock()
    monkeypatch.setattr(api.harness_orchestrator, "accept_harness_run", accept)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/chat/runs", json={"message": "你好", "assistant_preset": preset})
    assert response.status_code == 403
    accept.assert_not_awaited()
