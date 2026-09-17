# -*- coding: utf-8 -*-
"""计划反空壳闸（2026-07-28）。

旧 DAG 的 `planner.investigate` 有三重边界：写死的只读工具集 + ≤4 轮上限 +
`is_no_investigation` 反空壳判定（findings 为空视为凭空生成）。DAG 删除后工具边界被
`action_authority="inspect"` 接住了、而且更强（物理上给不出写工具），但**反空壳判定
整条丢了**——模型一个文件都不读就直接写计划，没有任何机制能发现，而计划报告正是
计划轮唯一的交付物。`task_graph/types.py` 里那句「findings 是反空壳的关键字段」的
注释还留着，但已无人读。

计划最典型的失败不是格式不对，是编的。
"""
from app.services.agent_harness.model_driver import (
    LoopState,
    _turn_investigated,
    _turn_mutated,
)
from app.services.chat.tools.base import MainTool


def _t(name: str) -> dict:
    return {"name": name, "status": "ok"}


async def _noop(_args):
    return "ok"


def _tool(name: str, *, investigate: bool) -> MainTool:
    return MainTool(
        name=name,
        description=name,
        parameters={},
        execute=_noop,
        internal=True,
        semantic_tags=(("investigate",) if investigate else ("control",)),
        control_command=not investigate,
    )


TOOLS = {
    "reader": _tool("reader", investigate=True),
    "planner": _tool("planner", investigate=False),
}


def test_reading_or_searching_counts_as_investigation():
    assert _turn_investigated([_t("reader")], TOOLS) is True


def test_planning_only_tools_do_not_count():
    """update_plan / use_skill 不带来任何关于现状的信息，不能算勘查。

    这两个恰恰是计划轮里模型最容易「看起来在干活」的调用——把它们算进去，
    这道闸就等于不存在。
    """
    assert _turn_investigated([_t("planner")], TOOLS) is False


def test_empty_or_garbage_trace_is_not_investigation():
    assert _turn_investigated([], TOOLS) is False
    assert _turn_investigated(None, TOOLS) is False
    assert _turn_investigated(["不是字典", {"没有name": 1}], TOOLS) is False


def test_new_read_tool_is_automatically_an_investigation_signal():
    tool = _tool("future_reader", investigate=True)
    assert _turn_investigated([_t(tool.name)], {tool.name: tool}) is True


def test_investigation_and_mutation_are_distinct_signals():
    """勘查 ≠ 动手：两张网各管各的，判据不能混。"""
    assert _turn_investigated([_t("reader")], TOOLS) is True
    assert _turn_mutated([_t("reader")], TOOLS) is False


def test_loop_state_only_pushes_back_once():
    """勘查有成本，推第二次就是空转——状态位必须是一次性的。"""
    st = LoopState()
    assert st.plan_grounding_checked is False
    st.plan_grounding_checked = True
    assert st.plan_grounding_checked is True


# ---- 闸真的会开火吗（用假模型驱动真循环）----
import json
import unittest
from unittest.mock import patch

from app.services.agent_harness import model_driver
from app.services.chat.tools.base import MainTool
from tests.test_tool_loop_circuit_breakers import (
    DONE,
    FakeAsyncClient,
    sse,
    sse_tool_calls,
)


def _read_tool(calls: list) -> MainTool:
    async def run(args):
        calls.append(args)
        return "文件内容：hello"
    return MainTool(name="read_file", description="读文件",
                    parameters={}, execute=run, internal=True)


async def _drive_plan(responses, *, plan_mode: bool, tools=None):
    FakeAsyncClient.responses = list(responses)
    FakeAsyncClient.requests = []
    events = []
    with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", FakeAsyncClient):
        async for ev in model_driver.drive_model(
            model="m", api_key="k", system_prompt="sys", user_input="先给我一份计划",
            tools=tools or [], plan_mode=plan_mode,
        ):
            events.append(ev)
    return events, FakeAsyncClient.requests


def _pushbacks(requests: list) -> list:
    """被追加进 messages 的内部检查提示（去重）。

    必须去重：提示词一旦 append 进 messages 就会出现在**后续每一次**请求体里，
    按出现次数数会把「推回一次」数成 N 次。这里要判的是「推回了几次」，
    所以看的是**不同**提示的条数。
    """
    seen: list = []
    for req in requests:
        for m in (req.get("messages") or []):
            if m.get("role") != "user":
                continue
            content = str(m.get("content") or "")
            if "内部检查" in content and content not in seen:
                seen.append(content)
    return seen


class PlanGroundingNetTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_without_investigation_is_pushed_back_once(self):
        """一个文件都没读就交计划 → 必须被推回去勘查，且只推一次。"""
        events, reqs = await _drive_plan([
            [sse({"content": "## 计划\n直接开干就行。"}), DONE],   # 空壳计划
            [sse({"content": "## 计划（已核实）\n读过文件后的计划。"}), DONE],
        ], plan_mode=True)

        pushes = _pushbacks(reqs)
        self.assertEqual(len(pushes), 1, f"应当恰好推回一次，实际 {len(pushes)} 次")
        self.assertIn("一个文件都没读", pushes[0])
        final = [e for e in events if e.get("type") == "final"]
        self.assertTrue(final and "已核实" in final[0]["answer"])

    async def test_plan_with_investigation_is_not_pushed_back(self):
        """真读过文件就不该被打扰——误伤会让每个计划轮凭空多一轮。"""
        calls: list = []
        events, reqs = await _drive_plan([
            [sse_tool_calls([("c1", "read_file", {"path": "a.md"})]), DONE],
            [sse({"content": "## 计划\n基于 a.md 的实际内容。"}), DONE],
        ], plan_mode=True, tools=[_read_tool(calls)])

        self.assertEqual(_pushbacks(reqs), [], "读过文件了还推回去=误伤")
        self.assertEqual(len(calls), 1)

    async def test_non_plan_turns_are_never_touched(self):
        """这道闸只属于计划轮。普通问答被推回去问「你怎么不读文件」是纯粹的噪音。"""
        _events, reqs = await _drive_plan([
            [sse({"content": "你好，我能帮你做很多事。"}), DONE],
        ], plan_mode=False)
        self.assertEqual(_pushbacks(reqs), [])
