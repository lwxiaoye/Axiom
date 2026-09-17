"""text_protocol_guard 单测：deepseek 系文本工具协议标记的流式/非流式清洗。

覆盖：完整标记单帧命中、跨帧切碎（含逐字符）、全角变体、假前缀不误杀不吞字、
标记前正文保留/标记后丢弃、scrub_text 与流式喂入等价、agent_executor 流式接入。
"""

import json
import unittest
import pytest
from unittest.mock import patch
from app.services.agent_harness.responses_protocol import ResponsesTerminalError

from app.services.platform.text_protocol_guard import (
    PROTOCOL_MARKERS,
    StreamingProtocolScrubber,
    find_first_marker,
    scrub_text,
)


def _feed_all(chunks):
    """按帧喂入，返回 (拼接后的下发文本, scrubber)。"""
    scrubber = StreamingProtocolScrubber()
    out = "".join(scrubber.feed(chunk) for chunk in chunks)
    out += scrubber.flush()
    return out, scrubber


class MarkerTableTests(unittest.TestCase):
    def test_marker_families_present(self):
        # DSML（半角/全角）、deepseek special tokens（含 ▁ 变体）、通用 XML 风格
        for marker in (
            "<|DSML|", "<｜DSML｜",
            "<|tool_calls_begin|>", "<|tool_call_begin|>", "<|tool_calls_end|>", "<|tool_sep|>",
            "<|tool_calls|>",
            "<｜tool▁calls▁begin｜>", "<｜tool▁sep｜>",
            "<tool_call>", "</tool_call>",
        ):
            self.assertIn(marker, PROTOCOL_MARKERS, marker)

    def test_find_first_marker_earliest(self):
        text = "前文<tool_call>中段<|DSML|invoke>"
        self.assertEqual(find_first_marker(text), (2, "<tool_call>"))
        self.assertIsNone(find_first_marker("普通文本 1 < 2 且 a <| b"))


class SingleFrameTests(unittest.TestCase):
    def test_full_marker_single_frame(self):
        scrubber = StreamingProtocolScrubber()
        out = scrubber.feed('答案是42。<|tool_calls_begin|>{"name":"x"}')
        self.assertEqual(out, "答案是42。")
        self.assertTrue(scrubber.leaked)
        self.assertEqual(scrubber.marker, "<|tool_calls_begin|>")
        self.assertEqual(scrubber.flush(), "")

    def test_dsml_prefix_marker(self):
        out, scrubber = _feed_all(['好的<|DSML|tool_calls><invoke name="foo">'])
        self.assertEqual(out, "好的")
        self.assertTrue(scrubber.leaked)
        self.assertEqual(scrubber.marker, "<|DSML|")

    def test_content_after_marker_dropped_across_frames(self):
        scrubber = StreamingProtocolScrubber()
        self.assertEqual(scrubber.feed("正文<tool_call>"), "正文")
        # 命中后所有后续帧一律丢弃
        self.assertEqual(scrubber.feed('{"name": "get_weather"}'), "")
        self.assertEqual(scrubber.feed("</tool_call>再多正文也不放行"), "")
        self.assertEqual(scrubber.flush(), "")
        self.assertTrue(scrubber.leaked)
        self.assertIn("<tool_call>", scrubber.dropped_sample)


class CrossFrameTests(unittest.TestCase):
    def test_split_two_frames(self):
        # 截图实况：标记被分帧切成 <|DS + ML|tool_calls>
        out, scrubber = _feed_all(["<|DS", "ML|tool_calls>"])
        self.assertEqual(out, "")
        self.assertTrue(scrubber.leaked)
        self.assertEqual(scrubber.marker, "<|DSML|")

    def test_split_char_by_char(self):
        text = '结论：OK<|tool_call_begin|>{"a":1}'
        out, scrubber = _feed_all(list(text))
        self.assertEqual(out, "结论：OK")
        self.assertTrue(scrubber.leaked)
        self.assertEqual(scrubber.marker, "<|tool_call_begin|>")

    def test_fullwidth_underline_variant_split(self):
        out, scrubber = _feed_all(["前文<｜tool▁calls", "▁begin｜>function<｜tool▁sep｜>get_weather"])
        self.assertEqual(out, "前文")
        self.assertTrue(scrubber.leaked)
        self.assertEqual(scrubber.marker, "<｜tool▁calls▁begin｜>")

    def test_fullwidth_dsml_char_by_char(self):
        out, scrubber = _feed_all(list("答：<｜DSML｜invoke>"))
        self.assertEqual(out, "答：")
        self.assertTrue(scrubber.leaked)
        self.assertEqual(scrubber.marker, "<｜DSML｜")


class FalsePositiveTests(unittest.TestCase):
    def test_plain_text_passes_through_immediately(self):
        scrubber = StreamingProtocolScrubber()
        # 无可疑尾部时整帧立即放行，不缓冲不卡顿
        self.assertEqual(scrubber.feed("这是普通正文。"), "这是普通正文。")
        self.assertEqual(scrubber.flush(), "")
        self.assertFalse(scrubber.leaked)

    def test_lt_and_pipe_fake_prefix_not_killed(self):
        for text in (
            "数学上 1 < 2 而且 3 <| 4",
            "行内代码 `a <tool_x>` 不是协议",
            "结尾悬着一个 <",
            "结尾悬着 <|",
            "结尾悬着 <|tool_call",  # 像前缀但流已结束
            "<tool_cat 开头假前缀",
        ):
            out, scrubber = _feed_all([text])
            self.assertEqual(out, text, text)
            self.assertFalse(scrubber.leaked, text)

    def test_fake_prefix_across_frames_released(self):
        # <tool_ 先被缓冲（可能是 <tool_call>），下一帧证明不是 => 完整放行
        out, scrubber = _feed_all(["温度 <tool_", "box> 内框"])
        self.assertEqual(out, "温度 <tool_box> 内框")
        self.assertFalse(scrubber.leaked)

    def test_buffer_bounded_and_nonprefix_flows(self):
        scrubber = StreamingProtocolScrubber()
        released = scrubber.feed("x" * 5000 + "<|")
        # 大段正文立即放行，只留 "<|" 这种可疑尾部
        self.assertEqual(released, "x" * 5000)
        self.assertEqual(scrubber.flush(), "<|")


class ScrubTextTests(unittest.TestCase):
    CASES = [
        "纯正文，没有任何标记 1 < 2 <| ok",
        '前文<|tool_calls_begin|>{"x":1}<|tool_calls_end|>',
        "<|DSML|tool_calls>全部内部协议",
        "前<｜tool▁calls▁begin｜>后",
        "尾部悬挂假前缀 <|tool",
        "带通用标签<tool_call>{}</tool_call>尾巴",
        "",
    ]

    def test_scrub_text_basic(self):
        safe, leaked = scrub_text('前文<|tool_calls_begin|>{"x":1}')
        self.assertEqual(safe, "前文")
        self.assertTrue(leaked)
        safe, leaked = scrub_text("干净正文")
        self.assertEqual(safe, "干净正文")
        self.assertFalse(leaked)

    def test_streaming_equivalent_to_scrub_text(self):
        # 任意切分（含逐字符）与一次性 scrub_text 结果一致
        for text in self.CASES:
            expect_safe, expect_leaked = scrub_text(text)
            splits = [[text], list(text)]
            for width in (2, 3, 7):
                splits.append([text[i:i + width] for i in range(0, len(text), width)])
            for chunks in splits:
                out, scrubber = _feed_all(chunks)
                self.assertEqual(out, expect_safe, f"{text!r} 分帧 {len(chunks)}")
                self.assertEqual(scrubber.leaked, expect_leaked, f"{text!r} 分帧 {len(chunks)}")


# ---------------------------------------------------------------------------
# agent_executor 流式接入（工作流 Agent 节点）
# ---------------------------------------------------------------------------

from app.services.agents.agent_executor import run_function_call_loop  # noqa: E402


class _FakeCtx:
    def __init__(self):
        self.variables = {}
        self.llm_api_key = "test-key"
        self.chunks = []

    async def stream_output(self, seq: int, text: str):
        self.chunks.append((seq, text))


class _FakeEngine:
    def __init__(self):
        self.ctx = _FakeCtx()


class _FakeStreamResponse:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    responses = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method, url, json=None, headers=None):
        return _FakeStreamResponse(self.responses.pop(0))


def _sse(delta: dict) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": "stop"}]}, ensure_ascii=False)


class AgentExecutorScrubTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("app.services.agent_harness.function_round.model_uses_responses_transport", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_streamed_protocol_leak_truncated(self):
        _FakeAsyncClient.responses = [
            [
                _sse({"content": "已完成"}),
                _sse({"content": "分析。<|DS"}),          # 标记跨帧切碎
                _sse({"content": "ML|tool_calls><invoke>"}),
                _sse({"content": '{"query":"内部参数"}'}),
                "data: [DONE]",
            ]
        ]
        engine = _FakeEngine()
        with patch("app.services.agents.agent_executor.httpx.AsyncClient", _FakeAsyncClient):
            with pytest.raises(ResponsesTerminalError):
                await run_function_call_loop(
                    engine, model="deepseek-test", temperature=None, system_prompt="",
                    max_histories=0, user_input="hi", tools=[], output_seq=3,
                )
        self.assertEqual(engine.ctx.chunks, [])
        self.assertNotIn("DSML", "".join(t for _, t in engine.ctx.chunks))

    async def test_streamed_fake_prefix_fully_released(self):
        _FakeAsyncClient.responses = [
            [
                _sse({"content": "结果 1 <"}),
                _sse({"content": "| 2 属于假前缀"}),
                "data: [DONE]",
            ]
        ]
        engine = _FakeEngine()
        with patch("app.services.agents.agent_executor.httpx.AsyncClient", _FakeAsyncClient):
            answer, _ = await run_function_call_loop(
                engine, model="deepseek-test", temperature=None, system_prompt="",
                max_histories=0, user_input="hi", tools=[], output_seq=1,
            )
        self.assertEqual(answer, "结果 1 <| 2 属于假前缀")
        self.assertEqual("".join(t for _, t in engine.ctx.chunks), "结果 1 <| 2 属于假前缀")


if __name__ == "__main__":
    unittest.main()
