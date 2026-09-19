import asyncio
from datetime import datetime
import json
from types import SimpleNamespace

from app.services.agent_harness.model_driver import (
    LoopState,
    _call_seeds_provisional_plan,
    _fallback_tool_checkpoint,
    _opening_checkpoint_text,
)
from app.services.chat.main_tool_turn import map_tool_loop_events
from app.services.chat.turn_context_builder import (
    CODEX_COMMENTARY_STYLE_RULE,
    _build_system_prompt,
)
from app.services.chat.types import TurnOutcome
from app.services.chat.tools.plan import _normalize_plan_steps
from app.services.sse_protocol import HARNESS, SSEChannel
from app.services.tasks import task_run_service


def _tool(*tags: str):
    return SimpleNamespace(spec=SimpleNamespace(
        semantic_tags=frozenset(tags),
        public_action="执行命令",
    ))


def test_plain_bash_opening_is_neutral_not_artifact_delivery():
    calls = [(0, {}, "bash", {"command": "printf ok", "intent": "读取命令结果"}, "")]
    text = _opening_checkpoint_text(calls, {"bash": _tool("productive", "artifact_producer")})
    assert "产物" not in text
    assert "读取命令结果" in text


def test_bash_with_delivery_target_can_use_artifact_opening():
    calls = [(0, {}, "bash", {"command": "python build.py > /workspace/files/report.txt"}, "")]
    text = _opening_checkpoint_text(calls, {"bash": _tool("productive", "artifact_producer")})
    assert "产物" in text


def test_completed_artifact_tag_without_receipt_uses_neutral_summary():
    text = _fallback_tool_checkpoint(
        items=[{
            "name": "bash",
            "label": "执行命令",
            "semantic_tags": ["productive", "artifact_producer"],
            "failed": False,
            "preview": "ok",
            "delivered_artifact": False,
        }],
        state=LoopState(),
    )
    assert "产物" not in text
    assert "执行命令" in text


def test_read_only_bash_does_not_seed_task_plan():
    tools = {"bash": _tool("productive", "artifact_producer")}
    assert not _call_seeds_provisional_plan(
        "bash", {"command": "date", "intent": "获取当前时间"}, tools,
    )
    assert _call_seeds_provisional_plan(
        "bash",
        {"command": "python build.py > /workspace/files/report.txt"},
        tools,
    )


def test_completed_artifact_receipt_uses_delivery_summary():
    text = _fallback_tool_checkpoint(
        items=[{
            "name": "write_file",
            "label": "写入报告",
            "semantic_tags": ["artifact_producer"],
            "failed": False,
            "preview": "report.md",
            "delivered_artifact": True,
        }],
        state=LoopState(),
    )
    assert "产物" in text


def test_plan_normalization_preserves_terminal_status_and_reason():
    rows = _normalize_plan_steps([
        {"key": "skip", "title": "Skip", "status": "skipped", "reason": "not needed"},
        {"key": "old", "title": "Old", "status": "invalidated", "reason": "goal changed"},
    ])
    assert [row["status"] for row in rows] == ["skipped", "invalidated"]
    assert [row["reason"] for row in rows] == ["not needed", "goal changed"]


def test_plan_sse_projects_reason_when_detail_is_empty():
    frame = SSEChannel(HARNESS, "thread", "run").task_plan_updated([{
        "key": "old",
        "title": "Old",
        "status": "invalidated",
        "reason": "goal changed",
        "goal_revision": 2,
        "plan_version": 3,
    }])
    step = json.loads(frame.removeprefix("data: "))["data"]["steps"][0]
    assert step["status"] == "invalidated"
    assert step["detail"] == "goal changed"
    assert step["reason"] == "goal changed"


def test_artifact_saved_rejects_filename_without_persisted_file_id():
    channel = SSEChannel(HARNESS, "thread", "run")
    frame = json.loads(channel.artifact_saved([
        {"filename": "missing.pdf", "size": 1},
        {"id": "file-1", "filename": "report.pdf", "size": 2},
    ], source="write_file").removeprefix("data: "))
    assert frame["type"] == "artifact.saved"
    assert frame["data"]["files"] == [{"id": "file-1", "filename": "report.pdf", "size": 2}]


def test_successful_delivery_emits_canonical_artifact_saved_after_tool_receipt():
    async def events():
        yield {
            "type": "tool_result",
            "name": "write_file",
            "status": "succeeded",
            "preview": "saved",
            "meta": {"files": [{"id": "file-1", "filename": "report.pdf", "size": 2}]},
        }

    async def collect():
        out = {"latest_task_plan": []}
        channel = SSEChannel(HARNESS, "thread", "run")
        return [
            json.loads(frame.removeprefix("data: "))
            async for frame in map_tool_loop_events(channel, events(), out)
        ]

    frames = asyncio.run(collect())
    assert [frame["type"] for frame in frames] == ["tool.completed", "artifact.saved"]
    assert frames[-1]["data"]["files"][0]["id"] == "file-1"


def test_research_progress_is_emitted_only_for_explicit_research_profile(monkeypatch):
    """Standard/Plan may search or update a plan, but those facts cannot change Profile identity."""
    from app.services.agent_harness.research import engine as research_engine

    async def fake_ingest(*_args, **_kwargs):
        return object()

    async def fake_sync(*_args, **_kwargs):
        return object()

    monkeypatch.setattr(research_engine, "ingest_tool_receipt", fake_ingest)
    monkeypatch.setattr(research_engine, "sync_topics_from_plan", fake_sync)
    monkeypatch.setattr(research_engine, "progress_payload", lambda _ledger: {
        "stage": "researching",
        "search_calls": 1,
    })

    async def events():
        yield {
            "type": "task_plan",
            "steps": [{"key": "search", "title": "查资料", "status": "running"}],
        }
        yield {
            "type": "tool_result",
            "name": "search_web",
            "args": {"query": "Kimi K3"},
            "status": "completed",
            "preview": "ok",
            "observation": {"evidence_refs": []},
        }

    async def collect(research_profile: bool):
        out = {"trace": [], "latest_task_plan": []}
        channel = SSEChannel(HARNESS, "thread", "run")
        return [
            json.loads(frame.removeprefix("data: "))
            async for frame in map_tool_loop_events(
                channel, events(), out, research_profile=research_profile,
            )
        ]

    standard_frames = asyncio.run(collect(False))
    research_frames = asyncio.run(collect(True))
    assert "research.progress" not in [frame["type"] for frame in standard_frames]
    assert [frame["type"] for frame in research_frames].count("research.progress") == 2


def test_tool_round_public_commentary_precedes_buffered_reasoning():
    async def events():
        yield {"type": "commentary", "text": "现有结构已经确认，接下来检查边界。"}
        yield {"type": "reasoning", "text": "第一段"}
        yield {"type": "reasoning", "text": "第二段"}

    async def collect():
        out = {"answer": "", "trace": [], "latest_task_plan": []}
        channel = SSEChannel(HARNESS, "thread", "run")
        return [
            json.loads(frame.removeprefix("data: "))
            async for frame in map_tool_loop_events(channel, events(), out)
        ]

    frames = asyncio.run(collect())
    assert [frame["type"] for frame in frames] == [
        "message.commentary",
        "message.reasoning.delta",
        "message.reasoning.delta",
        "message.reasoning.completed",
    ]
    assert frames[3]["data"].get("text") == "第一段第二段"
    assert "seconds" in frames[3]["data"]


def test_model_connection_recovery_is_a_structured_visible_event():
    async def events():
        yield {
            "type": "model_connection",
            "status": "recovering",
            "transport": "stream_retry",
            "attempt": 5,
            "max_retries": 5,
            "delay_seconds": 3.2,
        }
        yield {
            "type": "model_connection",
            "status": "failed",
            "transport": "stream_retry",
            "attempt": 5,
            "max_retries": 5,
        }

    async def collect():
        out = {"answer": "", "trace": [], "latest_task_plan": []}
        channel = SSEChannel(HARNESS, "thread", "run")
        return [
            json.loads(frame.removeprefix("data: "))
            async for frame in map_tool_loop_events(channel, events(), out)
        ]

    frames = asyncio.run(collect())
    assert [frame["type"] for frame in frames] == ["model.connection", "model.connection"]
    assert [frame["data"]["status"] for frame in frames] == ["recovering", "failed"]
    assert all(frame["data"]["transport"] == "stream_retry" for frame in frames)
    assert frames[0]["data"] == {
        "status": "recovering",
        "transport": "stream_retry",
        "attempt": 5,
        "max_retries": 5,
        "delay_seconds": 3.2,
    }


def test_turn_outcome_accepts_public_commentary_state():
    async def events():
        yield {
            "type": "commentary",
            "text": "技能规范已经确认，页面结构必须遵循其中的字段约束。下一步会把内容骨架与视觉素材一起落进工程。",
        }

    async def collect():
        channel = SSEChannel(HARNESS, "thread", "run")
        out = TurnOutcome()
        frames = [
            json.loads(frame.removeprefix("data: "))
            async for frame in map_tool_loop_events(channel, events(), out)
        ]
        return out, frames

    out, frames = asyncio.run(collect())
    assert out.latest_public_commentary.startswith("技能规范已经确认")
    assert [frame["type"] for frame in frames] == ["message.commentary"]


def test_existing_public_preamble_suppresses_first_tool_round_repeat():
    async def events():
        yield {"type": "commentary", "text": "我先查看 PPTD 字段定义和示例。"}
        yield {"type": "reasoning", "text": "核对字段"}
        yield {"type": "tool_started", "name": "read_file", "args": {"path": "pptd.md"}}

    async def collect():
        out = TurnOutcome(latest_public_commentary="版式约束与工程字段需要一起对齐。规范和示例会成组核对，再据此搭建页面。")
        channel = SSEChannel(HARNESS, "thread", "run")
        frames = [
            json.loads(frame.removeprefix("data: "))
            async for frame in map_tool_loop_events(channel, events(), out)
        ]
        return out, frames

    out, frames = asyncio.run(collect())
    assert out.latest_public_commentary.startswith("版式约束")
    assert [frame["type"] for frame in frames] == [
        "message.reasoning.delta",
        "message.reasoning.completed",
        "tool.started",
    ]


def test_short_temporal_action_commentary_stays_quiet_after_tool_result():
    async def events():
        yield {
            "type": "tool_result",
            "name": "read_file",
            "status": "completed",
            "preview": "ok",
            "args": {"path": "pptd.md"},
        }
        yield {"type": "commentary", "text": "我先读取一个示例页面和清单，确认字段写法。"}

    async def collect():
        out = TurnOutcome(latest_public_commentary="版式与字段约束会一起核对。")
        channel = SSEChannel(HARNESS, "thread", "run")
        return [
            json.loads(frame.removeprefix("data: "))
            async for frame in map_tool_loop_events(channel, events(), out)
        ]

    frames = asyncio.run(collect())
    assert [frame["type"] for frame in frames] == ["tool.completed"]


def test_system_prompt_uses_one_codex_commentary_contract():
    prompt = _build_system_prompt()
    assert CODEX_COMMENTARY_STYLE_RULE in prompt
    assert "单个琐碎读取" in prompt
    assert "新证据或用户插话改变了方向" in prompt
    assert "不要每调一个工具都配一句" in prompt
    assert "同时包含一个具体结果和下一步" in prompt
    assert "用 1–2 句把前一阶段与下一阶段连起来" in prompt
    assert "不要用「我先」「我会先」「我再」「现在」" in prompt
    assert "大约需要一两分钟，请稍候" not in prompt


def test_reasoning_burst_closes_when_driver_stream_ends():
    async def events():
        yield {"type": "reasoning", "text": "只返回这一段"}

    async def collect():
        out = {"answer": "", "trace": [], "latest_task_plan": []}
        channel = SSEChannel(HARNESS, "thread", "run")
        return [
            json.loads(frame.removeprefix("data: "))
            async for frame in map_tool_loop_events(channel, events(), out)
        ]

    frames = asyncio.run(collect())
    assert [frame["type"] for frame in frames] == [
        "message.reasoning.delta",
        "message.reasoning.completed",
    ]
    assert frames[1]["data"]["text"] == "只返回这一段"


def test_n_reasoning_bursts_emit_n_completed_summaries():
    async def events():
        yield {"type": "reasoning", "text": "先想清楚目标是什么。"}
        yield {"type": "tool_started", "name": "bash", "args": {"command": "date"}}
        yield {
            "type": "tool_result",
            "name": "bash",
            "status": "completed",
            "preview": "ok",
            "call_id": "c1",
        }
        yield {"type": "reasoning", "text": "再核对输出是否可用。"}
        yield {"type": "commentary", "text": "命令已经跑完。"}

    async def collect():
        out = {"answer": "", "trace": [], "latest_task_plan": []}
        channel = SSEChannel(HARNESS, "thread", "run")
        return [
            json.loads(frame.removeprefix("data: "))
            async for frame in map_tool_loop_events(channel, events(), out)
        ]

    frames = asyncio.run(collect())
    completed = [frame for frame in frames if frame["type"] == "message.reasoning.completed"]
    assert len(completed) == 2
    assert completed[0]["data"]["text"] == "先想清楚目标是什么。"
    assert completed[1]["data"]["text"] == "再核对输出是否可用。"
    assert all("seconds" in frame["data"] for frame in completed)


def test_reasoning_completed_is_persisted_in_event_catalog():
    from app.services.agent_harness.event_catalog import EVENT_CATALOG

    assert EVENT_CATALOG["message.reasoning.delta"].persisted is False
    assert EVENT_CATALOG["message.reasoning.completed"].persisted is True


def test_compact_reasoning_summary_keeps_replay_body():
    from app.services.sse_protocol import compact_reasoning_summary

    long_burst = "前面铺垫了很多过程。中间还有一句过渡。" + ("细节。" * 20) + "最后得出可以继续。"
    summary = compact_reasoning_summary(long_burst)
    assert summary.endswith("最后得出可以继续。")
    assert "前面铺垫了很多过程。" in summary
    assert len(summary) <= 2000

    english = (
        "The user asked a short question. I should answer directly without tools. "
        "that I can answer directly without any tools. No plan is needed."
    )
    english_summary = compact_reasoning_summary(english)
    assert english_summary.startswith("The user asked")
    assert "No plan is needed." in english_summary


def test_raw_bash_output_is_not_duplicated_as_commentary():
    async def events():
        yield {
            "type": "tool_result",
            "name": "bash",
            "args": {"command": "date"},
            "status": "completed",
            "preview": "Sun Aug 16 20:45:17 UTC 2026",
        }
        yield {
            "type": "commentary",
            "text": "命令输出是：\n\n```text\nSun Aug 16 20:45:17 UTC 2026\n```",
        }

    async def collect():
        out = {"answer": "", "trace": [], "latest_task_plan": []}
        channel = SSEChannel(HARNESS, "thread", "run")
        return [
            json.loads(frame.removeprefix("data: "))
            async for frame in map_tool_loop_events(channel, events(), out)
        ]

    frames = asyncio.run(collect())
    assert [frame["type"] for frame in frames] == ["tool.completed"]


def test_tool_observation_survives_failure_before_final_event():
    observation = {
        "call_id": "call-bash-1",
        "tool_name": "bash",
        "status": "succeeded",
        "summary": "ok",
        "structured_data": {},
        "evidence_refs": [],
        "artifact_refs": [],
        "receipts": [],
        "retryable": False,
    }

    async def events():
        yield {
            "type": "tool_result",
            "name": "bash",
            "args": {"command": "printf ok"},
            "status": "completed",
            "preview": "ok",
            "semantic_tags": ["productive"],
            "observation": observation,
        }
        raise RuntimeError("final model request failed")

    async def collect():
        out = {"trace": [], "latest_task_plan": []}
        channel = SSEChannel(HARNESS, "thread", "run")
        try:
            async for _frame in map_tool_loop_events(channel, events(), out):
                pass
        except RuntimeError as exc:
            assert str(exc) == "final model request failed"
        return out

    out = asyncio.run(collect())
    assert out["trace"] == [{
        "call_id": "",
        "name": "bash",
        "args": {"command": "printf ok"},
        "status": "completed",
        "preview": "ok",
        "semantic_tags": ["productive"],
        "observation": observation,
    }]


class _Result:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class _Session:
    def __init__(self, results):
        self._results = list(results)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, *_args, **_kwargs):
        return _Result(self._results.pop(0) if self._results else [])


def test_partial_trace_preserves_authoritative_pending_plan():
    from app.runtime_models import AgentRun, AgentRunEvent

    run = AgentRun(
        id="run-partial",
        thread_id="thread",
        user_id="user",
        status="completed",
        outcome="partial",
        kind="chat",
        state={"phase": "partial"},
    )
    run.created_at = datetime(2026, 8, 16, 12, 0, 0)
    events = []
    for sequence, event_type, data in [
        (1, "plan.updated", {"steps": [
            {"key": "done", "title": "Done", "status": "completed"},
            {"key": "left", "title": "Left", "status": "pending"},
        ]}),
        (2, "message.commentary", {
            "kind": "reasoning_summary",
            "text": "Checked the persisted tool result.",
        }),
        (3, "message.completed", {"message_id": 42, "text": "Partially done"}),
        (4, "run.partial", {"message_id": 42, "reason_codes": ["missing_evidence"]}),
    ]:
        event = AgentRunEvent(
            run_id=run.id,
            event_id=f"event-{sequence}",
            sequence=sequence,
            type=event_type,
            data=data,
        )
        event.created_at = datetime(2026, 8, 16, 12, 0, sequence)
        events.append(event)

    original = task_run_service.runtime_session
    task_run_service.runtime_session = lambda: (lambda: _Session([[run], events, []]))
    try:
        trace = asyncio.run(task_run_service.get_execution_traces_by_thread("thread"))[42]
    finally:
        task_run_service.runtime_session = original

    assert trace["status"] == "partial"
    assert trace["event_cursor"] == 4
    assert trace["completedAt"] == int(events[-1].created_at.timestamp() * 1000)
    assert [step["status"] for step in trace["task_plan"]] == ["completed", "pending"]
    assert trace["preamble"] == "Checked the persisted tool result."
    assert trace["steps"] == []


def test_active_trace_without_assistant_message_keeps_snapshot_cursor():
    from app.runtime_models import AgentRun, AgentRunEvent

    run = AgentRun(
        id="run-active-without-assistant",
        thread_id="thread",
        user_id="user",
        status="running",
        kind="chat",
        state={"phase": "executing"},
    )
    run.created_at = datetime(2026, 8, 31, 12, 0, 0)
    events = []
    for sequence, event_type, data in [
        (1, "plan.updated", {"steps": [
            {"key": "research", "title": "Collect material", "status": "running"},
        ]}),
        (7, "message.commentary", {
            "kind": "reasoning_summary",
            "text": "Collecting the current material.",
        }),
    ]:
        event = AgentRunEvent(
            run_id=run.id,
            event_id=f"active-event-{sequence}",
            sequence=sequence,
            type=event_type,
            data=data,
        )
        event.created_at = datetime(2026, 8, 31, 12, 0, sequence)
        events.append(event)

    run_traces = {}
    original = task_run_service.runtime_session
    task_run_service.runtime_session = lambda: (lambda: _Session([[run], events, []]))
    try:
        message_traces = asyncio.run(task_run_service.get_execution_traces_by_thread(
            "thread",
            run_traces=run_traces,
        ))
    finally:
        task_run_service.runtime_session = original

    # 首条助手消息还没有 message_id：历史行字典可以为空，但活动 Run
    # 仍必须产生“当前轨迹 + 同批游标”，供前端从用户输入行恢复。
    assert message_traces == {}
    trace = run_traces[run.id]
    assert trace["status"] == "running"
    assert trace["run_phase"] == "executing"
    assert trace["event_cursor"] == 7
    assert trace["preamble"] == "Collecting the current material."
    assert [step["status"] for step in trace["task_plan"]] == ["running"]
