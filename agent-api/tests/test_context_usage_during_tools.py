# -*- coding: utf-8 -*-
"""工具期计量行必须有真实用量（2026-07-27 用户反馈「0 tokens 挂很久」）。

两件事叠起来造成整段 0 tokens：
1. `context_usage(kind="actual")` **只在整轮收尾发一次**（main_tool_turn 的 final 之后），
   工具期一帧都没有；
2. 前端计量行刻意忽略 `estimate` 帧（c12b2f51：纯问答会把 4.8k 估算误差显示成「本轮产出」）。

于是工具期 `liveContextTokens`/`turnBaselineTokens` 都是 undefined → ctxDelta=0，
正文还没开始流 → textEst=0 → 用户看到「0 tokens · 正在思考…」挂很久。

⚠️ 这条不能靠 tests/parity/ 兜：那套黄金快照里**工具调用轮不带 usage**（只有末尾的正文轮
带），所以新帧压根不会被触发 —— parity 全绿并不代表这条被验证过。
"""
import json

import pytest

from app.services import sse_protocol
from app.services.chat.main_tool_turn import map_tool_loop_events


def _channel():
    return sse_protocol.SSEChannel(sse_protocol.HARNESS, "t1", "RUN1")


async def _events(evs):
    for e in evs:
        yield e


def _frames(payloads):
    out = []
    for p in payloads:
        for line in str(p).splitlines():
            if line.startswith("data:"):
                try:
                    out.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    pass
    return out


@pytest.mark.asyncio
async def test_usage_event_becomes_actual_context_usage_frame():
    out: dict = {}
    payloads = [
        p async for p in map_tool_loop_events(
            _channel(),
            _events([
                {"type": "usage", "usage_prompt_tokens": 4321},
                {"type": "final", "answer": "好了", "trace": [], "usage_prompt_tokens": 4321},
            ]),
            {}, out, None, ctx_window=32000,
        )
    ]
    usage = [f for f in _frames(payloads) if f.get("type") == "context.usage"]
    assert usage, "工具期必须发出 context.usage，否则计量行一直是 0 tokens"
    assert usage[0]["data"]["kind"] == "actual", "必须是 actual —— 前端刻意忽略 estimate"
    assert usage[0]["data"]["tokens"] == 4321
    assert usage[0]["data"]["ratio"] == pytest.approx(4321 / 32000, abs=0.001)


@pytest.mark.asyncio
async def test_no_usage_frame_without_context_window():
    """拿不到窗口大小就没法算占比，不发半成品帧。"""
    out: dict = {}
    payloads = [
        p async for p in map_tool_loop_events(
            _channel(),
            _events([{"type": "usage", "usage_prompt_tokens": 4321},
                     {"type": "final", "answer": "x", "trace": []}]),
            {}, out, None, ctx_window=0,
        )
    ]
    assert not [f for f in _frames(payloads) if f.get("type") == "context.usage"]


def test_agent_loop_emits_usage_only_on_tool_rounds():
    """纯问答不多发帧：正文当场就在流，且会动到 c12b2f51 修过的那条路径。

    用源码断言这条约束（真跑一轮要整套 LLM 桩，成本远高于收益）：发用量的条件里
    必须同时带上 tool_frags。
    """
    import inspect

    from app.services.agent_harness import model_driver

    src = inspect.getsource(model_driver)
    idx = src.index('yield {"type": "usage"')
    guard = src[max(0, idx - 400):idx]
    assert "tool_frags" in guard, "发用量必须限定在有工具调用的轮次"
