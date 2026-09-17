"""P0 第二批回归（2026-07-17）：终态 CAS / 发布判定 / 无正文锚点 / 过期清单。

1. _set_status：条件 UPDATE 语义——rowcount=0（已终态）返回 False，不覆盖；
2. finalize_run：只有 CAS applied→发布；与既有终态冲突、Runtime 不可用或非终态→不发布并恢复；
3. ensure_terminal_anchor：cancelled/failed 且无归属行→插占位+补锚；有行/非终态→no-op；
4. sweep_expired_waiting：RETURNING 清单形态。
无 DB：沿用假会话工厂模式。
"""
import asyncio
import unittest
from typing import Any, List

from app.services.tasks import task_run_service as trs


class _FakeResult:
    def __init__(self, items=None, rowcount=0):
        self._items = items or []
        self.rowcount = rowcount

    def scalars(self):
        return self

    def all(self):
        return self._items

    def fetchall(self):
        return self._items

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class _FakeSession:
    def __init__(self, results: List[Any], sink: List[Any]):
        self._results = list(results)
        self._sink = sink
        self.gets: dict = {}
        self.statements: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, statement, *_a, **_k):
        self.statements.append(statement)
        item = self._results.pop(0) if self._results else _FakeResult()
        return item if isinstance(item, _FakeResult) else _FakeResult(item)

    async def get(self, model, key):
        return self.gets.get((getattr(model, "__name__", str(model)), key))

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = 9001
        self._sink.append(obj)

    async def commit(self):
        pass


def _patch_runtime(results, sink):
    orig = trs.runtime_session
    trs.runtime_session = lambda: (lambda: _FakeSession(results, sink))  # type: ignore[assignment]
    return orig


class SetStatusCasTests(unittest.TestCase):
    def test_applied_when_not_terminal(self):
        orig = _patch_runtime([_FakeResult(rowcount=1)], [])
        try:
            self.assertTrue(asyncio.run(trs.complete_run("r1")))
        finally:
            trs.runtime_session = orig  # type: ignore[assignment]

    def test_blocked_when_already_terminal(self):
        # rowcount=0 = WHERE status NOT IN 终态 未命中：已终态不可覆盖
        orig = _patch_runtime([_FakeResult(rowcount=0)], [])
        try:
            self.assertFalse(asyncio.run(trs.fail_run("r1", "late failure")))
        finally:
            trs.runtime_session = orig  # type: ignore[assignment]


class FinalizeRunTests(unittest.TestCase):
    def _with(self, applied: bool, current, expect: bool, status: str = "cancelled",
              expect_recovery: bool = False):
        async def _fake_set(run_id, s, **kw):
            return applied

        async def _fake_status(run_id):
            return current

        recovery_calls = []

        async def _fake_recover(run_id, **kwargs):
            recovery_calls.append((run_id, kwargs))
            return True

        orig_set, orig_status = trs._set_status, trs.get_run_status
        orig_recover = trs.recover_run_after_fault
        trs._set_status = _fake_set  # type: ignore[assignment]
        trs.get_run_status = _fake_status  # type: ignore[assignment]
        trs.recover_run_after_fault = _fake_recover  # type: ignore[assignment]
        try:
            self.assertEqual(asyncio.run(trs.finalize_run("r1", status)), expect)
            if expect_recovery:
                self.assertEqual([item[0] for item in recovery_calls], ["r1"])
            else:
                self.assertEqual(recovery_calls, [])
        finally:
            trs._set_status = orig_set  # type: ignore[assignment]
            trs.get_run_status = orig_status  # type: ignore[assignment]
            trs.recover_run_after_fault = orig_recover  # type: ignore[assignment]

    def test_applied_publishes(self):
        self._with(True, "cancelled", True)

    def test_conflict_with_terminal_suppresses_publish(self):
        # 已被并发收敛成 completed：晚到的取消不得再广播 run.failed
        self._with(False, "completed", False)

    def test_runtime_unavailable_does_not_publish_and_recovers(self):
        # PG 关/Run 不存在：未知不能伪装成终态，交给同一 Run 的恢复层
        self._with(False, None, False, expect_recovery=True)

    def test_raced_nonterminal_does_not_publish_and_recovers(self):
        self._with(False, "running", False, expect_recovery=True)

    def test_cas_exception_does_not_publish_and_recovers(self):
        async def _raise_cancel(run_id):
            raise RuntimeError("runtime CAS unavailable")

        async def _status(run_id):
            return "running"

        recovery_calls = []

        async def _recover(run_id, **kwargs):
            recovery_calls.append((run_id, kwargs))
            return True

        orig_cancel = trs.cancel_run
        orig_status = trs.get_run_status
        orig_recover = trs.recover_run_after_fault
        trs.cancel_run = _raise_cancel  # type: ignore[assignment]
        trs.get_run_status = _status  # type: ignore[assignment]
        trs.recover_run_after_fault = _recover  # type: ignore[assignment]
        try:
            self.assertFalse(asyncio.run(trs.finalize_run("r1", "cancelled")))
            self.assertEqual([item[0] for item in recovery_calls], ["r1"])
        finally:
            trs.cancel_run = orig_cancel  # type: ignore[assignment]
            trs.get_run_status = orig_status  # type: ignore[assignment]
            trs.recover_run_after_fault = orig_recover  # type: ignore[assignment]


class EnsureTerminalAnchorTests(unittest.TestCase):
    def _run(self, run_status, existing_row, thread_exists=True):
        from app.services.tasks import run_reconcile_service as rrs

        sink: list = []
        anchors: list = []

        async def _fake_status(run_id):
            return run_status

        async def _fake_anchor(run_id, mid, text=""):
            anchors.append((run_id, mid, text))

        orig_status, orig_anchor = trs.get_run_status, trs.record_terminal_message_event
        orig_session = rrs.async_session
        trs.get_run_status = _fake_status  # type: ignore[assignment]
        trs.record_terminal_message_event = _fake_anchor  # type: ignore[assignment]
        session = _FakeSession([_FakeResult([existing_row] if existing_row else [])], sink)
        if thread_exists:
            session.gets[("ChatThread", "t1")] = object()
        rrs.async_session = lambda: session  # type: ignore[assignment]
        try:
            mid = asyncio.run(rrs.ensure_terminal_anchor("r1", "t1"))
        finally:
            trs.get_run_status = orig_status  # type: ignore[assignment]
            trs.record_terminal_message_event = orig_anchor  # type: ignore[assignment]
            rrs.async_session = orig_session  # type: ignore[assignment]
        return mid, sink, anchors

    def test_cancelled_without_row_inserts_placeholder_and_anchor(self):
        mid, sink, anchors = self._run("cancelled", existing_row=None)
        self.assertEqual(mid, 9001)
        self.assertEqual(len(sink), 1)
        self.assertEqual(sink[0].status, "cancelled")
        self.assertEqual(sink[0].content, "（已停止，未生成回复）")
        self.assertEqual(anchors, [("r1", 9001, "（已停止，未生成回复）")])

    def test_existing_row_is_noop(self):
        mid, sink, anchors = self._run("cancelled", existing_row=123)
        self.assertIsNone(mid)
        self.assertEqual(sink, [])
        self.assertEqual(anchors, [])

    def test_non_terminal_is_noop(self):
        mid, sink, anchors = self._run("running", existing_row=None)
        self.assertIsNone(mid)
        self.assertEqual(sink, [])

    def test_policy_rejected_user_makes_terminal_anchor_context_excluded(self):
        from app.models import POLICY_REJECTED_MESSAGE_STATUS
        from app.services.tasks import run_reconcile_service as rrs

        sink: list = []
        anchors: list = []

        async def _fake_status(_run_id):
            return "failed"

        async def _fake_anchor(run_id, mid, text=""):
            anchors.append((run_id, mid, text))

        orig_status = trs.get_run_status
        orig_anchor = trs.record_terminal_message_event
        orig_session = rrs.async_session
        session = _FakeSession([
            _FakeResult([]),       # 未有 assistant 归属行
            _FakeResult([POLICY_REJECTED_MESSAGE_STATUS]),
        ], sink)
        session.gets[("ChatThread", "t1")] = object()
        trs.get_run_status = _fake_status  # type: ignore[assignment]
        trs.record_terminal_message_event = _fake_anchor  # type: ignore[assignment]
        rrs.async_session = lambda: session  # type: ignore[assignment]
        try:
            mid = asyncio.run(rrs.ensure_terminal_anchor("r1", "t1"))
        finally:
            trs.get_run_status = orig_status  # type: ignore[assignment]
            trs.record_terminal_message_event = orig_anchor  # type: ignore[assignment]
            rrs.async_session = orig_session  # type: ignore[assignment]

        self.assertEqual(mid, 9001)
        self.assertEqual(sink[0].status, POLICY_REJECTED_MESSAGE_STATUS)
        self.assertEqual(anchors[0][0:2], ("r1", 9001))


class PolicyRejectedTranscriptTests(unittest.TestCase):
    def test_quarantine_marks_user_and_existing_anchor_without_deleting_them(self):
        from app.models import POLICY_REJECTED_MESSAGE_STATUS, ChatMessage
        from app.services.memory import context_service
        from app.services.tasks import run_reconcile_service as rrs

        user = ChatMessage(
            id=1, thread_id="t1", role="user", content="qz_sensitive_test_92741",
            run_id="r1", status=None,
        )
        anchor = ChatMessage(
            id=2, thread_id="t1", role="assistant", content="失败占位",
            run_id="r1", status="failed",
        )
        session = _FakeSession([
            _FakeResult([user, anchor]),
            _FakeResult(rowcount=2),
            _FakeResult(rowcount=2),
        ], [])
        thread = type("Thread", (), {"title": "qz_sensitive_test_92741"})()
        session.gets[("ChatThread", "t1")] = thread
        orig_session = rrs.async_session
        orig_delete_summary = context_service.delete_for_thread
        orig_delete_snapshot = trs.delete_context_snapshots_for_run

        async def _delete_summary(thread_id, *, strict=False):
            self.assertEqual((thread_id, strict), ("t1", True))

        async def _delete_snapshot(run_id):
            self.assertEqual(run_id, "r1")
            return True

        rrs.async_session = lambda: session  # type: ignore[assignment]
        context_service.delete_for_thread = _delete_summary  # type: ignore[assignment]
        trs.delete_context_snapshots_for_run = _delete_snapshot  # type: ignore[assignment]
        try:
            changed = asyncio.run(rrs.quarantine_policy_rejected_run("r1", "t1"))
        finally:
            rrs.async_session = orig_session  # type: ignore[assignment]
            context_service.delete_for_thread = orig_delete_summary  # type: ignore[assignment]
            trs.delete_context_snapshots_for_run = orig_delete_snapshot  # type: ignore[assignment]

        self.assertEqual(changed, 2)
        self.assertIsNone(thread.title)
        statements = "\n".join(
            str(statement.compile(compile_kwargs={"literal_binds": True}))
            for statement in session.statements
        )
        self.assertIn("policy_pending", statements)
        self.assertIn(POLICY_REJECTED_MESSAGE_STATUS, statements)

    def test_completed_quarantine_is_idempotent_and_keeps_later_summary(self):
        from app.models import POLICY_REJECTED_MESSAGE_STATUS, ChatMessage
        from app.services.memory import context_service
        from app.services.tasks import run_reconcile_service as rrs

        user = ChatMessage(
            id=1, thread_id="t1", role="user", content="blocked",
            run_id="r1", status=POLICY_REJECTED_MESSAGE_STATUS,
        )
        anchor = ChatMessage(
            id=2, thread_id="t1", role="assistant", content="notice",
            run_id="r1", status=POLICY_REJECTED_MESSAGE_STATUS,
        )
        session = _FakeSession([_FakeResult([user, anchor])], [])
        thread = type("Thread", (), {"title": "blocked"})()
        session.gets[("ChatThread", "t1")] = thread
        orig_session = rrs.async_session
        orig_delete_summary = context_service.delete_for_thread
        orig_delete_snapshot = trs.delete_context_snapshots_for_run

        async def _unexpected(*_args, **_kwargs):
            raise AssertionError("already-converged policy Run must not delete later state")

        rrs.async_session = lambda: session  # type: ignore[assignment]
        context_service.delete_for_thread = _unexpected  # type: ignore[assignment]
        trs.delete_context_snapshots_for_run = _unexpected  # type: ignore[assignment]
        try:
            changed = asyncio.run(rrs.quarantine_policy_rejected_run("r1", "t1"))
        finally:
            rrs.async_session = orig_session  # type: ignore[assignment]
            context_service.delete_for_thread = orig_delete_summary  # type: ignore[assignment]
            trs.delete_context_snapshots_for_run = orig_delete_snapshot  # type: ignore[assignment]

        self.assertEqual(changed, 0)
        self.assertIsNone(thread.title)


class SweepReturningTests(unittest.TestCase):
    def test_stale_waiting_runs_are_observed_without_terminal_transition(self):
        orig = _patch_runtime([_FakeResult([("r1", "t1"), ("r2", "t2")], rowcount=2)], [])
        try:
            swept = asyncio.run(trs.sweep_expired_waiting(24))
        finally:
            trs.runtime_session = orig  # type: ignore[assignment]
        self.assertEqual(swept, [])


if __name__ == "__main__":
    unittest.main()
