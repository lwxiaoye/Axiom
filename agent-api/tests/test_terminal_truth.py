"""Run、消息与最终总结必须共享同一终态事实。"""
import asyncio
import unittest

from app.models import ChatMessage
from app.services.chat import turn_finalizer
from app.services.chat.types import TurnOutcome


class TerminalMessageStatusTests(unittest.TestCase):
    # 原 test_graph_failure_is_failed_message 已删（2026-07-27）：它断言 task_graph_failed
    # 能把消息标成 failed，而这个键的唯一写入方随 DAG Runtime 一起下线，判据已删除。
    def test_unverified_failure_is_interrupted_message(self):
        self.assertEqual(
            turn_finalizer.terminal_message_status({"run_disposition": "failed"}),
            "interrupted",
        )

    def test_verified_failure_is_failed_message(self):
        self.assertEqual(
            turn_finalizer.terminal_message_status({
                "run_disposition": "failed", "verified_failure": True,
            }),
            "failed",
        )

    def test_cancel_is_cancelled_message(self):
        self.assertEqual(
            turn_finalizer.terminal_message_status({"run_disposition": "cancelled"}),
            "cancelled",
        )

    def test_normal_turn_is_completed_message(self):
        self.assertEqual(
            turn_finalizer.terminal_message_status({"run_disposition": None}),
            "completed",
        )

    def test_interrupted_tool_turn_is_interrupted_message(self):
        self.assertEqual(
            turn_finalizer.terminal_message_status({"completion_interrupted": True}),
            "interrupted",
        )

    def test_turn_outcome_carries_interrupted_completion_to_finalizer(self):
        outcome = TurnOutcome()
        outcome["completion_interrupted"] = True
        self.assertTrue(outcome.completion_interrupted)
        self.assertEqual(turn_finalizer.terminal_message_status(outcome), "interrupted")


class _Session:
    def __init__(self, row):
        self.row = row
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, _model, _message_id):
        return self.row

    async def commit(self):
        self.commits += 1


class TerminalMessageReconcileTests(unittest.TestCase):
    def test_pg_terminal_wins_concurrent_mysql_status(self):
        row = ChatMessage(
            id=42,
            thread_id="t1",
            role="assistant",
            content="旧状态",
            run_id="r1",
            status="completed",
        )
        session = _Session(row)
        original = turn_finalizer.async_session
        turn_finalizer.async_session = lambda: session  # type: ignore[assignment]
        try:
            asyncio.run(
                turn_finalizer.reconcile_assistant_message_status(
                    42, "r1", "failed",
                )
            )
        finally:
            turn_finalizer.async_session = original  # type: ignore[assignment]
        self.assertEqual(row.status, "failed")
        self.assertEqual(session.commits, 1)

    def test_never_touches_message_from_another_run(self):
        row = ChatMessage(
            id=42,
            thread_id="t1",
            role="assistant",
            content="别的任务",
            run_id="other-run",
            status="completed",
        )
        session = _Session(row)
        original = turn_finalizer.async_session
        turn_finalizer.async_session = lambda: session  # type: ignore[assignment]
        try:
            asyncio.run(
                turn_finalizer.reconcile_assistant_message_status(
                    42, "r1", "failed",
                )
            )
        finally:
            turn_finalizer.async_session = original  # type: ignore[assignment]
        self.assertEqual(row.status, "completed")
        self.assertEqual(session.commits, 0)

    def test_partial_run_phase_overrides_completed_database_status(self):
        row = ChatMessage(
            id=42,
            thread_id="t1",
            role="assistant",
            content="部分完成",
            run_id="r1",
            status="completed",
        )
        session = _Session(row)
        original = turn_finalizer.async_session
        turn_finalizer.async_session = lambda: session  # type: ignore[assignment]
        try:
            asyncio.run(
                turn_finalizer.reconcile_assistant_message_status(
                    42, "r1", "completed", run_phase="partial",
                )
            )
        finally:
            turn_finalizer.async_session = original  # type: ignore[assignment]
        self.assertEqual(row.status, "partial")
        self.assertEqual(session.commits, 1)
