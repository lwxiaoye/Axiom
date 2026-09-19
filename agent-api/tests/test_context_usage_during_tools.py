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


def test_agent_loop_emits_usage_every_round_not_only_tool_rounds():
    """每轮 LLM 结束都发 actual usage，**不再**限定「有 tool_frags 的轮次」。

    旧条件（只在工具轮发）会让多步任务的首轮规划/长思考期一直停在「0 tokens · 正在思考…」
    ——首轮往往没有 tool_frags，一帧 actual 也发不出去。现役实现的主循环 usage 帧只看
    usage_prompt_tokens > 0。用源码断言（真跑一轮要整套 LLM 桩，成本远高于收益）：
    主循环那一处 yield 的直接守卫里不得再出现 tool_frags。
    """
    import inspect

    from app.services.agent_harness import model_driver

    src = inspect.getsource(model_driver)
    marker = "每轮 LLM 结束都发 actual usage"
    assert marker in src, "主循环逐轮上报 usage 的注释锚点丢了——请同步更新本用例"
    site = src.index('yield {"type": "usage"', src.index(marker))
    guard_start = src.rfind("\n", 0, src.rfind("if ", 0, site))
    guard = src[guard_start:site]
    assert "usage_prompt_tokens > 0" in guard
    assert "tool_frags" not in guard, "usage 帧不得再限定在有工具调用的轮次（首轮规划期会显示 0 tokens）"
