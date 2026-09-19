"""自主完成安全网（drive_model auto-continue）回归。

场景：模型自愿停手（本轮无工具调用）但 update_plan 里还有未完成步骤时，不接受它把
「你可以选 1/2/3」的甩锅清单当最终答案，注入「继续」推它做完；双重上限防空转。
用假 httpx 流驱动 drive_model（仿 test_agent_executor_streaming 的 mock 方式）。
"""
import json
import unittest
from unittest.mock import patch

from app.services.agent_harness import model_driver
from app.services.chat.tools.base import MainTool


def sse(delta: dict) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


def sse_update_plan(steps: list) -> str:
    return sse({"tool_calls": [{
        "index": 0, "id": "c1", "type": "function",
        "function": {"name": "update_plan", "arguments": json.dumps({"steps": steps})},
    }]})


def sse_answer_with_update_plan(text: str, steps: list) -> str:
    return sse({
        "content": text,
        "tool_calls": [{
            "index": 0, "id": "c1", "type": "function",
            "function": {"name": "update_plan", "arguments": json.dumps({"steps": steps})},
        }],
    })


def sse_plan_and_search(steps: list) -> str:
    return sse({"tool_calls": [
        {
            "index": 0, "id": "p1", "type": "function",
            "function": {
                "name": "update_plan",
                "arguments": json.dumps({"steps": steps}),
            },
        },
        {
            "index": 1, "id": "s1", "type": "function",
            "function": {
                "name": "search_web",
                "arguments": json.dumps({"query": "科学减重"}),
            },
        },
    ]})


def sse_tool_calls(calls: list) -> str:
    return sse({"tool_calls": [
        {
            "index": index,
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }
        for index, (call_id, name, args) in enumerate(calls)
    ]})


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


def _search_tool() -> MainTool:
    async def run(_args):
        return "检索结果：合理热量缺口、均衡饮食和运动共同决定长期减重效果。"

    return MainTool(
        name="search_web",
        description="search",
        parameters={},
        execute=run,
        internal=True,
    )


def _update_plan_tool() -> MainTool:
    async def noop(_args):
        return "ok"

    return MainTool(
        name="update_plan",
        description="plan",
        parameters={},
        execute=noop,
        internal=True,
        control_command=True,
    )


class StreamToolLoopPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_actions_continue_without_status_only_plan_updates(self):
        """计划已存在时，下一真实动作与终答都不被 update_plan 卡住。"""
        executed = []

        def action_tool(name: str) -> MainTool:
            async def run(_args):
                executed.append(name)
                return f"{name}-ok"

            return MainTool(name=name, description=name, parameters={}, execute=run)

        async def noop(_args):
            return "ok"

        update_plan = MainTool(
            name="update_plan",
            description="plan",
            parameters={},
            execute=noop,
            internal=True,
            control_command=True,
        )
        initial = [
            {"title": "读取 A", "status": "in_progress"},
            {"title": "读取 B", "status": "pending"},
        ]
        FakeAsyncClient.responses = [
            [sse_tool_calls([
                ("p1", "update_plan", {"steps": initial}),
                ("a1", "read_a", {}),
            ]), DONE],
            # 没有状态型 update_plan：read_b 仍应立即执行。
            [sse_tool_calls([("b1", "read_b", {})]), DONE],
            [sse({"content": "两步都已执行。"}), DONE],
        ]

        events = await _drive(
            model="m",
            api_key="k",
            user_input="分两步读取",
            tools=[update_plan, action_tool("read_a"), action_tool("read_b")],
        )

        self.assertEqual(executed, ["read_a", "read_b"])
        plans = [event["steps"] for event in events if event["type"] == "task_plan"]
        self.assertEqual(len(plans), 1)
        self.assertEqual(
            [[step["status"] for step in plan] for plan in plans],
            [["running", "pending"]],
        )
        public_tools = [
            event["name"] for event in events if event["type"] == "tool_started"
        ]
        self.assertEqual(public_tools, ["read_a", "read_b"])
        final = next(event for event in events if event["type"] == "final")
        self.assertIn("两步都已执行", final["answer"])

    async def test_substantive_answer_with_completed_plan_is_not_summarized_again(self):
        """正文和最终 update_plan 同轮到达时，正文直接成为最终答案。"""
        report = "第一份研究结论：" + ("基于已核验资料的详细结论。" * 40)
        steps = [
            {"title": "检索资料", "status": "completed"},
            {"title": "整理结论", "status": "completed"},
        ]
        FakeAsyncClient.responses = [
            [sse_answer_with_update_plan(report, steps), DONE],
            [sse({"content": "不应再次总结"}), DONE],
        ]
        # update_plan 必须是本轮注册的工具：快速收尾只认 tool_map 里的 update_plan，
        # 未注册的同名调用按未知工具纠错回灌、再采样一轮（那就不是本用例要钉的路径）。
        events = await _drive(
            model="m", api_key="k", user_input="深度研究", tools=[_update_plan_tool()],
        )
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(final["answer"], report)
        self.assertEqual(len(FakeAsyncClient.responses), 1)

    async def test_no_plan_voluntary_stop_is_respected(self):
        """简单问答（无 update_plan 计划）自愿收尾，不被强行续接。"""
        FakeAsyncClient.responses = [
            [sse({"content": "你好，这是简单回答。"}), DONE],
            [sse({"content": "不应该被调用到"}), DONE],
        ]
        events = await _drive(model="m", api_key="k", user_input="hi", tools=[])
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(final["answer"], "你好，这是简单回答。")
        # 只消费了第一条响应，第二条原封不动 → 没有多余续接
        self.assertEqual(len(FakeAsyncClient.responses), 1)

if __name__ == "__main__":
    unittest.main()
