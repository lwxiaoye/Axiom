"""create_run 必须真的构造并落库 AgentRun（回归守护）。

背景：create_run 的 try 里若有未定义名/构造错误（如曾经的 state=state NameError），会被通用
except 吞成 None——请求不报错，但 Run 从不落库、部分唯一索引不参与 claim、R0 静音退化。
py_compile 抓不到运行时 NameError，纯逻辑 pytest 又不连 PG，故用假会话工厂在无 DB 下守住
「create_run 返回 run_id 且确实 add 了一个 AgentRun 行」这条底线。
"""
import asyncio
import unittest

from app.services.tasks import task_run_service as trs


class _FakeSession:
    def __init__(self, sink):
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def add(self, obj):
        self._sink.append(obj)

    async def commit(self):
        pass


class CreateRunPersistsTests(unittest.TestCase):
    def test_returns_run_id_and_adds_row(self):
        sink: list = []
        orig = trs.runtime_session
        trs.runtime_session = lambda: (lambda: _FakeSession(sink))  # type: ignore[assignment]
        try:
            rid = asyncio.run(trs.create_run(
                run_id="run-x", thread_id="t1", user_id="u1", kind="chat", goal="g",
            ))
        finally:
            trs.runtime_session = orig  # type: ignore[assignment]
        # 若构造抛错被吞 → rid 为 None、sink 为空，这条断言即失败
        self.assertEqual(rid, "run-x")
        self.assertEqual(len(sink), 1)
        row = sink[0]
        self.assertEqual(row.id, "run-x")
        self.assertEqual(row.thread_id, "t1")
        self.assertEqual(row.status, "running")


if __name__ == "__main__":
    unittest.main()
