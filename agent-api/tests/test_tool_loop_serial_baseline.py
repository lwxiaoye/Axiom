"""历史 DAG 升级前固化的串行工具循环基线。

两条契约，作为 Phase 3 只读 DAG Runtime 的**对照组**，不是永久正确性断言：

1. 事件序列基线：同轮多工具时事件顺序固定为
   plan → (tool_started → tool_result)×N → final，且 N 个工具**按模型给出的顺序**执行。
2. 串行墙钟基线：两个各 sleep t 的互不依赖只读工具，同轮总耗时 ≥ 2t（当前逐个 await）。
   Phase 3 动态 Ready Queue 落地后，等价场景的墙钟应显著低于 2t——届时本测试应被
   「并行墙钟测试」取代或改写，而不是让它反过来阻止并行化。

mock 方式仿 test_drive_model_persistence（假 httpx 流驱动 drive_model）。
"""
import asyncio
import json
import time
import unittest
from unittest.mock import patch

from app.services.agent_harness import model_driver
from app.services.agent_harness.contracts import ToolObservation
from app.services.agent_harness.model_driver import MainTool


def sse(delta: dict) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


def sse_tool_calls(calls: list) -> str:
    frags = []
    for index, (call_id, name, args) in enumerate(calls):
        frags.append({
            "index": index, "id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        })
    return sse({"tool_calls": frags})


DONE = "data: [DONE]"


class FakeStreamResponse:
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


class FakeAsyncClient:
    responses: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, json=None, headers=None):
        return FakeStreamResponse(FakeAsyncClient.responses.pop(0))


async def _drive(**kwargs):
    events = []
    with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", FakeAsyncClient):
        async for ev in model_driver.drive_model(**kwargs):
            events.append(ev)
    return events


class ToolLoopSerialBaselineTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_round_multi_tool_event_order(self):
        """事件序列基线：plan 先于一切工具事件；started/result 成对且按模型顺序推进。"""
        order: list = []

        def make_tool(name):
            async def run(args):
                order.append(name)
                return f"{name}-ok"
            return MainTool(name=name, description=name, parameters={}, execute=run)

        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "read_a", {"query": "a"}),
                             ("c2", "read_b", {"query": "b"}),
                             ("c3", "read_c", {"query": "c"})]), DONE],
            [sse({"content": "综合三个来源，结论如下。"}), DONE],
        ]
        events = await _drive(
            model="m", api_key="k", user_input="扫描三个模块",
            tools=[make_tool("read_a"), make_tool("read_b"), make_tool("read_c")],
        )
        kinds = [e["type"] for e in events]
        # plan 帧只有一次，且先于任何 tool_started
        self.assertEqual(kinds.count("plan"), 1)
        self.assertLess(kinds.index("plan"), kinds.index("tool_started"))
        plan = next(e for e in events if e["type"] == "plan")
        self.assertEqual([i["name"] for i in plan["items"]], ["read_a", "read_b", "read_c"])
        # started/result 成对、按模型给出的顺序逐个推进（当前串行语义）
        tool_seq = [(e["type"], e["name"]) for e in events if e["type"] in ("tool_started", "tool_result")]
        self.assertEqual(tool_seq, [
            ("tool_started", "read_a"), ("tool_result", "read_a"),
            ("tool_started", "read_b"), ("tool_result", "read_b"),
            ("tool_started", "read_c"), ("tool_result", "read_c"),
        ])
        self.assertEqual(order, ["read_a", "read_b", "read_c"])
        for event in (e for e in events if e["type"] == "tool_result"):
            self.assertNotIn("model_content", event["observation"])
            ToolObservation.model_validate(event["observation"])
        commentaries = [
            (i, e["text"]) for i, e in enumerate(events) if e["type"] == "commentary"
        ]
        # 首轮模型静默时才根据已提交动作补一句开场。下一轮直接给最终回答时，
        # 最终回答本身已经承接工具结果，不再额外插固定“阶段说明”。
        self.assertEqual(len(commentaries), 1)
        self.assertTrue(commentaries[0][1].strip())
        last_result_index = max(
            i for i, e in enumerate(events) if e["type"] == "tool_result"
        )
        self.assertFalse(
            any(e["type"] == "commentary" for e in events[last_result_index + 1:]),
            "模型直接回答时不应再插平台固定播报",
        )
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(final["answer"], "综合三个来源，结论如下。")
        self.assertEqual([t["status"] for t in final["trace"]], ["completed"] * 3)

    async def test_two_independent_read_tools_run_serially_baseline(self):
        """串行墙钟基线（性能对照组）：两个 sleep t 的独立只读工具，同轮墙钟 ≥ 2t。

        本断言记录现状（§2.2：for prepared_calls 逐个执行）。Phase 3 并行调度落地后
        等价场景应显著 < 2t，届时改写本测试为并行墙钟门禁（§18 Phase 3 关键门禁）。
        """
        interval = 0.15

        def make_sleep_tool(name):
            async def run(args):
                await asyncio.sleep(interval)
                return "ok"
            return MainTool(name=name, description=name, parameters={}, execute=run)

        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "slow_read_1", {}), ("c2", "slow_read_2", {})]), DONE],
            [sse({"content": "done"}), DONE],
        ]
        start = time.monotonic()
        events = await _drive(
            model="m", api_key="k", user_input="双源读取",
            tools=[make_sleep_tool("slow_read_1"), make_sleep_tool("slow_read_2")],
        )
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(
            elapsed, interval * 2,
            "当前基线是串行执行；若此断言失败说明执行语义已变（并行化），请把本测试改写为并行门禁",
        )
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(len(final["trace"]), 2)


if __name__ == "__main__":
    unittest.main()
