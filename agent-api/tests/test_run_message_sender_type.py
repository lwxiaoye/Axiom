from app.models import ChatMessage
from app.routers.workflow import _run_message_sender_type


def _user_message(sender_type=None) -> ChatMessage:
    return ChatMessage(
        thread_id="run-test",
        role="user",
        content="test",
        sender_type=sender_type,
    )


def test_explicit_message_sender_overrides_delegation_session_origin():
    assert _run_message_sender_type(_user_message("human"), "delegation") == "human"
    assert _run_message_sender_type(_user_message("work_agent"), None) == "work_agent"


def test_legacy_delegation_messages_keep_work_agent_fallback():
    assert _run_message_sender_type(_user_message(), "delegation") == "work_agent"
    assert _run_message_sender_type(_user_message(), None) == "human"


def test_assistant_messages_do_not_receive_user_sender_identity():
    row = ChatMessage(thread_id="run-test", role="assistant", content="result")
    assert _run_message_sender_type(row, "delegation") is None
