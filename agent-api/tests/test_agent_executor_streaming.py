import json
import unittest
import pytest
from unittest.mock import patch

from app.services.agents.agent_executor import ToolSpec, run_function_call_loop
from app.services.agents.agent_service import agent_service


@pytest.fixture(autouse=True)
def chat_catalog(monkeypatch):
    monkeypatch.setattr(agent_service, "model_responses_capability", lambda *_: False)


class FakeCtx:
    def __init__(self):
        self.variables = {}
        self.llm_api_key = "test-key"
        self.chunks = []

    async def stream_output(self, seq: int, text: str):
        self.chunks.append((seq, text))


class FakeEngine:
    def __init__(self):
        self.ctx = FakeCtx()


class FakeStreamResponse:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_lines(self):
        tool_round = any('"tool_calls"' in line for line in self._lines)
        for line in self._lines:
            if line == "data: [DONE]":
                yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls" if tool_round else "stop"}]})
            yield line


class FakeAsyncClient:
    responses = []

    def __init__(self, *args, **kwargs):
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method, url, json=None, headers=None):
        self.requests.append({"method": method, "url": url, "json": json, "headers": headers})
        return FakeStreamResponse(self.responses.pop(0))


def sse_delta(delta: dict) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


class AgentExecutorStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_agent_content_deltas(self):
        FakeAsyncClient.responses = [
            [
                sse_delta({"content": "你好"}),
                sse_delta({"content": "，世界"}),
                "data: [DONE]",
            ]
        ]
        engine = FakeEngine()

        with patch("app.services.agents.agent_executor.httpx.AsyncClient", FakeAsyncClient):
            answer, traces = await run_function_call_loop(
                engine,
                model="qwen-test",
                temperature=None,
                system_prompt="",
                max_histories=0,
                user_input="hi",
                tools=[],
                output_seq=7,
            )

        self.assertEqual(answer, "你好，世界")
        self.assertEqual(traces, [])
        self.assertEqual(engine.ctx.chunks, [(7, "你好"), (7, "，世界")])

    async def test_streams_tool_call_chunks_then_final_content(self):
        async def execute_tool(args):
            return f"weather:{args['city']}"

        tool = ToolSpec(
            name="get_weather",
            description="Get weather",
            parameters={"type": "object", "properties": {"city": {"type": "string"}}},
            execute=execute_tool,
        )
        FakeAsyncClient.responses = [
            [
                sse_delta(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "get_", "arguments": "{\"city\":\"杭"},
                            }
                        ]
                    }
                ),
                sse_delta(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"name": "weather", "arguments": "州\"}"},
                            }
                        ]
                    }
                ),
                "data: [DONE]",
            ],
            [
                sse_delta({"content": "杭州天气很好"}),
                "data: [DONE]",
            ],
        ]
        engine = FakeEngine()

        with patch("app.services.agents.agent_executor.httpx.AsyncClient", FakeAsyncClient):
            answer, traces = await run_function_call_loop(
                engine,
                model="qwen-test",
                temperature=None,
                system_prompt="",
                max_histories=0,
                user_input="weather",
                tools=[tool],
                output_seq=9,
            )

        self.assertEqual(answer, "杭州天气很好")
        self.assertEqual(traces, [{"name": "get_weather", "args": {"city": "杭州"}, "result": "weather:杭州"}])
        self.assertEqual(engine.ctx.chunks, [(9, "杭州天气很好")])


def sse_tool_call(name: str) -> str:
    return sse_delta(
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_x",
                    "type": "function",
                    "function": {"name": name, "arguments": "{}"},
                }
            ]
        }
    )


class AgentExecutorUnknownToolTests(unittest.IsolatedAsyncioTestCase):
    """未知工具回执必须教路（对齐 model_driver._run_one_tool 2026-07-27）：
    只回名字，模型会连调不存在的工具烧光轮次。"""

    @staticmethod
    def _tool(name: str) -> ToolSpec:
        async def execute(args):
            return "ok"

        return ToolSpec(name=name, description=name, parameters={"type": "object"}, execute=execute)

    async def test_unknown_tool_reply_lists_available_tools(self):
        FakeAsyncClient.responses = [
            [sse_tool_call("write_file"), "data: [DONE]"],
            [sse_delta({"content": "收到"}), "data: [DONE]"],
        ]
        engine = FakeEngine()

        with patch("app.services.agents.agent_executor.httpx.AsyncClient", FakeAsyncClient):
            _, traces = await run_function_call_loop(
                engine,
                model="qwen-test",
                temperature=None,
                system_prompt="",
                max_histories=0,
                user_input="hi",
                tools=[self._tool("bash"), self._tool("edit_file")],
            )

        result = traces[0]["result"]
        self.assertIn("未知工具 write_file", result)
        # 清单按字典序稳定输出，便于模型逐一核对
        self.assertIn("本轮可用的工具: bash, edit_file", result)
        self.assertIn("不要再调用不存在的工具", result)

    async def test_unknown_tool_reply_without_tools_still_names_the_tool(self):
        """本轮没挂任何工具（模型幻觉出调用）时不编造清单，但至少点名。"""
        FakeAsyncClient.responses = [
            [sse_tool_call("write_file"), "data: [DONE]"],
            [sse_delta({"content": "收到"}), "data: [DONE]"],
        ]
        engine = FakeEngine()

        with patch("app.services.agents.agent_executor.httpx.AsyncClient", FakeAsyncClient):
            _, traces = await run_function_call_loop(
                engine,
                model="qwen-test",
                temperature=None,
                system_prompt="",
                max_histories=0,
                user_input="hi",
                tools=[],
            )

        result = traces[0]["result"]
        self.assertIn("未知工具 write_file", result)
        self.assertIn("read_execution_result", result)
