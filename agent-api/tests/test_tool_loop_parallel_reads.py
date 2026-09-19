"""只读工具并发执行（对齐 Claude「同一轮的多个 tool_use 应并发执行」，2026-07-27）。

设计要点：并发**只发生在执行这一步**。事件下发、错误指纹记账、trace/messages 追加仍由
主循环严格按模型给出的顺序串行完成——所以对外可观察行为与全串行完全一致，只是把多次
网络等待叠在一起。有副作用的工具（写文件/bash/子智能体）一律不参与。

本测试锁住：
1. 两个 parallel_safe 只读工具同轮墙钟 < 2t（真的并发了）；
2. 事件顺序仍是 plan → (started→result)×N 按模型顺序（对外行为不变）；
3. 混批时只有 parallel_safe 的并发，写工具仍串行且在其之后不被提前执行；
4. 只有 1 个候选时不启用并发（无价值）；开关关掉时全串行；
5. 未标记 parallel_safe 的工具（默认）不参与并发——既有工具不会被悄悄改变行为。

mock 方式仿 test_tool_loop_serial_baseline（假 httpx 流驱动 drive_model）。
"""
import asyncio
import json
import time
import unittest
from unittest.mock import patch

from app.services.agent_harness import model_driver
from app.services.chat.tools.base import MainTool


def sse(delta: dict) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


def sse_tool_calls(calls: list) -> str:
    frags = [
        {"index": i, "id": call_id, "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}
        for i, (call_id, name, args) in enumerate(calls)
    ]
    return sse({"tool_calls": frags})


DONE = "data: [DONE]"
INTERVAL = 0.15


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
    last_response: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, json=None, headers=None):
        # 脚本耗尽时**重放最后一条**，不再 IndexError（2026-07-27）。
        # 起因：主循环新增「交付前自查」网——动过手的回合收尾前会多一轮 LLM 往返，于是每个
        # 把应答条数写死的用例都 pop from empty list。重放最后一条让这一轮对既有用例**透明**：
        # 断言的仍是脚本里那句最终回答。用例要验的是**行为**，不是"恰好几次 LLM 往返"。
        if not FakeAsyncClient.responses:
            return FakeStreamResponse(getattr(FakeAsyncClient, "last_response", None) or [])
        FakeAsyncClient.last_response = FakeAsyncClient.responses.pop(0)
        return FakeStreamResponse(FakeAsyncClient.last_response)


async def _drive(**kwargs):
    events = []
    with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", FakeAsyncClient):
        async for ev in model_driver.drive_model(**kwargs):
            events.append(ev)
    return events


def _sleep_tool(name: str, order: list, parallel_safe: bool, delay: float = INTERVAL) -> MainTool:
    async def run(args):
        order.append(f"{name}:start")
        await asyncio.sleep(delay)
        order.append(f"{name}:end")
        return f"{name}-ok"
    return MainTool(name=name, description=name, parameters={},
                    execute=run, internal=True, parallel_safe=parallel_safe)


class ParallelReadToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_exclusive_tool_is_a_hard_barrier(self):
        """[read A, write B, read B] must execute exactly in that dependency order."""
        order: list = []

        async def write_run(args):
            order.append("write:start")
            await asyncio.sleep(0.01)
            order.append("write:end")
            return "written"

        FakeAsyncClient.responses = [
            [sse_tool_calls([
                ("c1", "read_a", {"query": "a"}),
                ("c2", "edit_file", {"file_id": "b"}),
                ("c3", "read_b", {"query": "b"}),
            ]), DONE],
            [sse({"content": "完成。"}), DONE],
        ]
        await _drive(
            model="m", api_key="k", user_input="读写读",
            tools=[
                _sleep_tool("read_a", order, True, 0.02),
                MainTool("edit_file", "write", {}, write_run, internal=True),
                _sleep_tool("read_b", order, True, 0.02),
            ],
        )
        self.assertEqual(order, [
            "read_a:start", "read_a:end", "write:start", "write:end",
            "read_b:start", "read_b:end",
        ])

    async def test_dynamic_execution_mode_can_downgrade_to_exclusive(self):
        order: list = []

        async def dynamic(args):
            label = str(args.get("label"))
            order.append(f"{label}:start")
            await asyncio.sleep(0.01)
            order.append(f"{label}:end")
            return label

        tool = MainTool(
            "dynamic_read", "dynamic", {}, dynamic, internal=True, parallel_safe=True,
            dispatch_mode=lambda args: "exclusive" if args.get("lock") else "parallel",
        )
        FakeAsyncClient.responses = [
            [sse_tool_calls([
                ("c1", "dynamic_read", {"label": "a"}),
                ("c2", "dynamic_read", {"label": "barrier", "lock": True}),
                ("c3", "dynamic_read", {"label": "b"}),
            ]), DONE],
            [sse({"content": "完成。"}), DONE],
        ]
        await _drive(model="m", api_key="k", user_input="动态屏障", tools=[tool])
        self.assertEqual(order, [
            "a:start", "a:end", "barrier:start", "barrier:end", "b:start", "b:end",
        ])

    async def test_two_parallel_safe_reads_overlap(self):
        """两个只读工具真的并发：墙钟明显小于 2t。"""
        order: list = []
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "read_a", {"query": "a"}),
                             ("c2", "read_b", {"query": "b"})]), DONE],
            [sse({"content": "两边都查完了。"}), DONE],
        ]
        t0 = time.monotonic()
        events = await _drive(
            model="m", api_key="k", user_input="并行查两处",
            tools=[_sleep_tool("read_a", order, True), _sleep_tool("read_b", order, True)],
        )
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, INTERVAL * 1.8,
                        f"两个只读工具应并发，墙钟 {elapsed:.3f}s 不该接近 2×{INTERVAL}")
        # 真并发的判据：b 在 a 结束前就已开始
        self.assertLess(order.index("read_b:start"), order.index("read_a:end"),
                        f"执行未重叠: {order}")
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(final["answer"], "两边都查完了。")

    async def test_event_order_unchanged_under_parallelism(self):
        """对外可观察行为不变：仍是 plan 在前，started/result 按模型顺序成对。"""
        order: list = []
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "read_a", {"query": "a"}),
                             ("c2", "read_b", {"query": "b"}),
                             ("c3", "read_c", {"query": "c"})]), DONE],
            [sse({"content": "汇总完毕。"}), DONE],
        ]
        events = await _drive(
            model="m", api_key="k", user_input="并行查三处",
            tools=[_sleep_tool(n, order, True, 0.02) for n in ("read_a", "read_b", "read_c")],
        )
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds.count("plan"), 1)
        self.assertLess(kinds.index("plan"), kinds.index("tool_started"))
        tool_seq = [(e["type"], e["name"]) for e in events
                    if e["type"] in ("tool_started", "tool_result")]
        self.assertEqual(tool_seq, [
            ("tool_started", "read_a"), ("tool_result", "read_a"),
            ("tool_started", "read_b"), ("tool_result", "read_b"),
            ("tool_started", "read_c"), ("tool_result", "read_c"),
        ], "并发不得改变对外事件顺序")
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual([t["status"] for t in final["trace"]], ["completed"] * 3)

    async def test_write_tool_never_joins_parallel_group(self):
        """混批：写工具不参与并发，且不会被只读工具的并发提前带跑。"""
        order: list = []
        write_started: list = []

        async def write_run(args):
            write_started.append(time.monotonic())
            return "written"

        # 写工具夹具用现役的 edit_file（2026-07-29）：并发判据看的是 readonly/
        # parallel_safe 标志而不是名字，所以换名不改行为；但写退休名会让人以为它还活着。
        writer = MainTool(name="edit_file", description="写", parameters={},
                          execute=write_run, internal=True)  # 默认 parallel_safe=False
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "read_a", {"query": "a"}),
                             ("c2", "read_b", {"query": "b"}),
                             ("c3", "edit_file", {"file_id": "f1"})]), DONE],
            [sse({"content": "读完并写好了。"}), DONE],
        ]
        events = await _drive(
            model="m", api_key="k", user_input="读两处再写",
            tools=[_sleep_tool("read_a", order, True), _sleep_tool("read_b", order, True), writer],
        )
        self.assertEqual(len(write_started), 1)
        # 写工具在两个读都结束之后才开始（串行消费保证）
        self.assertEqual(order[-1], "read_b:end")
        tool_seq = [e["name"] for e in events if e["type"] == "tool_result"]
        self.assertEqual(tool_seq, ["read_a", "read_b", "edit_file"])

    async def test_unmarked_tools_stay_serial(self):
        """既有工具（未标 parallel_safe）行为不变：仍然串行。"""
        order: list = []
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "read_a", {"query": "a"}),
                             ("c2", "read_b", {"query": "b"})]), DONE],
            [sse({"content": "好了。"}), DONE],
        ]
        t0 = time.monotonic()
        await _drive(
            model="m", api_key="k", user_input="查两处",
            tools=[_sleep_tool("read_a", order, False), _sleep_tool("read_b", order, False)],
        )
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, INTERVAL * 2,
                                f"未标记的工具必须保持串行，实测 {elapsed:.3f}s")
        self.assertEqual(order, ["read_a:start", "read_a:end", "read_b:start", "read_b:end"])

    async def test_single_read_does_not_use_parallel_path(self):
        """只有一个候选时不启用并发（没价值），结果照常。"""
        order: list = []
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "read_a", {"query": "a"})]), DONE],
            [sse({"content": "查到了。"}), DONE],
        ]
        events = await _drive(
            model="m", api_key="k", user_input="查一处",
            tools=[_sleep_tool("read_a", order, True, 0.01)],
        )
        self.assertEqual(order, ["read_a:start", "read_a:end"])
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(final["answer"], "查到了。")

    async def test_switch_off_falls_back_to_serial(self):
        """开关置 false 立即回到全串行（线上出问题的兜底旋钮）。"""
        order: list = []
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "read_a", {"query": "a"}),
                             ("c2", "read_b", {"query": "b"})]), DONE],
            [sse({"content": "好了。"}), DONE],
        ]
        with patch.object(model_driver.settings, "TOOL_LOOP_PARALLEL_READS", False):
            t0 = time.monotonic()
            await _drive(
                model="m", api_key="k", user_input="查两处",
                tools=[_sleep_tool("read_a", order, True), _sleep_tool("read_b", order, True)],
            )
            elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, INTERVAL * 2, f"关掉开关必须全串行，实测 {elapsed:.3f}s")

    async def test_one_tool_failing_does_not_break_the_batch(self):
        """并发批里有工具抛错：该条记 failed，其余照常，回合不中断。"""
        order: list = []

        async def boom(args):
            raise RuntimeError("下游炸了")

        bad = MainTool(name="read_bad", description="坏", parameters={},
                       execute=boom, internal=True, parallel_safe=True)
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "read_a", {"query": "a"}),
                             ("c2", "read_bad", {"query": "b"})]), DONE],
            [sse({"content": "一个查到了，一个失败了。"}), DONE],
        ]
        events = await _drive(
            model="m", api_key="k", user_input="查两处",
            tools=[_sleep_tool("read_a", order, True, 0.01), bad],
        )
        results = {e["name"]: e["status"] for e in events if e["type"] == "tool_result"}
        self.assertEqual(results.get("read_a"), "completed")
        self.assertEqual(results.get("read_bad"), "failed")
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(final["answer"], "一个查到了，一个失败了。")


class ProgressAttributionTests(unittest.IsolatedAsyncioTestCase):
    """并发只读工具共用一条 tool_progress_queue：进度必须精确归属，不误吞、不串台。

    并发化之前只有一个 drain 在跑，「按工具名匹配、不匹配丢弃」够用；并发后
    glob 的 drain 会把 search_web 的「正在阅读 X」取走扔掉，两次并发
    search_web 更会互相串台。修法：上报方用 contextvar 盖 call_id，消费方按
    call_id 精确归属，不属于自己的原样放回队列。
    """

    async def _progress_tool(self, name: str, queue, labels: list, delay: float):
        from app.services.chat.tools.base import current_tool_call_id

        async def run(args):
            for label in labels:
                await queue.put({
                    "call_id": current_tool_call_id(),
                    "name": name, "stage": "reading", "label": label, "detail": {},
                })
                await asyncio.sleep(delay)
            return f"{name}-ok"
        return MainTool(name=name, description=name, parameters={},
                        execute=run, internal=True, parallel_safe=True)

    async def test_progress_is_not_stolen_by_the_other_tool(self):
        """慢工具的进度不会被同批快工具的 drain 吞掉。"""
        queue: asyncio.Queue = asyncio.Queue()
        slow = await self._progress_tool("read_slow", queue, ["读第一页", "读第二页"], 0.05)
        fast = await self._progress_tool("read_fast", queue, ["快查"], 0.0)
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "read_fast", {"query": "f"}),
                             ("c2", "read_slow", {"query": "s"})]), DONE],
            [sse({"content": "都查完了。"}), DONE],
        ]
        events = await _drive(
            model="m", api_key="k", user_input="一快一慢",
            tools=[fast, slow], tool_progress_queue=queue,
        )
        progress = [(e.get("name"), e.get("label")) for e in events if e["type"] == "tool_progress"]
        self.assertIn(("read_slow", "读第一页"), progress, f"慢工具进度被吞了: {progress}")
        self.assertIn(("read_slow", "读第二页"), progress, f"慢工具进度被吞了: {progress}")
        self.assertIn(("read_fast", "快查"), progress)

    async def test_same_name_concurrent_calls_do_not_cross_talk(self):
        """两次并发 search_web：各自的进度必须归到各自的调用，不串台。"""
        queue: asyncio.Queue = asyncio.Queue()
        from app.services.chat.tools.base import current_tool_call_id
        seen: dict = {}

        async def run(args):
            cid = current_tool_call_id()
            seen[args["query"]] = cid
            await queue.put({
                "call_id": cid, "name": "search_web", "stage": "reading",
                "label": f"正在阅读 {args['query']}", "detail": {},
            })
            await asyncio.sleep(0.05)
            return f"{args['query']}-ok"

        tool = MainTool(name="search_web", description="搜", parameters={},
                        execute=run, internal=True, parallel_safe=True)
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "search_web", {"query": "甲"}),
                             ("c2", "search_web", {"query": "乙"})]), DONE],
            [sse({"content": "两个都搜到了。"}), DONE],
        ]
        events = await _drive(
            model="m", api_key="k", user_input="搜甲和乙",
            tools=[tool], tool_progress_queue=queue,
        )
        # 两次调用各自拿到不同的 call_id（contextvar 在并发任务间是隔离的）
        self.assertEqual(len(set(seen.values())), 2, f"两次并发调用应有不同 call_id: {seen}")
        # 关键判据：进度必须**各自归到各自那段** started→result 之间，而不是全被
        # 第一次调用的 drain 卷走（旧逻辑按同名匹配，两条都会落进第一张卡）。
        groups: list = []
        cur = None
        for e in events:
            if e["type"] == "tool_started":
                cur = []
            elif e["type"] == "tool_progress" and cur is not None:
                cur.append(e.get("label"))
            elif e["type"] == "tool_result" and cur is not None:
                groups.append(cur)
                cur = None
        self.assertEqual(len(groups), 2, f"应有两段工具执行: {groups}")
        self.assertEqual([len(g) for g in groups], [1, 1],
                         f"每次调用各自 1 条进度，不得串台: {groups}")
        # 用集合比较：两次调用的完成先后本就不保证（并发），只要各归各的即可
        self.assertEqual({g[0] for g in groups}, {"正在阅读 甲", "正在阅读 乙"})


class MetaAttributionTests(unittest.IsolatedAsyncioTestCase):
    """并发同名调用的**元信息**同样必须精确归属（与上面的进度是同一个病）。

    tool_meta_sink 原来按工具名寻址：同一轮里两个并发 read_file 都写 sink["read_file"]，
    后写覆盖先写——第一条 tool.completed 拿到的是另一次调用的文件名，第二条 pop 到 None
    （执行卡没有动作标签）；search_web 则会丢掉其中一次的 [图N]→URL 映射。
    修法同进度队列：按 call_id 归属（ToolMetaSink + pop_tool_meta）。
    """

    @staticmethod
    async def _replay(events: list):
        for ev in events:
            yield ev

    async def _completed_metas(self, events: list, sink) -> list:
        """把循环事件走一遍装配层，取出每条 tool.completed 的 (name, meta)。"""
        from app.services.chat.main_tool_turn import map_tool_loop_events
        from app.services.chat.types import TurnOutcome
        from app.services.sse_protocol import HARNESS, SSEChannel

        channel = SSEChannel(HARNESS, "th-meta", "run-meta")
        frames = [
            f async for f in map_tool_loop_events(
                channel, self._replay(events), TurnOutcome(), sink)
            if f
        ]
        out = []
        for frame in frames:
            payload = json.loads(frame[len("data: "):])
            if payload.get("type") == "tool.completed":
                data = payload.get("data") or {}
                out.append((data.get("name"), data.get("meta")))
        return out

    def _read_tool(self, sink):
        async def run(args):
            path = str(args.get("path") or "")
            await asyncio.sleep(0.05)  # 两次调用真的重叠，才谈得上覆盖
            sink["read_file"] = {"action": {"operation": "read", "target": path}}
            return f"{path} 的内容"
        return MainTool(name="read_file", description="读", parameters={},
                        execute=run, internal=True, parallel_safe=True)

    async def test_same_name_concurrent_calls_keep_their_own_meta(self):
        from app.services.chat.tools.base import ToolMetaSink

        sink = ToolMetaSink()
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "read_file", {"path": "甲.md"}),
                             ("c2", "read_file", {"path": "乙.md"})]), DONE],
            [sse({"content": "两份都读了。"}), DONE],
        ]
        events = await _drive(model="m", api_key="k", user_input="读甲和乙",
                              tools=[self._read_tool(sink)])
        metas = await self._completed_metas(events, sink)
        self.assertEqual([n for n, _ in metas], ["read_file", "read_file"])
        targets = [(m or {}).get("action", {}).get("target") for _, m in metas]
        self.assertEqual(targets, ["甲.md", "乙.md"],
                         f"两张执行卡必须各显示各自的文件名，实际 {targets}")

    async def test_single_call_meta_still_delivered(self):
        """串行单次调用照常拿到 meta（按 call_id 取不到时按名回退，不能把老路径修没了）。"""
        from app.services.chat.tools.base import ToolMetaSink

        sink = ToolMetaSink()
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "read_file", {"path": "唯一.md"})]), DONE],
            [sse({"content": "读完了。"}), DONE],
        ]
        events = await _drive(model="m", api_key="k", user_input="读一份",
                              tools=[self._read_tool(sink)])
        metas = await self._completed_metas(events, sink)
        self.assertEqual([(m or {}).get("action", {}).get("target") for _, m in metas],
                         ["唯一.md"])

    async def test_plain_dict_sink_falls_back_to_name(self):
        """调用方传普通 dict（旧调用点/单测）时行为不变：按名取走。"""
        sink: dict = {}
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "read_file", {"path": "老路径.md"})]), DONE],
            [sse({"content": "读完了。"}), DONE],
        ]

        async def run(args):
            sink["read_file"] = {"action": {"operation": "read",
                                            "target": str(args.get("path") or "")}}
            return "内容"

        events = await _drive(
            model="m", api_key="k", user_input="读一份",
            tools=[MainTool(name="read_file", description="读", parameters={},
                            execute=run, internal=True)])
        metas = await self._completed_metas(events, sink)
        self.assertEqual([(m or {}).get("action", {}).get("target") for _, m in metas],
                         ["老路径.md"])


if __name__ == "__main__":
    unittest.main()
