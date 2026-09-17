"""Canonical active-Run input endpoint gates."""

import pytest
from fastapi import HTTPException

from app.api.router import submit_chat_run_input
from app.core.auth import UserContext
from app.schemas.schemas import HarnessInputRequest
from app.services.tasks import run_input_service, task_run_service

pytestmark = pytest.mark.asyncio


def _user() -> UserContext:
    return UserContext(user_id="u1", username="u1", tenant_id="0")


@pytest.fixture(autouse=True)
def _isolate_storage(monkeypatch):
    from app.services.agent_harness import plan_store, run_store

    async def get_state(_run_id):
        return {"version": 3, "state": {"goal_revision": 0}}

    async def revise_goal(_run_id, *, expected_state_version, goal):
        assert expected_state_version == 3
        assert isinstance(goal, str)
        return 1

    async def get_by_id(**_kwargs):
        return None

    async def persist_event(**_kwargs):
        return None

    monkeypatch.setattr(run_store, "get_run_state", get_state)
    monkeypatch.setattr(run_store, "enqueue_job", lambda *_a, **_k: _async(True))
    monkeypatch.setattr(run_store, "patch_run_state", lambda *_a, **_k: _async({"version": 4}))
    monkeypatch.setattr(plan_store, "revise_goal", revise_goal)
    monkeypatch.setattr(run_input_service, "get_by_id", get_by_id)
    monkeypatch.setattr(run_input_service, "persist_received_event", persist_event)


async def test_message_input_is_accepted_and_advances_goal(monkeypatch):
    async def get_run(_run_id, _user_id):
        return {"id": "r1", "thread_id": "t1", "status": "running", "state": {}}

    async def submit(**kwargs):
        return {"id": kwargs["input_id"], "_created": True}

    monkeypatch.setattr(task_run_service, "get_run", get_run)
    monkeypatch.setattr(run_input_service, "submit", submit)
    monkeypatch.setattr(run_input_service, "persist_user_message", lambda **_k: _async(7))

    result = await submit_chat_run_input(
        "r1",
        HarnessInputRequest(
            kind="message",
            content="补充要求",
            client_input_id="input_1234567890",
        ),
        user=_user(),
        protocol_version="1",
    )
    assert result == {
        "run_id": "r1",
        "accepted": True,
        "goal_revision": 1,
        "input_id": "input_1234567890",
        "message_id": 7,
    }


async def test_message_input_rejects_degraded_storage(monkeypatch):
    monkeypatch.setattr(
        task_run_service,
        "get_run",
        lambda *_a, **_k: _async({"id": "r1", "thread_id": "t1", "status": "running", "state": {}}),
    )
    monkeypatch.setattr(run_input_service, "submit", lambda **_k: _async(None))
    monkeypatch.setattr(run_input_service, "persist_user_message", lambda **_k: _async(17))

    with pytest.raises(HTTPException) as error:
        await submit_chat_run_input(
            "r1",
            HarnessInputRequest(kind="message", content="补充要求"),
            user=_user(),
            protocol_version="1",
        )
    assert error.value.status_code == 503


async def test_attachment_only_input_is_valid(monkeypatch):
    monkeypatch.setattr(
        task_run_service,
        "get_run",
        lambda *_a, **_k: _async({"id": "r1", "thread_id": "t1", "status": "running", "state": {}}),
    )
    monkeypatch.setattr(
        run_input_service,
        "submit",
        lambda **kwargs: _async({"id": kwargs["input_id"], "_created": True}),
    )
    monkeypatch.setattr(run_input_service, "persist_user_message", lambda **_k: _async(9))

    result = await submit_chat_run_input(
        "r1",
        HarnessInputRequest(
            kind="message",
            attachments=[{"filename": "a.txt", "text": "x"}],
        ),
        user=_user(),
        protocol_version="1",
    )
    assert result["accepted"] is True


async def test_expected_run_id_mismatch_does_not_revise_goal(monkeypatch):
    revised = []

    async def revise_goal(*_a, **_k):
        revised.append(True)
        return 1

    monkeypatch.setattr(
        task_run_service,
        "get_run",
        lambda *_a, **_k: _async({"id": "r1", "thread_id": "t1", "status": "running", "state": {}}),
    )
    from app.services.agent_harness import plan_store
    monkeypatch.setattr(plan_store, "revise_goal", revise_goal)

    with pytest.raises(HTTPException) as error:
        await submit_chat_run_input(
            "r1",
            HarnessInputRequest(
                kind="message",
                content="补充要求",
                expected_run_id="other-run",
            ),
            user=_user(),
            protocol_version="1",
        )
    assert error.value.status_code == 409
    assert revised == []


async def test_steer_rejects_terminal_run_without_enqueue(monkeypatch):
    queued = []

    async def enqueue(*_a, **_k):
        queued.append(True)
        return True

    from app.services.agent_harness import run_store
    monkeypatch.setattr(run_store, "enqueue_job", enqueue)
    monkeypatch.setattr(
        task_run_service,
        "get_run",
        lambda *_a, **_k: _async({"id": "r1", "thread_id": "t1", "status": "completed", "state": {}}),
    )
    with pytest.raises(HTTPException) as error:
        await submit_chat_run_input(
            "r1",
            HarnessInputRequest(kind="message", content="补充要求", expected_run_id="r1"),
            user=_user(),
            protocol_version="1",
        )
    assert error.value.status_code == 409
    assert queued == []


async def test_empty_input_is_rejected(monkeypatch):
    monkeypatch.setattr(
        task_run_service,
        "get_run",
        lambda *_a, **_k: _async({"id": "r1", "thread_id": "t1", "status": "running", "state": {}}),
    )
    with pytest.raises(HTTPException) as error:
        await submit_chat_run_input(
            "r1",
            HarnessInputRequest(kind="message", content="  "),
            user=_user(),
            protocol_version="1",
        )
    assert error.value.status_code == 400


async def _async(value):
    return value
