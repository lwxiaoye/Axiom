"""P0「刷新后执行过程消失」与私有推理隐藏回归。

覆盖四条新链路：
1. map_tool_loop_events 的思考 burst 聚合——reasoning_delta 照常透传，burst 结束补发
   message.reasoning_completed（全文+用时），流在思考中途自然结束也要收束；
2. get_execution_traces_by_thread——不查询/投影 reasoning，只还原公开进度与真实动作；
3. record_terminal_message_event——取消/中断轮补 message_id 锚点（sequence 续号）；
4. run_reconcile_service——中断回填幂等 + completed 消息缺失按原 id 重建。
无 DB 依赖：沿用 test_create_run_persists 的假会话工厂模式。
"""
import asyncio
import json
import unittest
from datetime import datetime
from typing import Any, List

from app.services.tasks import task_run_service as trs
from app.services import sse_protocol
from app.services.chat import main_tool_turn


def _parse(payload: str) -> dict:
    return json.loads(payload.replace("data: ", "", 1))


async def _collect(gen) -> List[dict]:
    return [_parse(p) async for p in gen if p]


class _FakeResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class _FakeSession:
    """按调用顺序回放 execute 结果；add/commit 记录写入。"""

    def __init__(self, results: List[List[Any]], sink: List[Any]):
        self._results = list(results)
        self._sink = sink
        self.gets: dict = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *_a, **_k):
        return _FakeResult(self._results.pop(0) if self._results else [])

    async def get(self, model, key, **_kwargs):
        return self.gets.get((getattr(model, "__name__", str(model)), key))

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = 9001
        self._sink.append(obj)

    async def commit(self):
        pass


class PublicCommentaryMappingTests(unittest.TestCase):
    def _run(self, events):
        channel = sse_protocol.SSEChannel(sse_protocol.HARNESS, "t1", "r1")
        out = {"answer": "", "trace": [], "usage_prompt_tokens": 0,
               "suspended": None, "streamed_any": False,
               "task_graph_failed": False, "task_outcome": None}

        async def _iter():
            for ev in events:
                yield ev

        return asyncio.run(_collect(
            main_tool_turn.map_tool_loop_events(channel, _iter(), out)
        )), out

    def test_public_commentary_stays_narration_after_action(self):
        frames, _ = self._run([
            {"type": "commentary", "text": "我先读取现有结构。"},
            {"type": "tool_started", "name": "read_file", "args": {"path": "a.ts"}},
            {"type": "tool_result", "name": "read_file", "status": "succeeded", "preview": "ok"},
            {"type": "commentary", "text": "已确认现有执行流足够承载这段摘要。"},
        ])
        commentaries = [frame for frame in frames if frame["type"] == "message.commentary"]
        # 第一句只是单个琐碎读取的时序预告，由工具行表达；真实结果叙述必须保留。
        self.assertEqual(len(commentaries), 1)
        self.assertEqual(commentaries[0]["data"]["text"], "已确认现有执行流足够承载这段摘要。")
        self.assertNotIn("kind", commentaries[0]["data"])

    def test_internal_schema_commentary_is_neither_persisted_nor_answer(self):
        leaked = (
            "补充明确契约：image-map.json 必须是扁平 JSON 对象，"
            "键是输出文件名。不要读脚本，按此格式立即继续。"
        )
        frames, out = self._run([
            {"type": "delta", "text": leaked},
            {"type": "commentary", "text": leaked},
        ])
        self.assertFalse(
            any(frame["type"] == "message.commentary" for frame in frames),
            "内部 schema 自纠不得作为公开进度写入 SSE/历史",
        )
        self.assertEqual(out["answer"], "", "内部 schema 自纠不得残留在最终回答")

    def test_answer_sized_tool_round_draft_is_not_emitted_as_commentary(self):
        leaked_draft = (
            "入学报到怎么办理\n\n"
            "报到时间、所需证件和现场缴费流程已经核对清楚。\n\n"
            "- 已缴费：领取宿舍钥匙后到学院报到。\n"
            "- 未缴费：先到学院报到，再缴费并安排床位。\n\n"
            "校园风光\n\n"
            "学校官网展示了教学楼、图书馆、实训公园、足球场和校园夜景。\n"
        ) * 4
        final_answer = "报到时请携带录取通知书和身份证，并按录取专业到对应校区办理。"
        frames, out = self._run([
            {"type": "commentary", "text": leaked_draft, "kind": "tool_round"},
            {"type": "delta", "text": final_answer},
        ])

        self.assertFalse(
            any(frame["type"] == "message.commentary" for frame in frames),
            "工具轮的完整答案草稿不得成为第二条可见答案通道",
        )
        self.assertEqual(
            [frame["data"]["text"] for frame in frames if frame["type"] == "message.delta"],
            [final_answer],
            "Responses 工具轮草稿应留在模型上下文，只有下一轮权威终答进入正文",
        )
        self.assertEqual(out["answer"], final_answer)


def _event(run_id: str, seq: int, etype: str, data: dict):
    from app.runtime_models import AgentRunEvent
    ev = AgentRunEvent(run_id=run_id, event_id=f"e{seq}", sequence=seq, type=etype, data=data)
    ev.created_at = datetime(2026, 7, 17, 12, 0, 0)
    return ev


def _run_row(run_id: str, thread_id: str = "t1", status: str = "completed"):
    from app.runtime_models import AgentRun
    r = AgentRun(id=run_id, thread_id=thread_id, user_id="u1", status=status, kind="chat")
    r.created_at = datetime(2026, 7, 17, 11, 59, 0)
    r.completed_at = datetime(2026, 7, 17, 12, 1, 0)
    return r


class ExecutionTraceReasoningTests(unittest.TestCase):
    def _traces(self, runs, events, reasoning_run_ids):
        # 查询顺序：runs → trace events → RunInstruction；私有推理不再另查。
        results = [runs, events, []]
        orig = trs.runtime_session
        trs.runtime_session = lambda: (lambda: _FakeSession(results, []))  # type: ignore[assignment]
        try:
            return asyncio.run(trs.get_execution_traces_by_thread("t1"))
        finally:
            trs.runtime_session = orig  # type: ignore[assignment]

    def test_reasoning_completed_projects_compact_thinking(self):
        events = [
            _event("r1", 1, "message.reasoning.completed", {"text": "完整思考原文", "seconds": 3.2}),
            _event("r1", 2, "message.completed", {"text": "答", "message_id": 42}),
        ]
        traces = self._traces([_run_row("r1")], events, ["r1"])
        thinking = [s for s in traces[42]["steps"] if s.get("kind") == "thinking"]
        self.assertEqual(len(thinking), 1)
        self.assertEqual(thinking[0]["text"], "完整思考原文")
        self.assertEqual(thinking[0]["seconds"], 3.2)
        self.assertEqual(traces[42]["reasoning_summary"], "完整思考原文")
        self.assertEqual(traces[42]["reasoning_seconds"], 3.2)

    def test_legacy_run_does_not_invent_fixed_reasoning(self):
        events = [_event("r1", 1, "message.completed", {"text": "答", "message_id": 42})]
        traces = self._traces([_run_row("r1")], events, ["r1"])
        thinking = [s for s in traces[42]["steps"] if s.get("kind") == "thinking"]
        self.assertEqual(thinking, [])

    def test_public_commentary_is_projected_without_raw_reasoning_tokens(self):
        raw = "前面铺垫了很多过程。" + ("内部原始推理细节。" * 400) + "最后确认可以继续。"
        events = [
            _event("r1", 1, "message.commentary", {
                "text": "已核对现有结构。",
            }),
            _event("r1", 2, "message.reasoning.completed", {"text": raw, "seconds": 2.0}),
            _event("r1", 3, "message.completed", {"text": "答", "message_id": 42}),
        ]
        trace = self._traces([_run_row("r1")], events, ["r1"])[42]
        self.assertEqual(trace["preamble"], "已核对现有结构。")
        thinking = [s for s in trace["steps"] if s.get("kind") == "thinking"]
        self.assertEqual(len(thinking), 1)
        self.assertTrue(thinking[0]["text"].endswith("最后确认可以继续。"))
        self.assertLessEqual(len(thinking[0]["text"]), 2000)
        self.assertNotEqual(thinking[0]["text"], raw)
        self.assertNotIn(raw, str(trace))
        self.assertNotIn("内部原始推理细节", trace["preamble"])

    def test_duplicate_sequence_projected_once(self):
        events = [
            _event("r1", 1, "message.reasoning.completed", {"text": "思"}),
            _event("r1", 1, "message.reasoning.completed", {"text": "思"}),
            _event("r1", 2, "message.completed", {"text": "答", "message_id": 42}),
        ]
        traces = self._traces([_run_row("r1")], events, [])
        thinking = [s for s in traces[42]["steps"] if s.get("kind") == "thinking"]
        self.assertEqual(len(thinking), 1)
        self.assertEqual(thinking[0]["text"], "思")

    def test_commentary_after_all_plan_steps_stays_at_timeline_tail(self):
        events = [
            _event("r1", 1, "plan.updated", {
                "goal_revision": 1, "plan_version": 1,
                "steps": [{"title": "读取文件", "status": "running"}],
            }),
            _event("r1", 2, "tool.started", {"name": "read_file", "args": {}}),
            _event("r1", 3, "tool.completed", {
                "name": "read_file", "result_preview": "已读取",
            }),
            _event("r1", 4, "plan.updated", {
                "goal_revision": 1, "plan_version": 2,
                "steps": [{"title": "读取文件", "status": "completed"}],
            }),
            _event("r1", 5, "message.commentary", {
                "text": "我已经完成核对，接下来整理最终交付。",
            }),
            _event("r1", 6, "message.completed", {"text": "答", "message_id": 42}),
        ]
        traces = self._traces([_run_row("r1")], events, [])
        notes = [s for s in traces[42]["steps"] if s.get("kind") == "note"]
        self.assertEqual(notes[0]["planKey"], "__fallback_tail__")

    def test_legacy_tagged_plan_commentary_restores_plan_card(self):
        tagged = (
            "<proposed_plan>\n"
            "# 调研并生成 Word\n"
            "## Summary\n核对资料后生成文档。\n"
            "## Key Changes\n- 整理规格\n- 生成文档\n"
            "## Test Plan\n- 验证文件可打开\n"
            "## Assumptions\n- 无\n"
            "</proposed_plan>"
        )
        events = [
            # 2026-08-25 的旧事件没有 kind=plan，但块标签是完整的。
            _event("r1", 1, "message.commentary", {"text": tagged}),
            _event("r1", 2, "message.completed", {"text": "", "message_id": 42}),
        ]

        trace = self._traces([_run_row("r1")], events, [])[42]

        self.assertEqual(trace["plan_report"], tagged.split("\n", 1)[1].rsplit("\n", 1)[0])
        self.assertFalse(
            any("<proposed_plan>" in str(step.get("text") or "") for step in trace["steps"])
        )

    def test_plan_resume_boundary_keeps_plan_only_in_previous_segment(self):
        report = (
            "# 调研并生成 Word\n\n## Summary\n核对资料。\n\n"
            "## Key Changes\n- 整理规格\n- 生成 Word\n\n"
            "## Test Plan\n- 文件可打开\n\n## Assumptions\n- 无"
        )
        events = [
            _event("r1", 1, "message.commentary", {"text": report, "kind": "plan"}),
            _event("r1", 2, "input.received", {
                "message_id": 41,
                "input_id": "resume-1",
                "content": "好，请执行此计划。",
            }),
            _event("r1", 3, "tool.started", {"name": "bash", "args": {}}),
            _event("r1", 4, "tool.completed", {"name": "bash", "result_preview": "ok"}),
            _event("r1", 5, "message.completed", {"text": "已生成。", "message_id": 42}),
        ]

        trace = self._traces([_run_row("r1")], events, [])[42]

        self.assertEqual(len(trace["segments"]), 1)
        self.assertEqual(trace["segments"][0]["plan_report"], report)
        self.assertIsNone(trace["plan_report"])
        self.assertTrue(any(step.get("name") == "bash" for step in trace["steps"]))

    def test_duplicate_plan_titles_keep_distinct_stable_keys_after_refresh(self):
        events = [
            _event("r1", 1, "plan.updated", {
                "goal_revision": 1, "plan_version": 1,
                "steps": [
                    {"key": "first", "title": "核对文件", "status": "running"},
                    {"key": "second", "title": "核对文件", "status": "pending"},
                ],
            }),
            _event("r1", 2, "tool.started", {"name": "read_file", "args": {}}),
            _event("r1", 3, "tool.completed", {
                "name": "read_file", "result_preview": "第一次核对完成",
            }),
            _event("r1", 4, "plan.updated", {
                "goal_revision": 1, "plan_version": 2,
                "steps": [
                    {"key": "first", "title": "核对文件", "status": "completed"},
                    {"key": "second", "title": "核对文件", "status": "running"},
                ],
            }),
            _event("r1", 5, "tool.started", {"name": "read_file", "args": {}}),
            _event("r1", 6, "tool.completed", {
                "name": "read_file", "result_preview": "第二次核对完成",
            }),
            _event("r1", 7, "message.completed", {"text": "答", "message_id": 42}),
        ]
        traces = self._traces([_run_row("r1")], events, [])
        tools = [step for step in traces[42]["steps"] if step.get("kind") == "tool"]
        self.assertEqual([step["planKey"] for step in tools], ["first", "second"])


class TerminalMessageAnchorTests(unittest.TestCase):
    def test_appends_message_completed_with_next_sequence(self):
        sink: list = []
        orig = trs.runtime_session
        trs.runtime_session = lambda: (lambda: _FakeSession([[7]], sink))  # type: ignore[assignment]
        try:
            asyncio.run(trs.record_terminal_message_event("r1", 42, "部分正文"))
        finally:
            trs.runtime_session = orig  # type: ignore[assignment]
        self.assertEqual(len(sink), 1)
        row = sink[0]
        self.assertEqual(row.sequence, 8)
        self.assertEqual(row.type, "message.completed")
        self.assertEqual(row.data["message_id"], 42)
        self.assertEqual(row.data["text"], "部分正文")


class RunReconcileTests(unittest.TestCase):
    def test_backfill_inserts_interrupted_row_and_anchor(self):
        from app.services.tasks import run_reconcile_service as rrs

        sink: list = []
        anchors: list = []

        async def _fake_collect(run_id):
            return {"text": "已流出的一半", "message_id": None}

        async def _fake_anchor(run_id, mid, text=""):
            anchors.append((run_id, mid, text))

        async def _fake_status(_run_id):
            return "failed"

        terminal_events: list[tuple[str, str, dict]] = []

        async def _fake_append(run_id, event_type, data):
            terminal_events.append((run_id, event_type, data))

        orig_collect = trs.collect_run_output_text
        orig_anchor = trs.record_terminal_message_event
        orig_status = trs.get_run_status
        orig_append = trs.append_run_event
        orig_session = rrs.async_session
        trs.collect_run_output_text = _fake_collect  # type: ignore[assignment]
        trs.record_terminal_message_event = _fake_anchor  # type: ignore[assignment]
        trs.get_run_status = _fake_status  # type: ignore[assignment]
        trs.append_run_event = _fake_append  # type: ignore[assignment]
        session = _FakeSession([[]], sink)
        session.gets[("ChatThread", "t1")] = object()  # 会话仍存在
        rrs.async_session = lambda: session  # type: ignore[assignment]
        try:
            n = asyncio.run(rrs.backfill_interrupted_runs([{"id": "r1", "thread_id": "t1"}]))
        finally:
            trs.collect_run_output_text = orig_collect  # type: ignore[assignment]
            trs.record_terminal_message_event = orig_anchor  # type: ignore[assignment]
            trs.get_run_status = orig_status  # type: ignore[assignment]
            trs.append_run_event = orig_append  # type: ignore[assignment]
            rrs.async_session = orig_session  # type: ignore[assignment]
        self.assertEqual(n, 1)
        self.assertEqual(len(sink), 1)
        row = sink[0]
        self.assertEqual(row.status, "interrupted")
        self.assertEqual(row.run_id, "r1")
        self.assertEqual(row.content, "已流出的一半")
        self.assertEqual(anchors, [("r1", 9001, "已流出的一半")])
        self.assertEqual(terminal_events[0][0:2], ("r1", "run.failed"))

    def test_backfill_skips_when_partial_row_exists(self):
        from app.services.tasks import run_reconcile_service as rrs

        sink: list = []
        orig_session = rrs.async_session
        orig_status = trs.get_run_status

        async def _fake_status(_run_id):
            return "failed"

        # 第一次 execute（判重查询）返回已有行 id → 跳过，不再写入
        rrs.async_session = lambda: _FakeSession([[123]], sink)  # type: ignore[assignment]
        trs.get_run_status = _fake_status  # type: ignore[assignment]
        try:
            n = asyncio.run(rrs.backfill_interrupted_runs([{"id": "r1", "thread_id": "t1"}]))
        finally:
            rrs.async_session = orig_session  # type: ignore[assignment]
            trs.get_run_status = orig_status  # type: ignore[assignment]
        self.assertEqual(n, 0)
        self.assertEqual(sink, [])

    def test_completed_reconcile_skips_deleted_thread(self):
        from app.services.tasks import run_reconcile_service as rrs

        sink: list = []

        async def _fake_missing_check(window_hours):
            return [{"run_id": "r1", "thread_id": "t-gone", "message_id": 42, "text": "x"}]

        orig_check = trs.list_completed_runs_missing_check
        orig_session = rrs.async_session
        trs.list_completed_runs_missing_check = _fake_missing_check  # type: ignore[assignment]
        rrs.async_session = lambda: _FakeSession([], sink)  # ChatThread get → None（已删）
        try:
            n = asyncio.run(rrs.reconcile_completed_messages(window_hours=2))
        finally:
            trs.list_completed_runs_missing_check = orig_check  # type: ignore[assignment]
            rrs.async_session = orig_session  # type: ignore[assignment]
        self.assertEqual(n, 0)
        self.assertEqual(sink, [])

    def test_completed_reconcile_rebuilds_missing_message_with_original_id(self):
        from app.services.tasks import run_reconcile_service as rrs

        sink: list = []

        async def _fake_missing_check(window_hours):
            return [{"run_id": "r1", "thread_id": "t1", "message_id": 42, "text": "完整回答"}]

        orig_check = trs.list_completed_runs_missing_check
        orig_session = rrs.async_session
        trs.list_completed_runs_missing_check = _fake_missing_check  # type: ignore[assignment]
        session = _FakeSession([], sink)  # ChatMessage get 返回 None → 缺行 → 重建
        session.gets[("ChatThread", "t1")] = object()  # 会话仍存在（已删会话则跳过）
        rrs.async_session = lambda: session  # type: ignore[assignment]
        try:
            n = asyncio.run(rrs.reconcile_completed_messages(window_hours=2))
        finally:
            trs.list_completed_runs_missing_check = orig_check  # type: ignore[assignment]
            rrs.async_session = orig_session  # type: ignore[assignment]
        self.assertEqual(n, 1)
        row = sink[0]
        self.assertEqual(row.id, 42)
        self.assertEqual(row.content, "完整回答")
        self.assertEqual(row.status, "completed")


if __name__ == "__main__":
    unittest.main()
