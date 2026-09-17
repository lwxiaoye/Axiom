"""P1 多 worker Run 租约回归（2026-07-17）。

覆盖：
1. is_run_lease_stale——空/新鲜/过期/isoformat 字符串/垃圾值；
2. chat_service._is_zombie_run 五格矩阵——own+task活 / own+task死 / 外来+新鲜 /
   外来+过期 / legacy 空 owner 空心跳；
3. create_run 落 owner_instance_id=INSTANCE_ID + heartbeat_at；
4. consume_resume_token 的 CAS UPDATE 同时接管 owner+heartbeat；
5. heartbeat_owned_runs 返回续约行数；
6. 启动对账 reconcile_orphan_running_details 只清租约过期行（租约新鲜的外来 Run 不动）。
无 DB：沿用 tests/test_terminal_cas_p0.py 的假会话工厂模式。
"""
import asyncio
import unittest
from datetime import datetime, timedelta
from typing import Any, List
from unittest.mock import patch

from app.core.config import settings
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

    def fetchone(self):
        return self._items[0] if self._items else None

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class _FakeSession:
    def __init__(self, results: List[Any], sink: List[Any]):
        self._results = list(results)
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *_a, **_k):
        item = self._results.pop(0) if self._results else _FakeResult()
        return item if isinstance(item, _FakeResult) else _FakeResult(item)

    def add(self, obj):
        self._sink.append(obj)

    async def commit(self):
        pass


def _patch_runtime(results, sink):
    orig = trs.runtime_session
    trs.runtime_session = lambda: (lambda: _FakeSession(results, sink))  # type: ignore[assignment]
    return orig


def _fresh() -> datetime:
    return datetime.utcnow()


def _expired() -> datetime:
    return datetime.utcnow() - timedelta(seconds=settings.RUN_LEASE_TTL_SECONDS + 30)


class LeaseStaleTests(unittest.TestCase):
    def test_none_run_and_empty_heartbeat_are_stale(self):
        self.assertTrue(trs.is_run_lease_stale(None))
        self.assertTrue(trs.is_run_lease_stale({}))
        self.assertTrue(trs.is_run_lease_stale({"heartbeat_at": None}))

    def test_fresh_heartbeat_not_stale(self):
        self.assertFalse(trs.is_run_lease_stale({"heartbeat_at": _fresh()}))
        self.assertFalse(trs.is_run_lease_stale({"heartbeat_at": _fresh().isoformat()}))

    def test_expired_heartbeat_stale(self):
        self.assertTrue(trs.is_run_lease_stale({"heartbeat_at": _expired()}))
        self.assertTrue(trs.is_run_lease_stale({"heartbeat_at": _expired().isoformat()}))

    def test_garbage_heartbeat_treated_as_stale(self):
        self.assertTrue(trs.is_run_lease_stale({"heartbeat_at": "not-a-timestamp"}))


class ZombieDecisionMatrixTests(unittest.TestCase):
    """chat_service._is_zombie_run 分流矩阵（P1 核心裁定）。"""

    def _decide(self, run, alive_task_for: str = "") -> bool:
        from app.services.agent_harness.orchestrator import HarnessOrchestrator

        async def _go():
            svc = HarnessOrchestrator()
            task = None
            if alive_task_for:
                task = asyncio.create_task(asyncio.sleep(30))
                svc._run_tasks[alive_task_for] = task
            try:
                return await svc._is_zombie_run(run)
            finally:
                if task is not None:
                    task.cancel()

        return asyncio.run(_go())

    def test_own_with_alive_task_is_active(self):
        run = {"id": "r1", "owner_instance_id": trs.INSTANCE_ID, "heartbeat_at": None}
        self.assertFalse(self._decide(run, alive_task_for="r1"))

    def test_own_without_task_is_zombie_even_with_fresh_lease(self):
        # own 分支不看租约：进程内事实最准，任务已死就是僵尸，不必等 TTL
        run = {"id": "r1", "owner_instance_id": trs.INSTANCE_ID,
               "heartbeat_at": _fresh().isoformat()}
        self.assertTrue(self._decide(run))

    def test_foreign_fresh_lease_is_active(self):
        # 绝不 fail 别的 worker 正在跑的 Run（扩容互杀就是这条错的）
        run = {"id": "r2", "owner_instance_id": "other-worker",
               "heartbeat_at": _fresh().isoformat()}
        self.assertFalse(self._decide(run))

    def test_foreign_expired_lease_is_zombie(self):
        run = {"id": "r3", "owner_instance_id": "other-worker",
               "heartbeat_at": _expired().isoformat()}
        self.assertTrue(self._decide(run))

    def test_legacy_empty_owner_and_heartbeat_is_zombie(self):
        # 租约上线前的存量行：owner/heartbeat 均空 → 走外来分支且租约过期，可收敛
        run = {"id": "r4", "owner_instance_id": None, "heartbeat_at": None}
        self.assertTrue(self._decide(run))


class CreateRunLeaseStampTests(unittest.TestCase):
    def test_create_run_stamps_owner_and_heartbeat(self):
        sink: list = []
        orig = _patch_runtime([], sink)
        try:
            rid = asyncio.run(trs.create_run(run_id="r1", thread_id="t1", user_id="u1"))
        finally:
            trs.runtime_session = orig  # type: ignore[assignment]
        self.assertEqual(rid, "r1")
        self.assertEqual(len(sink), 1)
        self.assertEqual(sink[0].owner_instance_id, trs.INSTANCE_ID)
        self.assertIsInstance(sink[0].heartbeat_at, datetime)


class ConsumeResumeTakeoverTests(unittest.TestCase):
    def test_cas_update_takes_over_owner_and_heartbeat(self):
        captured: list = []

        class _Capturing(_FakeSession):
            async def execute(self, stmt, *_a, **_k):
                captured.append(stmt)
                return _FakeResult(rowcount=1)

        orig = trs.runtime_session
        trs.runtime_session = lambda: (lambda: _Capturing([], []))  # type: ignore[assignment]
        try:
            ok = asyncio.run(trs.consume_resume_token("r1", None))
        finally:
            trs.runtime_session = orig  # type: ignore[assignment]
        self.assertTrue(ok)
        self.assertEqual(len(captured), 1)
        compiled = str(captured[0].compile())
        # 同一条 CAS 必须同时置 running、清 token、接管 owner+heartbeat（P1 租约）
        for col in ("status", "resume_token", "owner_instance_id", "heartbeat_at"):
            self.assertIn(col, compiled)


class HeartbeatOwnedRunsTests(unittest.TestCase):
    def test_returns_rowcount(self):
        orig = _patch_runtime([_FakeResult(rowcount=3)], [])
        try:
            self.assertEqual(asyncio.run(trs.heartbeat_owned_runs()), 3)
        finally:
            trs.runtime_session = orig  # type: ignore[assignment]

    def test_runtime_disabled_returns_zero(self):
        orig = trs.runtime_session
        trs.runtime_session = lambda: None  # type: ignore[assignment]
        try:
            self.assertEqual(asyncio.run(trs.heartbeat_owned_runs()), 0)
        finally:
            trs.runtime_session = orig  # type: ignore[assignment]


class _Row:
    """启动对账用的假行（SELECT 快照）。"""

    def __init__(self, id: str, thread_id: str, heartbeat_at, owner_instance_id: str = ""):
        self.id = id
        self.thread_id = thread_id
        self.heartbeat_at = heartbeat_at
        self.owner_instance_id = owner_instance_id
        self.status = "running"
        self.error = None
        self.resume_token = "tok"
        self.completed_at = None


class _ScriptedSession:
    """跨 session 共享脚本的假会话。

    对账收敛已改为逐行条件化 UPDATE（_fail_orphan_run 每行开一个新 session），因此结果
    脚本必须由所有 session 顺序共享——不能像 _FakeSession 那样每次进 session 复制一份。
    """

    def __init__(self, script: List[Any], captured: List[Any]):
        self._script = script          # 共享引用，不复制
        self._captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, *_a, **_k):
        self._captured.append(stmt)
        return self._script.pop(0) if self._script else _FakeResult()

    async def commit(self):
        pass


def _where_sql(stmt) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect())).split(" WHERE ", 1)[-1]


class StartupReconcileLeaseTests(unittest.TestCase):
    def _run(self, script, captured, recover_results=None):
        orig = trs.runtime_session
        trs.runtime_session = lambda: (  # type: ignore[assignment]
            lambda: _ScriptedSession(script, captured))
        recovery_calls = []
        outcomes = dict(recover_results or {})

        async def _recover(run_id, **kwargs):
            recovery_calls.append((run_id, kwargs))
            return outcomes.get(run_id, True)

        try:
            with patch.object(trs, "recover_run_after_fault", _recover):
                details = asyncio.run(trs.reconcile_orphan_running_details())
            return details, recovery_calls
        finally:
            trs.runtime_session = orig  # type: ignore[assignment]

    def test_only_lease_expired_rows_are_cleared(self):
        fresh = _Row("r-fresh", "t1", _fresh())
        stale = _Row("r-stale", "t2", _expired())
        legacy = _Row("r-legacy", "t3", None)  # 租约上线前存量：空心跳=可清
        captured: List[Any] = []
        details, recovery_calls = self._run([_FakeResult([fresh, stale, legacy])], captured)
        self.assertEqual({d["id"] for d in details}, {"r-stale", "r-legacy"})
        # 租约新鲜的外来 Run 一根手指都不能动；过期行只进入同一 Run 的恢复协调器。
        self.assertEqual(len(captured), 1)
        self.assertEqual({item[0] for item in recovery_calls}, {"r-stale", "r-legacy"})

    def test_orphan_recovery_passes_lease_snapshot(self):
        """恢复协调器必须把 owner/heartbeat 快照交给下层 CAS。"""
        stale = _Row("r-stale", "t2", _expired(), owner_instance_id="worker-a")
        captured: List[Any] = []
        details, recovery_calls = self._run([_FakeResult([stale])], captured)
        self.assertEqual([d["id"] for d in details], ["r-stale"])
        self.assertEqual(len(captured), 1)
        self.assertEqual(recovery_calls[0][0], "r-stale")
        self.assertEqual(recovery_calls[0][1]["expected_owner_instance_id"], "worker-a")
        self.assertEqual(recovery_calls[0][1]["expected_heartbeat_at"], stale.heartbeat_at)

    def test_concurrently_completed_row_is_not_overwritten(self):
        """对账窗口里原 owner 已把 Run 正常落成 completed → CAS 未命中 → 不回写 failed。

        旧实现在 ORM 对象上赋值再统一 commit（按主键无条件覆盖），会把已交付的 completed
        拍成 failed；现在未命中即放弃，且不进 details（不触发中断正文回填）。
        """
        done = _Row("r-done", "t1", _expired())      # 快照里还是 running，写回时已 completed
        stale = _Row("r-stale", "t2", _expired())
        captured: List[Any] = []
        details, recovery_calls = self._run(
            [_FakeResult([done, stale])], captured, {"r-done": False},
        )
        self.assertEqual([d["id"] for d in details], ["r-stale"])
        self.assertNotIn("r-done", {d["id"] for d in details})
        self.assertEqual([item[0] for item in recovery_calls], ["r-done", "r-stale"])


if __name__ == "__main__":
    unittest.main()
