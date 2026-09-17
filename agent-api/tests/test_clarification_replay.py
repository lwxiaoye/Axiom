"""clarification 事件回放原轮上下文（三轮评审 P2a）。

守护：channel.clarification 把 skill_ids + 附件文本 refs 带进 Harness 事件，供前端重订阅时
重建原轮一次性上下文；无上下文时不塞空字段。
"""
import json
import unittest

from app.services import sse_protocol


def _payload_data(raw: str) -> dict:
    """从 SSE 文本取出 Harness 事件 data。"""
    line = next(ln for ln in raw.splitlines() if ln.startswith("data:"))
    obj = json.loads(line[len("data:"):].strip())
    return obj["data"]


class ClarificationReplayTests(unittest.TestCase):
    def test_v1_carries_skill_ids_and_attachments(self):
        ch = sse_protocol.SSEChannel(sse_protocol.HARNESS, "t1", "r1")
        raw = ch.clarification(
            [{"id": "a", "name": "A"}], "选一个",
            skill_ids=["s1", "s2"],
            attachments=[{"filename": "报告.docx", "kind": "file", "text": "正文"}],
        )
        data = _payload_data(raw)
        self.assertEqual(data["skill_ids"], ["s1", "s2"])
        self.assertEqual(data["attachments"][0]["filename"], "报告.docx")
        self.assertEqual(data["attachments"][0]["text"], "正文")

    def test_no_context_omits_fields(self):
        ch = sse_protocol.SSEChannel(sse_protocol.HARNESS, "t1", "r1")
        data = _payload_data(ch.clarification([{"id": "a", "name": "A"}], "选一个"))
        self.assertNotIn("skill_ids", data)
        self.assertNotIn("attachments", data)


if __name__ == "__main__":
    unittest.main()
