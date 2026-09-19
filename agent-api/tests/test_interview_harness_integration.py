"""Interview registration and shared Harness wiring without live services or model calls."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError


@pytest.fixture(autouse=True)
def block_live_database_queries(monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    for method in ("execute", "get", "flush", "commit"):
        monkeypatch.setattr(AsyncSession, method, AsyncMock(
            side_effect=AssertionError("Shared wiring tests must not access a live database"),
        ))


def start_input():
    return {
        "action": "start",
        "config": {
            "job_title": "Frontend intern",
            "resume_file_id": "synthetic-resume-1",
            "jd_text": "Build accessible Vue forms and handle asynchronous requests.",
            "question_count": 3,
            "level": "intern",
            "pressure_level": "gentle",
        },
    }


def test_chat_request_keeps_typed_interview_command_and_material_references():
    from app.schemas.schemas import ChatRequest
    from app.services.chat.builtin_assistants.interview.contracts import InterviewInput

    request = ChatRequest.model_validate({
        "message": "Start my synthetic interview.",
        "assistant_preset": "interview",
        "stream": True,
        "interview_input": start_input(),
        "attachments": [{"file_id": "synthetic-resume-1", "kind": "text", "status": "ok"}],
    })
    assert isinstance(request.interview_input, InterviewInput)
    dumped = request.model_dump(mode="json")
    assert dumped["interview_input"]["config"]["resume_file_id"] == "synthetic-resume-1"
    assert dumped["interview_input"]["config"]["question_count"] == 3
    assert dumped["attachments"][0]["file_id"] == "synthetic-resume-1"
    assert dumped["assistant_preset"] == "interview"
    assert dumped["stream"] is True


@pytest.mark.parametrize("command", [
    {"action": "unknown"},
    {"action": "start"},
    {"action": "answer", "expected_version": -1, "question_id": "q1"},
    {"action": "answer", "expected_version": True, "question_id": "q1"},
    {"action": "answer", "expected_version": 1, "question_id": "q1", "score": 5},
])
def test_chat_request_rejects_invalid_or_client_scored_interview_commands(command):
    from app.schemas.schemas import ChatRequest

    with pytest.raises(ValidationError):
        ChatRequest.model_validate({
            "message": "Synthetic answer.", "assistant_preset": "interview",
            "interview_input": command,
        })


def test_interview_policy_supplies_public_preamble_without_treating_answers_as_tasks():
    from app.services.chat.builtin_assistants.interview.runtime import INTERVIEW_RUNTIME_POLICY

    start = INTERVIEW_RUNTIME_POLICY.public_preamble_guidance({"interview_input": {"action": "start"}})
    answer = INTERVIEW_RUNTIME_POLICY.public_preamble_guidance({"interview_input": {"action": "answer"}})
    assert "文字模拟面试" in start
    assert "准备第一问" in start
    assert "开发任务" in start
    assert "不要给分数" in answer
    assert "准备下一问" in answer


def test_interview_identity_history_scope_and_catalog_route_are_one_domain():
    from app.schemas.schemas import ThreadItem
    from app.services.chat import builtin_app_access as access
    from app.services.chat.builtin_assistants import registry

    identity = registry.get_builtin_assistant_by_id("builtin:interview")
    assert identity is not None
    assert identity["preset"] == identity["origin"] == "interview"
    assert identity["ui_policy_key"] == "interview_practice"
    assert registry.get_builtin_assistant_by_preset("interview") == identity
    assert registry.origin_for_preset("interview") == "interview"
    assert registry.preset_from_thread_origin("interview") == "interview"
    assert registry.get_builtin_assistant_by_id("面试助手") is None
    assert registry.preset_from_thread_origin("面试助手") == ""
    assert access.normalize_thread_scope("interview") == "interview"
    assert access.thread_scope_from_origin("interview") == "interview"
    assert access.thread_scope_from_origin(None) == "ordinary"
    assert access.scope_for_preset("interview") == "interview"
    assert access.builtin_preset_for_catalog_routes("/center/chat/interview/") == "interview"
    assert access.builtin_preset_for_catalog_routes(
        "/center/chat/interview", "/center/chat/campus",
    ) is None
    assert access.builtin_preset_for_catalog_routes("面试助手") is None
    history = ThreadItem(id="synthetic-thread", title="Practice", assistant_preset="interview")
    assert history.model_dump()["assistant_preset"] == "interview"


@pytest.fixture
def run_api(monkeypatch):
    from app.api import router as api
    from app.core.auth import UserContext

    app = FastAPI()
    app.include_router(api.api_router)
    user = UserContext(user_id="interview-wiring-user", username="synthetic-student", tenant_id="1")

    async def current_user():
        return user

    app.dependency_overrides[api.current_user] = current_user
    app_access = AsyncMock(return_value={})
    thread_access = AsyncMock(return_value={})
    accept = AsyncMock(return_value={"run_id": "synthetic-run", "thread_id": "synthetic-thread"})
    monkeypatch.setattr(api.builtin_app_access, "require_builtin_app_access", app_access)
    monkeypatch.setattr(api.builtin_app_access, "require_thread_access", thread_access)
    monkeypatch.setattr(api.harness_orchestrator, "accept_harness_run", accept)
    return SimpleNamespace(
        app=app, user=user, app_access=app_access, thread_access=thread_access, accept=accept,
    )


@pytest.mark.parametrize("existing_thread", [None, "synthetic-thread"])
@pytest.mark.asyncio
async def test_real_run_api_forwards_interview_input_origin_and_acl(run_api, existing_thread):
    command = start_input() if existing_thread is None else {
        "action": "answer", "expected_version": 7, "question_id": "q-current",
    }
    payload = {
        "message": "Synthetic interview turn.", "stream": True,
        "assistant_preset": "interview", "thread_id": existing_thread,
        "interview_input": command,
        "client_request_id": "interview-wiring-request-01",
        "attachments": [{"file_id": "synthetic-resume-1", "kind": "text", "status": "ok"}],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=run_api.app), base_url="http://test",
    ) as client:
        response = await client.post("/chat/runs", json=payload)
    assert response.status_code == 202, response.text
    assert response.json()["events_url"] == "/chat/runs/synthetic-run/events"
    accepted = run_api.accept.await_args.kwargs
    assert accepted["assistant_preset"] == "interview"
    assert accepted["thread_origin"] == "interview"
    assert accepted["interview_input"]["action"] == command["action"]
    assert accepted["interview_input"].get("question_id") == command.get("question_id")
    assert accepted["client_request_id"] == payload["client_request_id"]
    assert accepted["attachments"][0]["file_id"] == "synthetic-resume-1"
    if existing_thread:
        run_api.thread_access.assert_awaited_once_with(
            run_api.user, existing_thread, expected_scope="interview",
        )
        run_api.app_access.assert_not_awaited()
    else:
        run_api.app_access.assert_awaited_once_with(run_api.user, "interview")
        run_api.thread_access.assert_not_awaited()


@pytest.mark.parametrize("existing_thread", [None, "synthetic-thread"])
@pytest.mark.asyncio
async def test_real_run_api_denies_revoked_or_wrong_scope_before_acceptance(run_api, existing_thread):
    gate = run_api.thread_access if existing_thread else run_api.app_access
    gate.side_effect = HTTPException(status_code=403, detail="Synthetic access denial")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=run_api.app), base_url="http://test",
    ) as client:
        response = await client.post("/chat/runs", json={
            "message": "Start synthetic interview.", "assistant_preset": "interview",
            "thread_id": existing_thread, "interview_input": start_input(),
        })
    assert response.status_code == 403
    run_api.accept.assert_not_awaited()


@pytest.mark.parametrize("preset", [None, "presentation", "campus_services"])
@pytest.mark.asyncio
async def test_shared_acceptance_rejects_interview_commands_in_other_domains(monkeypatch, preset):
    from app.core import runtime_db
    from app.services.agent_harness import orchestrator

    service = orchestrator.HarnessOrchestrator()
    monkeypatch.setattr(runtime_db, "runtime_enabled", lambda: True)
    monkeypatch.setattr(service, "_ensure_thread", AsyncMock(return_value="synthetic-thread"))
    monkeypatch.setattr(service, "get_thread_model_setting", AsyncMock(return_value={
        "assistant_preset": preset,
    }))
    prepare_model = AsyncMock()
    monkeypatch.setattr(service, "prepare_chat", prepare_model)
    with pytest.raises(HTTPException) as error:
        await service.accept_harness_run(
            user_id="interview-wiring-user", assistant_preset=preset,
            thread_id="synthetic-thread", message="Synthetic answer.",
            interview_input={"action": "answer", "expected_version": 1, "question_id": "q1"},
        )
    assert error.value.status_code == 422
    prepare_model.assert_not_awaited()


@pytest.mark.asyncio
async def test_registered_builder_keeps_interview_boundary_on_first_and_recovered_turns(monkeypatch):
    from app.services.chat import tools as tool_builders
    from app.services.chat.builtin_assistants.runtime import (
        builtin_tool_build_options,
        get_builtin_runtime_policy,
    )

    forbidden_builder = Mock(side_effect=AssertionError("An interview cannot construct unrestricted tools"))
    for module, name in (
        (tool_builders._browser, "build_browser_tools"),
        (tool_builders._workspace, "build_workspace_tools"),
        (tool_builders._paths, "build_path_tools"),
        (tool_builders._shell, "build_shell_tools"),
    ):
        monkeypatch.setattr(module, name, forbidden_builder)
    connector_builder = AsyncMock(side_effect=AssertionError("An interview cannot load connectors"))
    monkeypatch.setattr(tool_builders._connectors, "build_connector_tools", connector_builder)

    policy = get_builtin_runtime_policy("interview")
    first_tools = await tool_builders.build_tools(
        token="", knowledge_ids=["synthetic-kb"], web_enabled=True,
        user_id="interview-wiring-user", thread_id="synthetic-thread", run_id="synthetic-run",
        **builtin_tool_build_options(policy),
    )
    recovered_policy = get_builtin_runtime_policy("interview")
    recovered_tools = await tool_builders.build_tools(
        token="", knowledge_ids=["synthetic-kb"], web_enabled=True,
        user_id="interview-wiring-user", thread_id="synthetic-thread", run_id="synthetic-run",
        **builtin_tool_build_options(recovered_policy),
    )
    first = policy.bound_tools(first_tools)
    recovered = recovered_policy.bound_tools(recovered_tools)
    names = {tool.name for tool in first}
    assert {"get_interview_session", "commit_interview_turn"} <= names
    assert names <= {"get_interview_session", "commit_interview_turn", "search_knowledge", "search_web"}
    assert len(first) == len(names)
    assert [(tool.name, tool.parameters) for tool in first] == [
        (tool.name, tool.parameters) for tool in recovered
    ]
    assert policy.forced_resume_mode == "standard"
    assert policy.allow_plain_fallback is False
    assert policy.action_authority == "mutate"
    forbidden_builder.assert_not_called()
    connector_builder.assert_not_awaited()


@pytest.fixture
def acceptance_harness(monkeypatch):
    from app.core import runtime_db
    from app.services.agent_harness import goal_contract, orchestrator, run_store
    from app.services.chat.builtin_assistants import runtime
    from app.services.chat.builtin_assistants.interview import service as interview_service
    from app.services.chat.builtin_assistants.interview.contracts import InterviewInput
    from app.services.tasks import task_run_service

    service = orchestrator.HarnessOrchestrator()
    monkeypatch.setattr(runtime_db, "runtime_enabled", lambda: True)
    monkeypatch.setattr(service, "_ensure_thread", AsyncMock(return_value="synthetic-thread"))
    monkeypatch.setattr(service, "get_thread_model_setting", AsyncMock(return_value={
        "assistant_preset": "interview",
    }))
    monkeypatch.setattr(service, "prepare_chat", AsyncMock(side_effect=lambda _user, model: ("synthetic-key", model)))
    monkeypatch.setattr(service, "_record_thread_run_model", AsyncMock())
    monkeypatch.setattr(orchestrator, "_decide_turn_with_attachment_intent", lambda *_args, **_kwargs: SimpleNamespace(
        route=lambda **_kwargs: "direct_answer", authority="mutate",
    ))
    monkeypatch.setattr(goal_contract, "seed_goal_contract_for_resume", AsyncMock(
        return_value=SimpleNamespace(to_state=lambda: {}),
    ))
    for name in ("initialize_run_state", "store_execution_profile_evidence", "store_pending_input", "enqueue_job", "patch_run_state"):
        monkeypatch.setattr(run_store, name, AsyncMock(return_value=True))
    for name in ("create_run", "save_run_state", "record_sse_payload", "recover_run_after_fault", "append_run_event", "finalize_run"):
        monkeypatch.setattr(task_run_service, name, AsyncMock(return_value=True))

    class SyntheticSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def add(self, row):
            row.id = 42

        async def get(self, *_args):
            return None

        async def commit(self):
            return None

    monkeypatch.setattr(orchestrator, "async_session", SyntheticSession)
    monkeypatch.setattr(interview_service, "validate_interview_input", AsyncMock(
        side_effect=lambda **kwargs: InterviewInput.model_validate(
            kwargs["interview_input"],
        ).model_dump(mode="json"),
    ))
    accept_input = AsyncMock(return_value={})
    policy = replace(runtime.get_builtin_runtime_policy("interview"), accept_input=accept_input)
    original_resolver = runtime.get_builtin_runtime_policy
    monkeypatch.setattr(runtime, "get_builtin_runtime_policy", lambda value: (
        policy if value == "interview" else original_resolver(value)
    ))
    return SimpleNamespace(
        service=service, run_store=run_store, task_run_service=task_run_service, accept_input=accept_input,
    )


@pytest.mark.asyncio
async def test_shared_acceptance_persists_interview_command_for_worker(acceptance_harness):
    harness = acceptance_harness
    command = start_input()
    expected = deepcopy(command)
    result = await harness.service.accept_harness_run(
        user_id="interview-wiring-user", assistant_preset="interview",
        agent_mode="research", model="synthetic-model", message="Start synthetic interview.",
        interview_input=command,
    )
    initialized = harness.run_store.initialize_run_state.await_args.kwargs
    assert initialized["agent_mode"] == "standard"
    assert initialized["decision_route"] == "agent"
    pending = harness.run_store.store_pending_input.await_args.args[1]
    assert pending["assistant_preset"] == "interview"
    assert pending["interview_input"]["action"] == expected["action"]
    assert pending["interview_input"]["config"]["resume_file_id"] == expected["config"]["resume_file_id"]
    assert pending["precreated_user_message_id"] == 42
    accepted = harness.accept_input.await_args.args[0]
    assert accepted["thread_id"] == result["thread_id"] == "synthetic-thread"
    assert accepted["run_id"] == result["run_id"]
    assert accepted["interview_input"]["action"] == "start"
    harness.accept_input.assert_awaited_once()
    harness.run_store.enqueue_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_shared_acceptance_preserves_domain_conflicts_without_retry_job(acceptance_harness):
    harness = acceptance_harness
    harness.accept_input.side_effect = HTTPException(
        status_code=409, detail="Synthetic question version is stale",
    )
    with pytest.raises(HTTPException) as error:
        await harness.service.accept_harness_run(
            user_id="interview-wiring-user", assistant_preset="interview",
            agent_mode="standard", model="synthetic-model", message="Synthetic answer.",
            interview_input={"action": "answer", "expected_version": 1, "question_id": "q-old"},
        )
    assert error.value.status_code == 409
    harness.run_store.enqueue_job.assert_not_awaited()
    harness.task_run_service.recover_run_after_fault.assert_not_awaited()
    harness.task_run_service.finalize_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_committed_text_mapper_hides_candidate_bank_drafts_and_tool_arguments():
    from app.services.chat.main_tool_turn import map_tool_loop_events
    from app.services.sse_protocol import HARNESS, SSEChannel

    secret = "SYNTHETIC_UNASKED_QUESTION_BANK"

    async def events():
        yield {"type": "delta", "text": secret}
        yield {"type": "reasoning", "text": secret}
        yield {"type": "commentary", "text": "我先对照简历和岗位要求。"}
        yield {
            "type": "tool_started", "name": "get_interview_session",
            "args": {"section": "materials", "material_kind": "resume", "offset": 0},
            "call_id": "read-resume",
        }
        yield {
            "type": "tool_result", "name": "get_interview_session", "status": "succeeded",
            "preview": secret, "call_id": "read-resume",
        }
        yield {"type": "tool_started", "name": "commit_interview_turn", "args": {"question_bank": [secret]}}
        yield {"type": "tool_progress", "name": "commit_interview_turn", "text": secret, "label": secret}
        yield {"type": "tool_result", "name": "commit_interview_turn", "status": "succeeded", "preview": secret}
        yield {"type": "final", "answer": secret, "trace": [{"name": "commit_interview_turn", "args": secret}]}

    out = {"answer": "", "streamed_any": False}
    frames = [frame async for frame in map_tool_loop_events(
        SSEChannel(HARNESS, "synthetic-thread", "synthetic-run"), events(), out,
        committed_text_only=True,
    )]
    payload = "".join(frames)
    assert secret not in payload
    assert "question_bank" not in payload
    assert out["streamed_any"] is False
    public_types = [json.loads(frame.removeprefix("data: "))["type"] for frame in frames]
    assert not set(public_types) & {
        "message.delta", "message.reasoning.delta", "message.reasoning.completed",
    }
    assert "message.commentary" in public_types
    assert "我先对照简历和岗位要求" in payload
    assert {"tool.started", "tool.completed", "tool.progress"} <= set(public_types)
    assert "阅读简历" in payload
    assert "准备下一问" in payload


@pytest.mark.asyncio
async def test_ordinary_mapper_still_streams_user_visible_text():
    from app.services.chat.main_tool_turn import map_tool_loop_events
    from app.services.sse_protocol import HARNESS, SSEChannel

    async def events():
        yield {"type": "delta", "text": "An ordinary answer."}
        yield {"type": "final", "answer": "An ordinary answer.", "trace": []}

    out = {"answer": "", "streamed_any": False}
    frames = [frame async for frame in map_tool_loop_events(
        SSEChannel(HARNESS, "synthetic-thread", "synthetic-run"), events(), out,
    )]
    assert any(json.loads(frame.removeprefix("data: "))["type"] == "message.delta" for frame in frames)
    assert "An ordinary answer." in "".join(frames)
    assert out["streamed_any"] is True


@pytest.mark.parametrize("receipt", [None, {"rendered_text": "Committed feedback and one question."}])
@pytest.mark.asyncio
async def test_interview_final_projection_never_uses_uncommitted_provider_answer(monkeypatch, receipt):
    from app.services.chat.builtin_assistants.interview import runtime, service

    read_receipt = AsyncMock(return_value=receipt)
    monkeypatch.setattr(service, "get_committed_turn", read_receipt)
    read_state = AsyncMock(return_value={"status": "active"})
    monkeypatch.setattr(service, "get_interview_session", read_state)
    text = await runtime.project_answer(
        SimpleNamespace(user_id="u1", thread_id="t1", run_id="r1"),
        "SYNTHETIC_UNCOMMITTED_QUESTION_BANK_AND_SCORE",
    )
    assert "SYNTHETIC_UNCOMMITTED" not in text
    if receipt:
        assert text == receipt["rendered_text"]
        read_state.assert_not_awaited()
    else:
        assert "没有成功保存" in text
        read_state.assert_awaited_once_with(user_id="u1", thread_id="t1")
    read_receipt.assert_awaited_once_with(user_id="u1", thread_id="t1", run_id="r1")


@pytest.mark.parametrize("kind", ["message", "clarification", "plan_confirmation"])
@pytest.mark.asyncio
async def test_active_interview_rejects_run_inputs_without_mutating_goal(run_api, monkeypatch, kind):
    from app.services.tasks import run_input_service, task_run_service

    monkeypatch.setattr(task_run_service, "get_run", AsyncMock(return_value={
        "thread_id": "synthetic-thread", "status": "running", "state": {"goal_revision": 3},
    }))
    run_api.thread_access.return_value = SimpleNamespace(origin="interview")
    lookup = AsyncMock()
    monkeypatch.setattr(run_input_service, "get_by_id", lookup)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=run_api.app), base_url="http://test",
    ) as client:
        response = await client.post("/chat/runs/synthetic-run/inputs", json={
            "kind": kind, "content": "Synthetic extra answer.",
            "client_input_id": "synthetic-input-0001",
        })
    assert response.status_code == 409, response.text
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_ordinary_run_input_keeps_existing_idempotent_acceptance(run_api, monkeypatch):
    from app.services.tasks import run_input_service, task_run_service

    monkeypatch.setattr(task_run_service, "get_run", AsyncMock(return_value={
        "thread_id": "synthetic-thread", "status": "running", "state": {"goal_revision": 3},
    }))
    run_api.thread_access.return_value = SimpleNamespace(origin=None)
    lookup = AsyncMock(return_value={"sourceMessageId": 43})
    monkeypatch.setattr(run_input_service, "get_by_id", lookup)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=run_api.app), base_url="http://test",
    ) as client:
        response = await client.post("/chat/runs/synthetic-run/inputs", json={
            "kind": "message", "content": "Synthetic clarification.",
            "client_input_id": "synthetic-input-0001", "expected_run_id": "synthetic-run",
        })
    assert response.status_code == 202, response.text
    assert response.json()["accepted"] is True
    assert response.json()["message_id"] == 43
    lookup.assert_awaited_once()


@pytest.mark.parametrize(("origin", "expected_status"), [("interview", 409), (None, 200)])
@pytest.mark.asyncio
async def test_queue_boundary_is_interview_specific(run_api, monkeypatch, origin, expected_status):
    from app.services.tasks import thread_queue_service

    run_api.thread_access.return_value = SimpleNamespace(origin=origin)
    enqueue = AsyncMock(return_value={"id": "synthetic-queue-item", "content": "Synthetic next turn."})
    monkeypatch.setattr(thread_queue_service, "enqueue", enqueue)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=run_api.app), base_url="http://test",
    ) as client:
        response = await client.post("/chat/threads/synthetic-thread/queue", json={
            "content": "Synthetic next turn.",
        })
    assert response.status_code == expected_status, response.text
    if origin == "interview":
        enqueue.assert_not_awaited()
    else:
        enqueue.assert_awaited_once()


@pytest.mark.parametrize("denied", [False, True])
@pytest.mark.asyncio
async def test_interview_snapshot_checks_owner_and_scope_before_public_projection(run_api, monkeypatch, denied):
    from app.api import interview as interview_api

    check_access = AsyncMock(return_value=SimpleNamespace(origin="interview"))
    if denied:
        check_access.side_effect = HTTPException(status_code=404, detail="Synthetic inaccessible thread")
    read_snapshot = AsyncMock(return_value={"status": "awaiting_answer", "current_question": {"id": "q1"}})
    monkeypatch.setattr(interview_api, "require_thread_access", check_access)
    monkeypatch.setattr(interview_api, "get_interview_session", read_snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=run_api.app), base_url="http://test",
    ) as client:
        response = await client.get("/chat/threads/synthetic-thread/interview")
    check_access.assert_awaited_once_with(run_api.user, "synthetic-thread", expected_scope="interview")
    if denied:
        assert response.status_code == 404
        read_snapshot.assert_not_awaited()
    else:
        assert response.status_code == 200
        read_snapshot.assert_awaited_once_with(user_id=run_api.user.user_id, thread_id="synthetic-thread")


@pytest.mark.parametrize("denial", ["other_owner", "missing_thread", "revoked_application"])
@pytest.mark.asyncio
async def test_event_subscription_refuses_before_streaming_headers(run_api, monkeypatch, denial):
    from app.api import router as api
    from app.services.tasks import task_run_service

    get_run = AsyncMock(return_value=None if denial == "other_owner" else {"thread_id": "synthetic-thread"})
    monkeypatch.setattr(task_run_service, "get_run", get_run)
    expected_status = 403 if denial == "revoked_application" else 404
    if denial != "other_owner":
        run_api.thread_access.side_effect = HTTPException(status_code=expected_status, detail="Synthetic refusal")
    subscribe = Mock(side_effect=AssertionError("Denied requests must not create a stream"))
    monkeypatch.setattr(api.harness_orchestrator, "subscribe_run", subscribe)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=run_api.app), base_url="http://test",
    ) as client:
        response = await client.get("/chat/runs/synthetic-run/events")

    assert response.status_code == expected_status
    assert response.headers["content-type"].startswith("application/json")
    get_run.assert_awaited_once_with("synthetic-run", run_api.user.user_id)
    subscribe.assert_not_called()
    if denial == "other_owner":
        run_api.thread_access.assert_not_awaited()
    else:
        run_api.thread_access.assert_awaited_once_with(run_api.user, "synthetic-thread")


@pytest.mark.parametrize("origin", [None, "interview", "campus_services", "presentation"])
@pytest.mark.asyncio
async def test_authorized_event_subscription_keeps_replay_cursor(run_api, monkeypatch, origin):
    from app.api import router as api
    from app.services.tasks import task_run_service

    monkeypatch.setattr(task_run_service, "get_run", AsyncMock(return_value={"thread_id": "synthetic-thread"}))
    run_api.thread_access.return_value = SimpleNamespace(origin=origin)

    async def events(**kwargs):
        yield 'data: {"type":"run.completed","sequence":43}\n\n'

    subscribe = Mock(side_effect=events)
    monkeypatch.setattr(api.harness_orchestrator, "subscribe_run", subscribe)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=run_api.app), base_url="http://test",
    ) as client:
        response = await client.get("/chat/runs/synthetic-run/events?after=42")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"sequence":43' in response.text
    run_api.thread_access.assert_awaited_once_with(run_api.user, "synthetic-thread")
    subscribe.assert_called_once_with(
        user_id=run_api.user.user_id, run_id="synthetic-run", after_sequence=42, protocol="harness/1",
    )


@pytest.mark.asyncio
async def test_committed_text_mapper_uses_policy_phase_projection():
    """助手自己的投影工厂（可 async）接管执行卡文案；阶段句来自冻结动作而非模型。"""
    from app.services.chat.main_tool_turn import map_tool_loop_events
    from app.services.sse_protocol import HARNESS, SSEChannel

    async def mapper(event):
        return {**event, "args": {"intent": "评估第 1 题的回答，准备第 2 题"}, "preview": "", "text": "", "observation": None}

    async def events():
        yield {"type": "tool_started", "name": "commit_interview_turn", "args": {"question_bank": ["SECRET"]}}
        yield {"type": "tool_result", "name": "commit_interview_turn", "status": "succeeded", "preview": "SECRET"}
        yield {"type": "final", "answer": "", "trace": []}

    out = {"answer": "", "streamed_any": False}
    payload = "".join([frame async for frame in map_tool_loop_events(
        SSEChannel(HARNESS, "synthetic-thread", "synthetic-run"), events(), out,
        committed_text_only=True, public_event_mapper=mapper,
    )])
    assert "SECRET" not in payload
    assert "评估第 1 题的回答，准备第 2 题" in payload


@pytest.mark.asyncio
async def test_interview_turn_context_policy_and_terminal_failure(monkeypatch):
    """面试自带上下文（不建沙箱/不重复注入简历/不叠通用契约），模型失败改判为可读终态。"""
    from app.services.agent_harness.public_errors import TerminalRunError
    from app.services.chat.builtin_assistants.interview import runtime, service

    policy = runtime.INTERVIEW_RUNTIME_POLICY
    assert policy.owns_turn_context is True
    assert policy.initial_observation and policy.turn_opening and policy.public_loop_event and policy.terminal_failure

    state = {
        "version": 1, "status": "active", "input": {"action": "answer", "expected_version": 1, "question_id": "q1",
                                                    "answer_message_id": 7, "answer_text": "答", "run_id": "run-1"},
        "progress": {"current_number": 1, "total": 3, "answered": 0, "skipped": 0}, "config": {"question_count": 3},
        "materials": {}, "question_bank": [], "turns": [], "profile": None, "current_question": None, "review": None,
    }
    monkeypatch.setattr(service, "get_interview_session", AsyncMock(return_value=state))
    identity = {"user_id": "u", "thread_id": "t", "run_id": "run-1"}
    assert await policy.turn_opening(identity) == "正在评估你第 1 题的回答，准备第 2 题。"
    env = SimpleNamespace(**identity)
    mapped = await policy.public_loop_event(env)({"type": "tool_started", "name": "commit_interview_turn", "args": {"x": 1}})
    assert mapped["args"] == {"intent": "评估第 1 题的回答，准备第 2 题"}

    failure = await policy.terminal_failure(env, RuntimeError("模型调用失败: 502 upstream body"))
    assert isinstance(failure, TerminalRunError)
    assert "模型服务返回 502" in failure.public_message
    assert "重新发送这份回答" in failure.public_message
    assert "upstream body" not in failure.public_message
    timeout = await policy.terminal_failure(env, TimeoutError("read timeout"))
    assert "模型响应超时" in timeout.public_message
    state["input"]["action"] = "start"
    start_failure = await policy.terminal_failure(env, RuntimeError("boom"))
    assert "继续准备" in start_failure.public_message


@pytest.mark.asyncio
async def test_tool_loop_failure_is_converted_by_policy_before_recovery():
    """main_tool_turn 的通用异常分支先问助手策略：面试把模型失败抛成 TerminalRunError；
    已是终态的不改判、策略抛错不吞原异常、普通助手（无钩子）沿用恢复路径。"""
    from app.services.agent_harness.public_errors import ConfigurationRunError, TerminalRunError
    from app.services.chat.main_tool_turn import convert_loop_failure
    from app.services.chat.builtin_assistants.interview import runtime

    seen = {}

    async def fake_terminal_failure(env, exc):
        seen["exc"] = exc
        return runtime.InterviewTurnFailed("面试助手这一轮没有完成：模型响应超时。请重新发送这份回答。")

    policy = replace(runtime.INTERVIEW_RUNTIME_POLICY, terminal_failure=fake_terminal_failure)
    env = SimpleNamespace(user_id="u", thread_id="t", run_id="r")
    converted = await convert_loop_failure(policy, env, RuntimeError("boom"))
    assert isinstance(converted, TerminalRunError)
    assert converted.public_message.startswith("面试助手这一轮没有完成")
    assert isinstance(seen["exc"], RuntimeError)
    assert await convert_loop_failure(policy, env, ConfigurationRunError("配置缺失")) is None
    assert await convert_loop_failure(None, env, RuntimeError("boom")) is None

    async def broken(_env, _exc):
        raise ValueError("hook broke")

    assert await convert_loop_failure(replace(policy, terminal_failure=broken), env, RuntimeError("boom")) is None
