"""Codex 式工具行：tool.completed result_preview 上限 2000（可展开输出面板要有可读片段）。"""
import json
import unittest

from app.services import sse_protocol


class ToolPreviewCapTests(unittest.TestCase):
    def test_completed_preview_capped_at_2000(self):
        ch = sse_protocol.SSEChannel(sse_protocol.HARNESS, "t1", "r1")
        raw = ch.tool_completed("bash", "x" * 5000)
        line = next(ln for ln in raw.splitlines() if ln.startswith("data:"))
        data = json.loads(line[len("data:"):].strip())["data"]
        self.assertEqual(len(data["result_preview"]), 2000)

    def test_failed_preview_and_review_meta_are_preserved(self):
        ch = sse_protocol.SSEChannel(sse_protocol.HARNESS, "t1", "r1")
        raw = ch.tool_failed(
            "bash", "x" * 5000,
            meta={"review_status": "failed", "files": [{"id": "draft-1"}]},
        )
        line = next(ln for ln in raw.splitlines() if ln.startswith("data:"))
        data = json.loads(line[len("data:"):].strip())["data"]
        self.assertEqual(len(data["error"]), 2000)
        self.assertEqual(data["meta"]["review_status"], "failed")
        self.assertEqual(data["meta"]["files"][0]["id"], "draft-1")

    def test_tool_call_identity_and_commentary_evidence_are_preserved(self):
        ch = sse_protocol.SSEChannel(sse_protocol.HARNESS, "t1", "r1")
        started = json.loads(
            ch.tool_started("read_file", {"path": "a"}, "call-1")
            .removeprefix("data: ")
        )["data"]
        completed = json.loads(
            ch.tool_completed("read_file", "ok", call_id="call-1")
            .removeprefix("data: ")
        )["data"]
        commentary = json.loads(
            ch.message_commentary(
                "文件结构已核对。",
                evidence_item_ids=["call-1", "call-1"],
                next_action="检查调用链",
            ).removeprefix("data: ")
        )["data"]

        self.assertEqual(started["call_id"], "call-1")
        self.assertEqual(completed["call_id"], "call-1")
        self.assertEqual(commentary["evidence_item_ids"], ["call-1"])
        self.assertEqual(commentary["next_action"], "检查调用链")


if __name__ == "__main__":
    unittest.main()
