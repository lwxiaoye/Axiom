from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.routers import workflow
from app.services.chat import builtin_app_access


def test_run_message_append_exposes_a_validated_turn_contract():
    payload = workflow.RunMessageAppend(
        sessionId="thread-1",
        role="assistant",
        content="reply",
        turnId="turn-1",
        status="failed",
    )

    assert payload.turnId == "turn-1"
    assert payload.status == "failed"

    with pytest.raises(ValidationError):
        workflow.RunMessageAppend(
            sessionId="thread-1",
            role="assistant",
            content="reply",
            status="anything",
        )


def test_run_message_reader_returns_persisted_turn_status_and_feedback():
    source = Path(workflow.__file__).read_text(encoding="utf-8")
    handler = source[source.index("async def run_messages"):source.index("async def append_run_message")]

    assert '"turnId": r.turn_id' in handler
    assert '"status": r.status' in handler
    assert '"feedback": r.feedback' in handler


@pytest.mark.asyncio
async def test_message_feedback_access_allows_the_owner_run_session(monkeypatch):
    """运行页消息也在 ai_chat_messages，但不属于主对话/内置应用会话。"""
    thread = SimpleNamespace(id="run-thread", user_id="user-1", app_id="app-1")

    class Result:
        def scalar_one_or_none(self):
            return thread

    class Session:
        async def execute(self, _statement):
            return Result()

    class SessionFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *_args):
            return None

    async def ordinary_thread_access(*_args, **_kwargs):
        raise AssertionError("运行会话不应走只允许主对话/内置应用的权限校验")

    monkeypatch.setattr(builtin_app_access, "async_session", SessionFactory())
    monkeypatch.setattr(builtin_app_access, "require_thread_access", ordinary_thread_access)

    actual = await builtin_app_access.require_message_access(SimpleNamespace(user_id="user-1"), 8850)

    assert actual is thread
