"""长期记忆写入的并发保护（深扫 P1-2，2026-07-26）。

store_memory 是「查重 → 语义治理 → 插入」的读改写，表上无唯一约束、无行锁，
READ COMMITTED 下并发两次写同一事实（轮后 fire-and-forget 抽取 + remember_fact
工具同轮调用）会各自查不到对方、双双插入 → 污染 recall Top-K、更快撞
MAX_MEMORIES_PER_USER。修复=按 user_id 的进程内 asyncio.Lock 串行化临界区。

无 DB：沿用 tests/test_run_lease_p1.py 的假会话工厂模式；execute/commit 里插 await，
让两个协程必然在「查重 → 插入」之间交错（无锁时稳定双插）。
"""
import asyncio
import unittest
from datetime import datetime

from app.services.memory import memory_service as ms


class _Row:
    def __init__(self, id: str, content: str):
        self.id = id
        self.content = content
        self.status = "active"
        self.confidence = 100
        self.updated_at = datetime.now()


class _FakeScalars:
    def __init__(self, items):
        self._items = list(items)

    def scalars(self):
        return self

    def first(self):
        return self._items[0] if self._items else None

    def all(self):
        return list(self._items)


class _FakeSession:
    """极简内存表：查重/同类候选查询都返回 active 行，插入在 commit 时落表。"""

    def __init__(self, store: list):
        self._store = store
        self._added: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, stmt):
        await asyncio.sleep(0)  # 真实实现是一次网络往返：制造交错窗口
        if "offset" in str(stmt).lower():  # 容量上限查询
            return _FakeScalars([])
        return _FakeScalars([r for r in self._store if r.status == "active"])

    def add(self, obj):
        self._added.append(obj)

    async def commit(self):
        await asyncio.sleep(0)
        for obj in self._added:
            self._store.append(_Row(obj.id, obj.content))
        self._added.clear()


class MemoryWriteConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store: list = []
        self.content = "用户在信息学院读大三"

        self._orig_factory = ms.runtime_session
        self._orig_enabled = ms.is_enabled
        self._orig_embed = ms._embed_many

        ms.runtime_session = lambda: (lambda: _FakeSession(self.store))  # type: ignore[assignment]

        async def _enabled(_user_id, **_kw):
            return True

        async def _no_embed(_texts):
            return None  # embedding 不可用 → 只走精确去重路径

        ms.is_enabled = _enabled  # type: ignore[assignment]
        ms._embed_many = _no_embed  # type: ignore[assignment]

    def tearDown(self):
        ms.runtime_session = self._orig_factory  # type: ignore[assignment]
        ms.is_enabled = self._orig_enabled  # type: ignore[assignment]
        ms._embed_many = self._orig_embed  # type: ignore[assignment]

    async def test_concurrent_same_fact_inserts_once(self):
        results = await asyncio.gather(
            ms.store_memory(user_id="u1", mem_type="fact", content=self.content, return_new=True),
            ms.store_memory(user_id="u1", mem_type="fact", content=self.content, return_new=True),
        )

        actives = [r for r in self.store if r.status == "active"]
        self.assertEqual(len(actives), 1, f"并发双写落了 {len(actives)} 条")
        # 一条真新建、一条命中去重（重复强化 → is_new=False）
        self.assertEqual(sorted(is_new for _mid, is_new in results), [False, True])
        self.assertTrue(all(mid for mid, _is_new in results))
        # 两次返回的是同一条记忆 id（去重命中既有条，而不是各写各的）
        self.assertEqual({mid for mid, _is_new in results}, {actives[0].id})

    async def test_lock_is_per_user_and_stable(self):
        lock_a = ms._write_lock_for("u1")
        lock_b = ms._write_lock_for("u2")
        self.assertIsNot(lock_a, lock_b)
        self.assertIs(lock_a, ms._write_lock_for("u1"))


if __name__ == "__main__":
    unittest.main()
