"""客观产物检查作为同一目标的 observation，不成为 Harness 的固定返工轮次。"""
import json
import unittest
from unittest.mock import patch

from app.services.chat.tools.base import _with_validity_gate
from app.services.agent_harness import model_driver
from app.services.agent_harness.model_driver import MainTool
from app.services.chat.main_tool_turn import map_tool_loop_events
from app.services.sse_protocol import HARNESS, SSEChannel


def _sse(delta: dict) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


def _tool(call_id: str = "c1") -> str:
    return _sse({
        "tool_calls": [{
            "index": 0, "id": call_id, "type": "function",
            "function": {"name": "bash", "arguments": "{}"},
        }],
    })


DONE = "data: [DONE]"


class _Stream:
    status_code = 200

    def __init__(self, lines):
        self.lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class _Client:
    responses: list = []
    last_response: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        # 脚本耗尽时**重放最后一条**（2026-07-27）：主循环新增「交付前自查」网，动过手的
        # 回合收尾前多一轮 LLM 往返。重放让这一轮对既有断言透明——用例验的是行为，
        # 不是"恰好几次往返"。
        if not self.responses:
            return _Stream(getattr(type(self), "last_response", None) or [])
        type(self).last_response = self.responses.pop(0)
        return _Stream(type(self).last_response)


class ArtifactQualityGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_review_is_observation_not_forced_tool_rerun(self):
        calls = 0

        async def run(_args):
            nonlocal calls
            calls += 1
            if calls == 1:
                return _with_validity_gate(
                    "独立质量审查未通过：封面层级混乱；返工：重做标题层级", "failed")
            return _with_validity_gate("独立质量审查通过（综合 94）", "passed")

        _Client.responses = [
            [_tool("c1"), DONE],
            [_sse({"content": "文件已经做好了。"}), DONE],
        ]
        events = []
        with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", _Client):
            async for event in model_driver.drive_model(
                model="m", api_key="k", user_input="做一份好看的报告",
                tools=[MainTool(
                    name="bash", description="test tool", parameters={}, execute=run,
                    effect_scope="user_files", idempotent=False,
                    semantic_tags=("artifact_producer",),
                )],
            ):
                events.append(event)

        results = [e for e in events if e["type"] == "tool_result"]
        # The failed objective check is returned as a same-goal observation.  The model, not a
        # fixed quality budget, decides whether another tool call is useful.
        self.assertEqual(calls, 1)
        self.assertEqual([e["status"] for e in results], ["completed"])
        self.assertTrue(results[0].get("quality_retry"))
        self.assertEqual(results[0]["preview"], "已完成初稿，正在完善产物细节")
        self.assertNotIn("封面层级混乱", json.dumps(events, ensure_ascii=False))
        self.assertNotIn("artifact_quality_gate", json.dumps(events, ensure_ascii=False))
        self.assertTrue(any(e["type"] == "commentary" for e in events))
        final = next(e for e in events if e["type"] == "final")
        self.assertTrue(final["answer"])

    async def test_quality_retry_sse_hides_review_and_failed_files(self):
        async def source():
            yield {
                "type": "tool_result", "name": "bash", "status": "completed",
                "quality_retry": True, "preview": "已完成初稿，正在完善产物细节",
            }

        meta = {
            "bash": {
                "review_status": "failed",
                "files": [{"id": "hidden-draft", "review": {"summary": "封面很乱"}}],
            },
        }
        frames = []
        async for frame in map_tool_loop_events(
            SSEChannel(HARNESS, "t1", "r1"), source(), {}, {}, meta,
        ):
            frames.append(frame)
        payload = "".join(frames)
        self.assertIn("已完成初稿，正在完善产物细节", payload)
        self.assertNotIn("review_status", payload)
        self.assertNotIn("hidden-draft", payload)
        self.assertNotIn("封面很乱", payload)
        self.assertNotIn("bash", meta)


if __name__ == "__main__":
    unittest.main()
