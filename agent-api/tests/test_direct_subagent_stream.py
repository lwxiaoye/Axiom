"""显式 @ 子智能体必须复用 call_subagent 的流式可视化契约。"""

import json
from types import SimpleNamespace

import pytest

from app.services.agents import subagent_service
from app.services.chat import subagent_turn, turn_finalizer
from app.services.sse_protocol import HARNESS, SSEChannel


def _event(frame: str) -> dict:
    line = next(line for line in frame.splitlines() if line.startswith("data:"))
    return json.loads(line[5:].strip())


@pytest.mark.asyncio
async def test_subagent_stream_announces_resolved_identity_before_work(monkeypatch):
    app = SimpleNamespace(id="agent-1", name="文档识别助手", status="published")

    async def _resolve(_user, _subagent_id):
        return app

    async def _published(_app_id):
        return {"nodes": [], "edges": []}

    monkeypatch.setattr(subagent_service, "_resolve_accessible", _resolve)
    from app.services.gateway import tool_invoker
    monkeypatch.setattr(tool_invoker, "load_published_definition", _published)

    stream = subagent_service.run_subagent_stream(
        user=SimpleNamespace(user_id="u-1"), token="t", newapi_key="k",
        default_model="m", subagent_id="agent-1", message="读文档",
    )
    first = await anext(stream)
    await stream.aclose()

    assert first == {
        "type": "started",
        "subagent_id": "agent-1",
        "subagent_name": "文档识别助手",
        "icon": "",
    }


@pytest.mark.asyncio
async def test_direct_dispatch_emits_live_subagent_process(monkeypatch):
    async def _stream(**_kwargs):
        yield {"type": "started", "subagent_id": "agent-1", "subagent_name": "文档识别助手"}
        yield {"type": "node", "label": "提取正文", "status": "running"}
        yield {"type": "reasoning", "text": "先识别章节结构"}
        yield {"type": "delta", "text": "核心内容"}
        yield {"type": "delta", "text": "与问题清单"}
        yield {
            "type": "result", "status": "succeeded", "text": "核心内容与问题清单",
            "subagent_id": "agent-1", "subagent_name": "文档识别助手",
            "files": [{"id": "file-1", "filename": "识别结果.docx", "size": 128}],
        }

    persisted = {}

    async def _persist_delegation_turn(**kwargs):
        persisted.update(kwargs)

    async def _persist_assistant_turn(*_args, **_kwargs):
        return SimpleNamespace(id=91)

    async def _finalize_terminal(channel, *_args, **_kwargs):
        yield channel.run_completed(91)

    async def _stream_parent_summary(env, **_kwargs):
        yield env.channel.message_reasoning_delta("核对委派结果")
        yield env.channel.message_reasoning_completed()
        yield env.channel.message_delta("主智能体整理后的结论")
        yield env.channel.message_completed("主智能体整理后的结论", 91)
        yield env.channel.run_completed(91)
        yield env.channel.done()

    monkeypatch.setattr(subagent_service, "run_subagent_stream", _stream)
    monkeypatch.setattr(subagent_service, "persist_delegation_turn", _persist_delegation_turn)
    monkeypatch.setattr(turn_finalizer, "persist_assistant_turn", _persist_assistant_turn)
    monkeypatch.setattr(turn_finalizer, "finalize_terminal", _finalize_terminal)
    monkeypatch.setattr(subagent_turn, "stream_parent_summary", _stream_parent_summary)

    channel = SSEChannel(HARNESS, "thread-1", "run-1")
    env = SimpleNamespace(
        session=object(), thread=object(), channel=channel,
        run_id="run-1", thread_id="thread-1", user_id="u-1",
        user_context=SimpleNamespace(user_id="u-1"), token="t", newapi_key="k",
        resolved_model="m", message="请识别这份文档", attachments=[], regenerate=False,
        is_first_turn=False, prompt_rows=[SimpleNamespace(role="user", content="请识别这份文档")],
        prep=SimpleNamespace(effective_subagent_id="agent-1", route_info=None),
        model_input="请识别这份文档", spawn_bg=lambda _coro: None,
    )

    frames = [frame async for frame in subagent_turn.run_dispatch_turn(env)]
    payloads = [
        _event(frame) for frame in frames
        if frame.startswith("data:") and "[DONE]" not in frame
    ]
    event_types = [payload["type"] for payload in payloads]

    assert event_types[:9] == [
        "message.commentary", "subagent.preparing", "subagent.started", "subagent.node",
        "subagent.reasoning", "subagent.reasoning.completed", "subagent.delta", "subagent.delta",
        "subagent.completed",
    ]
    assert event_types.count("message.delta") == 1
    assert "message.reasoning.delta" in event_types
    assert event_types.index("subagent.completed") < event_types.index("message.reasoning.delta")
    assert event_types.index("subagent.completed") < event_types.index("artifact.saved")
    completed = next(payload for payload in payloads if payload["type"] == "subagent.completed")
    artifact = next(payload for payload in payloads if payload["type"] == "artifact.saved")
    assert completed["data"]["files"][0]["id"] == "file-1"
    assert artifact["data"]["files"][0]["filename"] == "识别结果.docx"
    text_events = [payload.get("data", {}).get("text", "") for payload in payloads if payload["type"] == "message.delta"]
    assert text_events == ["主智能体整理后的结论"]
    assert persisted["task"] == "请识别这份文档"
    assert persisted["result_text"] == "核心内容与问题清单"
    assert persisted["parent_thread_id"] == "thread-1"
    assert persisted["generated_files"][0]["id"] == "file-1"
