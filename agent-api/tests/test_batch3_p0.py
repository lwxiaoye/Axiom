"""第三批 P0/P1 回归（2026-07-17）。

1. append_run_event：max(sequence)+1 续号补录；
2. 子智能体私有思考不进入事件，仅回放公开输出；
4. cancel_chat_run：执行任务未收敛 → {"status":"pending"}，收敛 → {"status":"cancelled"}；
5. _fail_zombie_run：CAS 生效才补 run.failed 事件+锚点；
6. 计划卡空反馈/未知决策 → 重挂起（新令牌+重发 task.plan.ready），不再 run_disposition=failed；
7. DAG 恢复上下文缺失 → final 带 run_disposition=failed。
"""
import asyncio
import unittest
from types import SimpleNamespace
from typing import Any, List

from app.services.tasks import task_run_service as trs
from app.services.agent_harness.run_store import HARNESS_STATE_SCHEMA_VERSION


class _FakeResult:
    def __init__(self, items=None, rowcount=0):
        self._items = items or []
        self.rowcount = rowcount

    def scalars(self):
        return self

    def all(self):
        return self._items

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class _FakeSession:
    def __init__(self, results: List[Any], sink: List[Any], get_result: Any = None):
        self._results = list(results)
        self._sink = sink
        self._get_result = get_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *_a, **_k):
        item = self._results.pop(0) if self._results else _FakeResult()
        return item if isinstance(item, _FakeResult) else _FakeResult(item)

    async def get(self, *_a, **_k):
        return self._get_result

    def add(self, obj):
        self._sink.append(obj)

    async def commit(self):
        pass


class AppendRunEventTests(unittest.TestCase):
    def test_appends_with_next_sequence(self):
        sink: list = []
        run = SimpleNamespace(
            state={
                "schema_version": HARNESS_STATE_SCHEMA_VERSION,
                "event_cursor": 11,
            },
            state_version=2,
        )
        orig = trs.runtime_session
        trs.runtime_session = lambda: (  # type: ignore[assignment]
            lambda: _FakeSession([_FakeResult([11])], sink, get_result=run)
        )
        try:
            asyncio.run(trs.append_run_event("r1", "run.failed", {"message": "x"}))
        finally:
            trs.runtime_session = orig  # type: ignore[assignment]
        self.assertEqual(len(sink), 1)
        self.assertEqual(sink[0].sequence, 12)
        self.assertEqual(sink[0].type, "run.failed")
        self.assertEqual(sink[0].data, {"message": "x"})
        self.assertEqual(run.state["event_cursor"], 12)
        self.assertEqual(run.state_version, 3)


class _FakeTask:
    """done() 按脚本返回；cancel() 记录调用。"""

    def __init__(self, done_seq):
        self._done_seq = list(done_seq)
        self.cancelled_calls = 0

    def done(self):
        return self._done_seq.pop(0) if self._done_seq else True

    def cancel(self):
        self.cancelled_calls += 1


class CancelPendingTests(unittest.TestCase):
    def _cancel(self, fake_task):
        import app.services.agent_harness.orchestrator as cs_mod
        svc = cs_mod.harness_orchestrator
        run_id = "run-cancel-test"
        svc._run_meta[run_id] = {"user_id": "u1", "thread_id": "t1", "protocol": "v1"}
        svc._run_tasks[run_id] = fake_task

        async def _fake_get_run(rid, uid):
            return None

        async def _noop_wait(tasks, timeout=None):
            return set(), set(tasks)

        orig_get_run = trs.get_run
        orig_wait = cs_mod.asyncio.wait
        trs.get_run = _fake_get_run  # type: ignore[assignment]
        cs_mod.asyncio.wait = _noop_wait  # type: ignore[assignment]
        try:
            return asyncio.run(svc.cancel_chat_run("u1", run_id))
        finally:
            trs.get_run = orig_get_run  # type: ignore[assignment]
            cs_mod.asyncio.wait = orig_wait  # type: ignore[assignment]
            svc._run_meta.pop(run_id, None)
            svc._run_tasks.pop(run_id, None)

    def test_unconverged_returns_pending(self):
        # done(): 初判 False → 等待后仍 False → pending，且绝不返回成功
        fake = _FakeTask([False, False])
        result = self._cancel(fake)
        self.assertEqual(result, {"status": "pending"})
        self.assertEqual(fake.cancelled_calls, 1)

    def test_converged_returns_cancelled(self):
        fake = _FakeTask([False, True])
        result = self._cancel(fake)
        self.assertEqual(result, {"status": "cancelled"})

    def test_partial_persist_timeout_returns_pending(self):
        """部分正文落库未在等待窗口内提交 → pending（P1 修正 2026-07-26）。

        此前 `except Exception: pass` 把 TimeoutError 吞掉仍回 cancelled：前端据此认定
        「已停干净」立刻发下一条，迟到的部分回复 created_at 反而晚于新用户消息。
        """
        import app.services.agent_harness.orchestrator as cs_mod
        svc = cs_mod.harness_orchestrator
        run_id = "run-cancel-persist-timeout"
        fake = _FakeTask([False, True])  # pump 本身已收敛，只有落库任务还没提交
        svc._run_meta[run_id] = {"user_id": "u1", "thread_id": "t1", "protocol": "v1"}
        svc._run_tasks[run_id] = fake

        async def _fake_get_run(rid, uid):
            return None

        async def _noop_wait(tasks, timeout=None):
            return set(tasks), set()

        async def _timeout_wait_for(aw, timeout=None):
            raise asyncio.TimeoutError()

        async def _drive():
            pending = asyncio.get_running_loop().create_future()
            svc._persist_tasks_by_run[run_id] = pending
            try:
                return await svc.cancel_chat_run("u1", run_id)
            finally:
                pending.cancel()
                svc._persist_tasks_by_run.pop(run_id, None)

        orig_get_run = trs.get_run
        orig_wait, orig_wait_for = cs_mod.asyncio.wait, cs_mod.asyncio.wait_for
        trs.get_run = _fake_get_run  # type: ignore[assignment]
        cs_mod.asyncio.wait = _noop_wait  # type: ignore[assignment]
        cs_mod.asyncio.wait_for = _timeout_wait_for  # type: ignore[assignment]
        try:
            result = asyncio.run(_drive())
        finally:
            trs.get_run = orig_get_run  # type: ignore[assignment]
            cs_mod.asyncio.wait = orig_wait  # type: ignore[assignment]
            cs_mod.asyncio.wait_for = orig_wait_for  # type: ignore[assignment]
            svc._run_meta.pop(run_id, None)
            svc._run_tasks.pop(run_id, None)
        self.assertEqual(result, {"status": "pending"})


class RecoverZombieRunTests(unittest.TestCase):
    def test_zombie_facade_no_longer_exposes_failed_terminal_path(self):
        import app.services.agent_harness.orchestrator as cs_mod
        import app.services.chat.run_hub as run_hub

        self.assertFalse(hasattr(cs_mod.harness_orchestrator, "_fail_zombie_run"))
        self.assertTrue(hasattr(run_hub, "recover_run_after_error"))



# PlanCardResuspendTests / DagResumeMissingContextTests 随 DAG Runtime 一起删除
#（2026-07-27）：它们测的是 plan_profile 计划卡重挂起与 orchestrator.resume_task_graph_turn，
# 两者都属于已下线的 task_graph 执行核心。
