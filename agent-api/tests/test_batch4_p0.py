"""第四批收尾回归（2026-07-17）。

1. 重新生成版本化：drop_last_assistant 软标记 superseded（不物理删除）；
2. 思考尾巴聚合：burst 中断（取消轮）/旧数据从 reasoning_delta 行还原真实原文；
3. DSML 整轮皆协议 → 本轮如实失败，不得空回合假成功；
4. 非流式端点 R0 守卫：撞活动 Run 返回 409。
"""
import asyncio
import json
import unittest
from datetime import datetime
from typing import Any, List
from unittest.mock import patch

from fastapi import HTTPException

from app.services.agent_harness import model_driver
from app.services.tasks import task_run_service as trs


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
    def __init__(self, results: List[Any], sink: List[Any]):
        self._results = list(results)
        self._sink = sink
        self.deleted: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *_a, **_k):
        item = self._results.pop(0) if self._results else _FakeResult()
        return item if isinstance(item, _FakeResult) else _FakeResult(item)

    async def delete(self, obj):
        self.deleted.append(obj)

    def add(self, obj):
        self._sink.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        pass


class RegenerateSupersedeTests(unittest.TestCase):
    def test_marks_superseded_instead_of_delete(self):
        from app.models import ChatMessage
        from app.services.chat import turn_finalizer

        last = ChatMessage(thread_id="t1", role="assistant", content="旧回答", status="completed")
        session = _FakeSession([_FakeResult([last])], [])
        asyncio.run(turn_finalizer.drop_last_assistant(session, "t1"))
        self.assertEqual(last.status, "superseded")
        self.assertEqual(session.deleted, [])  # 绝不物理删除

    def test_last_user_message_untouched(self):
        from app.models import ChatMessage
        from app.services.chat import turn_finalizer

        last = ChatMessage(thread_id="t1", role="user", content="问题")
        session = _FakeSession([_FakeResult([last])], [])
        asyncio.run(turn_finalizer.drop_last_assistant(session, "t1"))
        self.assertIsNone(last.status)


def _ev(run_id, seq, etype, data):
    from app.runtime_models import AgentRunEvent
    e = AgentRunEvent(run_id=run_id, event_id=f"e{seq}", sequence=seq, type=etype, data=data)
    e.created_at = datetime(2026, 7, 17, 12, 0, 0)
    return e


def _run_row(run_id, status="completed"):
    from app.runtime_models import AgentRun
    r = AgentRun(id=run_id, thread_id="t1", user_id="u1", status=status, kind="chat")
    r.created_at = datetime(2026, 7, 17, 11, 59)
    r.completed_at = datetime(2026, 7, 17, 12, 1)
    return r


class ReasoningTailTests(unittest.TestCase):
    def _traces(self, run_status, events, reasoning_run_ids, tail_datas):
        # V2 生产历史不再查询/投影私有推理；第三次查询是 RunInstruction。
        results = [
            _FakeResult([_run_row("r1", run_status)]),
            _FakeResult(events),
            _FakeResult([]),
        ]
        orig = trs.runtime_session
        trs.runtime_session = lambda: (lambda: _FakeSession(results, []))  # type: ignore[assignment]
        try:
            return asyncio.run(trs.get_execution_traces_by_thread("t1"))
        finally:
            trs.runtime_session = orig  # type: ignore[assignment]

    def test_cancelled_mid_burst_keeps_completed_compact_only(self):
        events = [
            _ev("r1", 1, "message.reasoning.completed", {"text": "第一段"}),
            _ev("r1", 2, "message.completed", {"text": "部分", "message_id": 42}),
        ]
        traces = self._traces("cancelled", events, ["r1"],
                              [{"text": "被打"}, {"text": "断的尾巴"}])
        thinking = [s["text"] for s in traces[42]["steps"] if s.get("kind") == "thinking"]
        self.assertEqual(thinking, ["第一段"])

    def test_legacy_run_does_not_restore_private_reasoning(self):
        events = [_ev("r1", 1, "message.completed", {"text": "答", "message_id": 42})]
        traces = self._traces("completed", events, ["r1"],
                              [{"text": "旧数据"}, {"text": "完整思考"}])
        thinking = [s["text"] for s in traces[42]["steps"] if s.get("kind") == "thinking"]
        self.assertEqual(thinking, [])

    def test_empty_tail_does_not_invent_reasoning_copy(self):
        events = [_ev("r1", 1, "message.completed", {"text": "答", "message_id": 42})]
        traces = self._traces("completed", events, ["r1"], [])
        thinking = [s["text"] for s in traces[42]["steps"] if s.get("kind") == "thinking"]
        self.assertEqual(thinking, [])


def _sse(delta: dict) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


DONE = "data: [DONE]"


class _FakeStreamResponse:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    responses: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, json=None, headers=None):
        return _FakeStreamResponse(_FakeAsyncClient.responses.pop(0))


class ProtocolOnlyRoundFailsTests(unittest.IsolatedAsyncioTestCase):
    async def test_pure_dsml_round_raises(self):
        """整轮正文都是文本工具协议、截断后一无所有 → 如实抛错，不空回合假成功。"""
        _FakeAsyncClient.responses = [
            [_sse({"content": "<|DSML|tool_calls>{\"name\":\"x\"}"}), DONE],
        ]
        with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", _FakeAsyncClient):
            with self.assertRaises(RuntimeError) as ctx:
                async for _ev2 in model_driver.drive_model(
                        model="m", api_key="k", user_input="hi", tools=[]):
                    pass
        self.assertIn("文本工具协议", str(ctx.exception))

    async def test_leak_with_real_content_keeps_going(self):
        """标记前有真实正文 → 截断后照常收尾，不误伤。"""
        _FakeAsyncClient.responses = [
            [_sse({"content": "正常回答。<|DSML|tool_calls>内部协议"}), DONE],
        ]
        events = []
        with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", _FakeAsyncClient):
            async for ev in model_driver.drive_model(
                    model="m", api_key="k", user_input="hi", tools=[]):
                events.append(ev)
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(final["answer"], "正常回答。")


if __name__ == "__main__":
    unittest.main()
