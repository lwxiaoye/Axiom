"""输出截断防误执行（对齐 Claude「先查 stop_reason 再读 content」，2026-07-27）。

修复前的真实故障链：模型输出长 → 渠道按输出上限从中间砍断 → finish_reason=length
（我们完全不读）→ tool_calls.arguments 是残缺 JSON → json.JSONDecodeError 被静默
兜底成 {} → **拿空参数真的去执行工具**（空命令的 bash / 空内容的 write_file）
→ 工具报出模型看不懂的错 → 重试 → 撞错误指纹止损 → 预算耗光。对用户的表现就是
「任务毫无征兆地跑飞」。

本测试锁住四条契约（统一母题：宁可如实失败，也不假装完成）：
1. finish_reason=length 那一轮，**任何工具都不执行**，改注入回执让模型拆小重来；
2. 连续截断超过 LoopState.TRUNCATED_MAX 即如实抛错；
3. 参数不是合法 JSON 时不执行，只向模型回填纠正 observation，
   不制造用户可见假工具失败；
4. 空回答（含渠道内容策略拦截）不得按成功收尾——用户不该看到「已完成」+ 一片空白。

mock 方式仿 test_tool_loop_serial_baseline（假 httpx 流驱动 drive_model）。
"""
import json
import unittest
from unittest.mock import patch

from app.services.agent_harness import model_driver
from app.services.agent_harness.model_driver import LoopState, MainTool
from tests.message_protocol import assert_all_requests_valid


def sse(delta: dict, finish_reason=None) -> str:
    choice = {"delta": delta}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return "data: " + json.dumps({"choices": [choice]}, ensure_ascii=False)


def sse_tool_calls(calls: list, finish_reason=None) -> str:
    """calls: [(id, name, arguments_raw_string)] —— arguments 传原始串，便于构造残缺 JSON。"""
    frags = [
        {"index": i, "id": call_id, "type": "function",
         "function": {"name": name, "arguments": raw}}
        for i, (call_id, name, raw) in enumerate(calls)
    ]
    return sse({"tool_calls": frags}, finish_reason=finish_reason)


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
    last_response: list = []
    requests: list = []

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
        FakeAsyncClient.requests.append(json or {})
        if not FakeAsyncClient.responses:
            return FakeStreamResponse(getattr(FakeAsyncClient, "last_response", None) or [])
        FakeAsyncClient.last_response = FakeAsyncClient.responses.pop(0)
        return FakeStreamResponse(FakeAsyncClient.last_response)


async def _drive(**kwargs):
    events = []
    FakeAsyncClient.requests = []
    with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", FakeAsyncClient):
        async for ev in model_driver.drive_model(**kwargs):
            events.append(ev)
    # 截断回执也是一个「往 messages 里插系统消息」的注入点，同样只能落在
    # 上一轮 tool 结果全部回填之后——顺带校验，别再靠人肉盯位置。
    assert_all_requests_valid(FakeAsyncClient.requests)
    return events


def _recording_tool(calls: list) -> MainTool:
    async def run(args):
        calls.append(args)
        return "工具执行了"
    # 夹具名用现役执行器（2026-07-29）：被测逻辑是 finish_reason="length" 的截断防护，
    # 与工具名无关；但写退休名会让读者以为它还是活工具。
    return MainTool(name="bash", description="跑命令", parameters={}, execute=run)


class TruncatedRoundTests(unittest.IsolatedAsyncioTestCase):
    async def test_truncated_round_executes_nothing_and_recovers(self):
        """截断轮：工具一个都不许执行；注入回执后模型重来一轮即可正常收尾。"""
        executed: list = []
        FakeAsyncClient.responses = [
            # 第 1 轮：参数被砍断（残缺 JSON），末帧 finish_reason=length
            [sse_tool_calls([("c1", "bash", '{"command": "echo ')], finish_reason="length"), DONE],
            # 第 2 轮：模型按回执把动作拆小，直接给出最终回答
            [sse({"content": "我把它拆小后完成了。"}), DONE],
        ]
        events = await _drive(
            model="m", api_key="k", user_input="生成一份长报告",
            tools=[_recording_tool(executed)],
        )
        self.assertEqual(executed, [], "截断轮的工具调用绝不能被执行")
        kinds = [e["type"] for e in events]
        self.assertNotIn("tool_started", kinds, "截断轮不该冒出任何工具事件")
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(final["answer"], "我把它拆小后完成了。")

    async def test_truncated_round_keeps_partial_text_as_commentary(self):
        """截断轮已生成的正文不丢：转成过程说明，不当最终答案。"""
        executed: list = []
        FakeAsyncClient.responses = [
            [sse({"content": "我先读一下配置"}),
             sse_tool_calls([("c1", "bash", '{"command": "op')], finish_reason="length"), DONE],
            [sse({"content": "完成。"}), DONE],
        ]
        events = await _drive(
            model="m", api_key="k", user_input="读配置",
            tools=[_recording_tool(executed)],
        )
        self.assertEqual(executed, [])
        commentaries = [e["text"] for e in events if e["type"] == "commentary"]
        self.assertIn("我先读一下配置", commentaries)
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(final["answer"], "完成。", "过程说明不得混进最终答案")

    async def test_consecutive_truncation_fails_honestly(self):
        """连续截断超上限：如实抛错，不假装完成、不无限重试。"""
        executed: list = []
        truncated = [sse_tool_calls(
            [("c1", "bash", '{"command": "x')], finish_reason="length"), DONE]
        FakeAsyncClient.responses = [list(truncated) for _ in range(LoopState.TRUNCATED_MAX + 1)]
        with self.assertRaises(RuntimeError) as ctx:
            await _drive(
                model="m", api_key="k", user_input="生成超长内容",
                tools=[_recording_tool(executed)],
            )
        self.assertIn("截断", str(ctx.exception))
        self.assertEqual(executed, [])

    async def test_successful_round_resets_truncation_counter(self):
        """成功一轮即清零：截断—成功—截断 不该被算作「连续两次」而提前失败。"""
        executed: list = []
        trunc = [sse_tool_calls([("c1", "bash", '{"command": "x')], finish_reason="length"), DONE]
        FakeAsyncClient.responses = [
            list(trunc),
            [sse_tool_calls([("c2", "bash", '{"command": "echo 1"}')]), DONE],
            list(trunc),
            [sse({"content": "最终回答。"}), DONE],
        ]
        events = await _drive(
            model="m", api_key="k", user_input="分两步做",
            tools=[_recording_tool(executed)],
        )
        self.assertEqual(len(executed), 1, "只有那一轮完整的调用该被执行")
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(final["answer"], "最终回答。")


class InvalidToolArgsTests(unittest.IsolatedAsyncioTestCase):
    async def test_unparseable_args_are_not_executed_with_empty_dict(self):
        """参数非法：不执行，纠正回执只进模型上下文。"""
        executed: list = []
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "bash", '{"command": "echo ')]), DONE],
            [sse({"content": "我重发了完整参数。"}), DONE],
        ]
        events = await _drive(
            model="m", api_key="k", user_input="跑个脚本",
            tools=[_recording_tool(executed)],
        )
        self.assertEqual(executed, [], "残缺参数绝不能被当成空参数执行")
        self.assertFalse(any(e["type"] in {"tool_started", "tool_result"} for e in events))
        model_context = json.dumps(FakeAsyncClient.requests[-1], ensure_ascii=False)
        self.assertIn("本次调用未执行", model_context)
        self.assertIn("参数不是合法 JSON", model_context)

    async def test_non_object_args_are_rejected(self):
        """参数是合法 JSON 但不是对象（模型偶发吐数组/字符串）：同样不执行。"""
        executed: list = []
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "bash", '["print(1)"]')]), DONE],
            [sse({"content": "改好了。"}), DONE],
        ]
        events = await _drive(
            model="m", api_key="k", user_input="跑个脚本",
            tools=[_recording_tool(executed)],
        )
        self.assertEqual(executed, [])
        self.assertFalse(any(e["type"] in {"tool_started", "tool_result"} for e in events))
        model_context = json.dumps(FakeAsyncClient.requests[-1], ensure_ascii=False)
        self.assertIn("本次调用未执行", model_context)
        self.assertIn("参数必须是 JSON 对象", model_context)

    async def test_valid_args_still_execute(self):
        """回归：合法参数照常执行，别把好的一起挡了。"""
        executed: list = []
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "bash", '{"command": "echo 1"}')]), DONE],
            [sse({"content": "跑完了。"}), DONE],
        ]
        events = await _drive(
            model="m", api_key="k", user_input="跑个脚本",
            tools=[_recording_tool(executed)],
        )
        self.assertEqual(executed, [{"command": "echo 1"}])
        result = next(e for e in events if e["type"] == "tool_result")
        self.assertEqual(result["status"], "completed")


class EmptyAnswerTests(unittest.IsolatedAsyncioTestCase):
    """空回答=假完成：用户绝不该看到「已完成」但正文一片空白。"""

    async def test_empty_round_fails_instead_of_blank_final(self):
        executed: list = []
        FakeAsyncClient.responses = [[sse({}), DONE]]
        with self.assertRaises(RuntimeError) as ctx:
            await _drive(
                model="m", api_key="k", user_input="你好",
                tools=[_recording_tool(executed)],
            )
        self.assertIn("空回答", str(ctx.exception))

    async def test_content_filter_reports_policy_reason(self):
        executed: list = []
        FakeAsyncClient.responses = [[sse({}, finish_reason="content_filter"), DONE]]
        with self.assertRaises(RuntimeError) as ctx:
            await _drive(
                model="m", api_key="k", user_input="敏感问题",
                tools=[_recording_tool(executed)],
            )
        self.assertIn("内容安全策略", str(ctx.exception))

    async def test_whitespace_only_answer_is_not_a_success(self):
        executed: list = []
        FakeAsyncClient.responses = [[sse({"content": "   \n  "}), DONE]]
        with self.assertRaises(RuntimeError):
            await _drive(
                model="m", api_key="k", user_input="你好",
                tools=[_recording_tool(executed)],
            )


if __name__ == "__main__":
    unittest.main()
