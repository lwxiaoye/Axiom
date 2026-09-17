# -*- coding: utf-8 -*-
"""历史轨迹里的产物行与失败文案（2026-07-28）。

两条既有缺口，表现都是「刷新一次，同一条消息就变了样」：

1. **产物行的位置与条数**。`get_execution_traces_by_thread` 从不写 `kind:"artifact"` 步骤，
   只把 `meta.files` 汇总进 `trace.files`，于是前端 `restoreExecutionTrace` 里那句
   `!steps.some(s => s.kind === 'artifact')` 恒成立——回放**永远**只在时间线末尾合成一行。
   一轮里 bash 分三次各存一个 pptx：实时看到三行「已生成并保存 1 个文件」穿插在对应步骤后，
   刷新后塌成末尾一行「已生成并保存 3 个文件」。

2. **失败文案的落库字段**。`tool.failed` 的错误被写进 `preview`，实时侧写的是 `error`。
   同一次失败，刷新前行内有一句红字摘要 + 展开面板的「输出」段，刷新后红字没了。

无 DB 依赖：沿用 test_refresh_loss_p0 的假会话工厂模式。
"""
import asyncio
import unittest
from datetime import datetime
from typing import Any, List

from app.services.tasks import task_run_service as trs


class _FakeResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class _FakeSession:
    """按调用顺序回放 execute 结果（runs → events → instructions）。"""

    def __init__(self, results: List[List[Any]]):
        self._results = list(results)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *_a, **_k):
        return _FakeResult(self._results.pop(0) if self._results else [])


def _event(seq: int, etype: str, data: dict, run_id: str = "r1"):
    from app.runtime_models import AgentRunEvent
    ev = AgentRunEvent(run_id=run_id, event_id=f"e{seq}", sequence=seq, type=etype, data=data)
    ev.created_at = datetime(2026, 7, 28, 12, 0, seq % 60)
    return ev


def _run_row(run_id: str = "r1"):
    from app.runtime_models import AgentRun
    r = AgentRun(id=run_id, thread_id="t1", user_id="u1", status="completed", kind="chat")
    r.created_at = datetime(2026, 7, 28, 11, 59, 0)
    r.completed_at = datetime(2026, 7, 28, 12, 1, 0)
    return r


def _trace(events):
    orig = trs.runtime_session
    trs.runtime_session = lambda: (lambda: _FakeSession([[_run_row()], events, []]))  # type: ignore[assignment]
    try:
        return asyncio.run(trs.get_execution_traces_by_thread("t1"))[42]
    finally:
        trs.runtime_session = orig  # type: ignore[assignment]


def _deck(file_id: str, filename: str, **over):
    row = {"id": file_id, "filename": filename, "mime": "", "size": 1, "deliverable": True}
    row.update(over)
    return row


DONE = ("message.completed", {"text": "好了", "message_id": 42})


class ArtifactRowPositionTests(unittest.TestCase):
    def test_three_saves_produce_three_artifact_rows_in_place(self):
        events = []
        seq = 0
        for i, name in enumerate(["一.pptx", "二.pptx", "三.pptx"]):
            seq += 1
            events.append(_event(seq, "tool.started", {"name": "bash", "args": {"command": f"python3 b{i}.py"}}))
            seq += 1
            events.append(_event(seq, "tool.completed", {
                "name": "bash", "meta": {"files": [_deck(f"f{i}", name)]},
            }))
        events.append(_event(seq + 1, *DONE))
        steps = _trace(events)["steps"]
        self.assertEqual(
            [s["kind"] for s in steps],
            ["tool", "artifact", "tool", "artifact", "tool", "artifact"],
        )
        self.assertEqual(
            [s["label"] for s in steps if s["kind"] == "artifact"],
            ["已生成并保存 1 个文件"] * 3,
        )

    def test_two_files_in_one_step_stay_one_row(self):
        steps = _trace([
            _event(1, "tool.started", {"name": "bash", "args": {}}),
            _event(2, "tool.completed", {
                "name": "bash", "meta": {"files": [_deck("a", "甲.pptx"), _deck("b", "乙.docx")]},
            }),
            _event(3, *DONE),
        ])["steps"]
        artifacts = [s for s in steps if s["kind"] == "artifact"]
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["label"], "已生成并保存 2 个文件")

    def test_process_files_do_not_produce_a_row(self):
        # build.py 照常落库供下一步用，但时间线上不该报「已生成并保存」
        steps = _trace([
            _event(1, "tool.started", {"name": "write_file", "args": {"path": "build.py"}}),
            _event(2, "tool.completed", {
                "name": "write_file",
                "meta": {"files": [{"id": "s1", "filename": "build.py", "deliverable": False}]},
            }),
            _event(3, *DONE),
        ])["steps"]
        self.assertEqual([s["kind"] for s in steps], ["tool"])

    def test_material_source_is_not_an_artifact_even_with_deliverable_ext(self):
        # download_url 取回的 .pdf：后缀在白名单里，但 source=material 是输入不是产出。
        # 老事件没有 deliverable 字段时按 文件名+来源 现算（_is_deliverable_row 的兜底路径）
        steps = _trace([
            _event(1, "tool.started", {"name": "download_url", "args": {"url": "https://x/y.pdf"}}),
            _event(2, "tool.completed", {
                "name": "download_url",
                "meta": {"files": [{"id": "m1", "filename": "报告.pdf", "source": "material"}]},
            }),
            _event(3, *DONE),
        ])["steps"]
        self.assertEqual([s["kind"] for s in steps], ["tool"])

    def test_same_file_id_counted_once(self):
        steps = _trace([
            _event(1, "tool.started", {"name": "write_file", "args": {}}),
            _event(2, "tool.completed", {"name": "write_file", "meta": {"files": [_deck("same", "稿.md")]}}),
            _event(3, "tool.started", {"name": "bash", "args": {}}),
            _event(4, "tool.completed", {"name": "bash", "meta": {"files": [_deck("same", "稿.md")]}}),
            _event(5, *DONE),
        ])["steps"]
        self.assertEqual(len([s for s in steps if s["kind"] == "artifact"]), 1)
        # trace.files 同样只留一份，前端 generatedFiles 与实时侧口径一致
        self.assertEqual(len(_trace([
            _event(1, "tool.started", {"name": "write_file", "args": {}}),
            _event(2, "tool.completed", {"name": "write_file", "meta": {"files": [_deck("same", "稿.md")]}}),
            _event(3, "tool.started", {"name": "bash", "args": {}}),
            _event(4, "tool.completed", {"name": "bash", "meta": {"files": [_deck("same", "稿.md")]}}),
            _event(5, *DONE),
        ])["files"]), 1)

    def test_verification_row_comes_before_artifact_row(self):
        steps = _trace([
            _event(1, "tool.started", {"name": "bash", "args": {}}),
            _event(2, "tool.completed", {
                "name": "bash",
                "meta": {"files": [_deck("r1", "汇报.pptx")], "review_status": "passed"},
            }),
            _event(3, *DONE),
        ])["steps"]
        self.assertEqual([s["kind"] for s in steps], ["tool", "verification", "artifact"])

    def test_review_failed_files_render_as_draft(self):
        steps = _trace([
            _event(1, "tool.started", {"name": "bash", "args": {}}),
            _event(2, "tool.completed", {
                "name": "bash",
                "meta": {"files": [_deck("d1", "汇报.pptx", review={"status": "failed"})]},
            }),
            _event(3, *DONE),
        ])["steps"]
        artifact = [s for s in steps if s["kind"] == "artifact"][0]
        self.assertEqual(artifact["status"], "failed")
        self.assertIn("已保存为草稿", artifact["label"])

    def test_plan_key_follows_the_step_it_happened_under(self):
        steps = _trace([
            _event(1, "plan.updated", {
                "goal_revision": 0, "plan_version": 1,
                "steps": [{"key": "k1", "title": "制作", "status": "in_progress"}],
            }),
            _event(2, "tool.started", {"name": "bash", "args": {}}),
            _event(3, "tool.completed", {"name": "bash", "meta": {"files": [_deck("p1", "汇报.pptx")]}}),
            _event(4, *DONE),
        ])["steps"]
        artifact = [s for s in steps if s["kind"] == "artifact"][0]
        self.assertEqual(artifact["planKey"], "k1")


class FailureTextFieldTests(unittest.TestCase):
    def test_tool_failed_writes_error_not_preview(self):
        steps = _trace([
            _event(1, "tool.started", {"name": "bash", "args": {"command": "python3 build.py"}}),
            _event(2, "tool.failed", {"name": "bash", "error": "ModuleNotFoundError: pptx"}),
            _event(3, *DONE),
        ])["steps"]
        self.assertEqual(steps[0]["error"], "ModuleNotFoundError: pptx")
        self.assertNotIn("preview", steps[0])

    def test_tool_completed_still_writes_preview(self):
        steps = _trace([
            _event(1, "tool.started", {"name": "bash", "args": {}}),
            _event(2, "tool.completed", {"name": "bash", "result_preview": "exit_code=0"}),
            _event(3, *DONE),
        ])["steps"]
        self.assertEqual(steps[0]["preview"], "exit_code=0")
        self.assertNotIn("error", steps[0])

    def test_failed_without_error_field_falls_back_to_result_preview(self):
        steps = _trace([
            _event(1, "tool.started", {"name": "bash", "args": {}}),
            _event(2, "tool.failed", {"name": "bash", "result_preview": "（沙箱执行失败：超时）"}),
            _event(3, *DONE),
        ])["steps"]
        self.assertEqual(steps[0]["error"], "（沙箱执行失败：超时）")


if __name__ == "__main__":
    unittest.main()
