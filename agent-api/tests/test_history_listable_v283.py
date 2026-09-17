"""A saved user conversation stays discoverable throughout the Run lifecycle."""

import pytest
from sqlalchemy import Column, MetaData, String, Table, Text, create_engine, select

from app.models import (
    POLICY_REJECTED_PENDING_MESSAGE_STATUS,
    POLICY_REJECTED_MESSAGE_STATUS,
    ChatMessage,
    ChatThread,
    live_chat_message_clause,
    visible_chat_message_clause,
)
from app.services.agent_harness.orchestrator import _history_listable_thread_clause


@pytest.fixture
def history_db():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    threads = Table("ai_chat_threads", metadata, Column("id", String, primary_key=True))
    messages = Table("ai_chat_messages", metadata,
        Column("thread_id", String), Column("role", String), Column("content", Text), Column("status", String))
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(threads.insert(), [{"id": "research"}, {"id": "other"}])
        yield connection, messages
    engine.dispose()


def visible_thread_ids(connection):
    return set(connection.execute(select(ChatThread.id).where(_history_listable_thread_clause())).scalars())


def test_pending_report_anchor_cannot_hide_a_saved_conversation(history_db):
    connection, messages = history_db
    connection.execute(messages.insert(), {"thread_id": "research", "role": "user", "content": "我想了解 kimi k3"})
    assert visible_thread_ids(connection) == {"research"}
    connection.execute(messages.insert(), {"thread_id": "research", "role": "assistant", "content": "", "status": "partial"})
    assert visible_thread_ids(connection) == {"research"}


@pytest.mark.parametrize("status,content", [
    ("failed", "执行失败，请稍后重试"),
    ("failed", "报告生成或核验服务响应超时"),
    (None, "服务暂时不可用"),
    ("completed", "遇到服务暂时不可用时，应保留会话并明确告知用户。"),
    ("cancelled", ""),
    ("interrupted", ""),
    ("superseded", "上一版内容"),
    ("completed", "已完成研究报告"),
])
def test_assistant_outcome_does_not_remove_user_history(history_db, status, content):
    connection, messages = history_db
    connection.execute(messages.insert(), {"thread_id": "research", "role": "user", "content": "我想了解 kimi k3"})
    connection.execute(messages.insert(), {"thread_id": "research", "role": "assistant", "content": content, "status": status})
    assert visible_thread_ids(connection) == {"research"}


def test_empty_and_archived_threads_remain_hidden_and_messages_stay_correlated(history_db):
    connection, messages = history_db
    assert visible_thread_ids(connection) == set()
    connection.execute(messages.insert(), {"thread_id": "research", "role": "assistant", "content": "占位"})
    assert visible_thread_ids(connection) == set()
    connection.execute(messages.insert(), {"thread_id": "research", "role": "user", "content": "已归档问题", "status": "archived"})
    assert visible_thread_ids(connection) == set()
    connection.execute(messages.insert(), {"thread_id": "other", "role": "user", "content": "另一条会话"})
    assert visible_thread_ids(connection) == {"other"}


@pytest.mark.parametrize("rejected_status", [
    POLICY_REJECTED_PENDING_MESSAGE_STATUS,
    POLICY_REJECTED_MESSAGE_STATUS,
])
def test_policy_rejected_turn_stays_visible_but_cannot_poison_next_model_request(
    history_db, rejected_status,
):
    connection, messages = history_db
    connection.execute(messages.insert(), [
        {
            "thread_id": "research",
            "role": "user",
            "content": "qz_sensitive_test_92741",
            "status": rejected_status,
        },
        {
            "thread_id": "research",
            "role": "assistant",
            "content": "（任务执行失败，未生成回复）",
            "status": rejected_status,
        },
        {
            "thread_id": "research",
            "role": "user",
            "content": "你好",
            "status": None,
        },
    ])

    live_contents = set(connection.execute(
        select(ChatMessage.content).where(live_chat_message_clause())
    ).scalars())
    visible_contents = set(connection.execute(
        select(ChatMessage.content).where(visible_chat_message_clause())
    ).scalars())

    assert live_contents == {"你好"}
    assert visible_contents == {
        "qz_sensitive_test_92741", "（任务执行失败，未生成回复）", "你好",
    }
    assert visible_thread_ids(connection) == {"research"}
