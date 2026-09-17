"""工具循环 telemetry、自动恢复与停滞 observation（2026-07-27）。

改之前熔断只有「轮次」一个维度，而一轮的成本方差是两个数量级（一轮 execute_in_sandbox 写 PPT
烧两万 token，一轮 list_files 烧两百）——40 轮的花费与耗时都不可预测；而且所有止损都
基于「失败」，**不报错的原地打转**（反复读同一批文件、每轮说点新话却不推进）一路空转
到轮次熔断才停，这是最贵的失败模式。

本测试锁住：
1. 累计输出 token 触顶 → 仍允许模型继续决策；
2. 墙钟触顶 → 同上；
3. 连续相同的一批调用 → 只记录 observation/提供换路事实，不强制收敛；
4. 单个工具卡死 → 到点中止并按普通失败回执处理，不挂死整个 Run。
"""
import asyncio
import json
import unittest
from unittest.mock import patch

from app.services.agent_harness import model_driver
from app.services.agent_harness.model_driver import LoopState, _budget_remaining
from app.services.chat.tools.base import MainTool
from tests.message_protocol import assert_all_requests_valid


def sse(delta: dict, usage: dict = None) -> str:
    payload = {"choices": [{"delta": delta}]}
    if usage:
        payload["usage"] = usage
    return "data: " + json.dumps(payload, ensure_ascii=False)


def sse_tool_calls(calls: list, usage: dict = None) -> str:
    frags = [
        {"index": i, "id": cid, "type": "function",
         "function": {"name": n, "arguments": json.dumps(a)}}
        for i, (cid, n, a) in enumerate(calls)
    ]
    return sse({"tool_calls": frags}, usage=usage)


DONE = "data: [DONE]"


class FakeStreamResponse:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class FakeAsyncClient:
    responses: list = []
    # 录下每次请求体：本文件里的每条用例都顺带校验 messages 序列合法（见 _drive）。
    # 停滞提示曾插在 assistant(tool_calls) 与 tool 结果之间导致网关 400，而当时的
    # 用例只看行为不看协议，所以没拦住。
    requests: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, json=None, headers=None):
        FakeAsyncClient.requests.append(json or {})
        return FakeStreamResponse(FakeAsyncClient.responses.pop(0))


async def _drive(**kwargs):
    events = []
    FakeAsyncClient.requests = []
    with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", FakeAsyncClient):
        async for ev in model_driver.drive_model(**kwargs):
            events.append(ev)
    assert_all_requests_valid(FakeAsyncClient.requests)
    return events


def _tool(name: str, calls: list, delay: float = 0.0) -> MainTool:
    async def run(args):
        calls.append(args)
        if delay:
            await asyncio.sleep(delay)
        return f"{name}-ok"
    return MainTool(name=name, description=name, parameters={},
                    execute=run, internal=True)


class BudgetDimensionTests(unittest.TestCase):
    """三维取最紧：谁先见底就用谁作判据。"""

    def test_tightest_dimension_wins(self):
        with patch.object(model_driver.settings, "TOOL_LOOP_TOKEN_BUDGET", 1000), \
             patch.object(model_driver.settings, "TOOL_LOOP_MAX_WALL_SECONDS", 100):
            # 轮次很宽松，token 已用九成 → 应判 token 最紧
            ratio, dim, desc = _budget_remaining(
                step=1, budget_total=40, out_tokens=900, elapsed_s=1.0)
            self.assertEqual(dim, "tokens")
            self.assertAlmostEqual(ratio, 0.1, places=3)
            # 墙钟见底 → 应判 wall 最紧
            ratio, dim, _ = _budget_remaining(
                step=1, budget_total=40, out_tokens=0, elapsed_s=99.0)
            self.assertEqual(dim, "wall")

    def test_disabled_dimensions_are_ignored(self):
        with patch.object(model_driver.settings, "TOOL_LOOP_TOKEN_BUDGET", 0), \
             patch.object(model_driver.settings, "TOOL_LOOP_MAX_WALL_SECONDS", 0):
            ratio, dim, _ = _budget_remaining(
                step=20, budget_total=40, out_tokens=10 ** 9, elapsed_s=10 ** 9)
            self.assertEqual(dim, "rounds", "关掉的维度不得参与判定")
            self.assertAlmostEqual(ratio, 0.5, places=3)


class TokenAndWallBreakerTests(unittest.IsolatedAsyncioTestCase):
    async def test_standard_mode_does_not_inject_plan_nudge(self):
        """Standard 只展示模型主动产生的计划，不强迫下一步 update_plan。"""
        executed: list = []
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "read_a", {"q": "事实"})]), DONE],
            [sse({"content": "已完成。"}), DONE],
        ]
        events = await _drive(
            model="m", api_key="k", user_input="制作一个多步报告",
            tools=[_tool("read_a", executed)],
        )
        self.assertEqual(len(executed), 1)
        self.assertTrue(next(e for e in events if e["type"] == "final"))
        self.assertFalse(
            any(
                "update_plan" in str(message.get("content") or "")
                for request in FakeAsyncClient.requests
                for message in request.get("messages") or []
                if message.get("role") in {"system", "user"}
            )
        )

    async def test_token_budget_is_observational_and_loop_continues(self):
        """累计输出 token 超过旧预算仍继续请求工具，不能 forced_final。"""
        executed: list = []
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "read_a", {"q": "1"})], usage={"completion_tokens": 150001}), DONE],
            [sse_tool_calls([("c2", "read_a", {"q": "2"})], usage={"completion_tokens": 1}), DONE],
            [sse({"content": "继续完成。"}), DONE],
        ]
        with patch.object(model_driver.settings, "TOOL_LOOP_TOKEN_BUDGET", 150000), \
             patch.object(model_driver.settings, "TOOL_LOOP_MAX_WALL_SECONDS", 0):
            events = await _drive(model="m", api_key="k", user_input="干活",
                                  tools=[_tool("read_a", executed)])
        self.assertEqual(len(executed), 2, "token 预算只记 telemetry，不得截断循环")
        self.assertTrue(all(req.get("tool_choice") != "none"
                            for req in FakeAsyncClient.requests[:2]))
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(final["answer"], "继续完成。")

    async def test_per_chunk_cumulative_usage_not_summed(self):
        """step 系渠道**每帧**都带「累计到当前」的 usage：逐帧相加会把一轮记成 N 倍。

        2026-07-30 真机事故：step-3.7-flash 含思考流的一轮 ~600 帧被记成 31.9 万
        token（预算 15 万），token 熔断在第 1 轮就误触发强制收敛 tool_choice=none，
        模型只好宣称「受工具轮次限制」——表象是「这个模型调用不了工具」。
        锁住：本轮计量取末帧值而非逐帧求和，预算未爆时第二轮必须仍能正常起工具。
        """
        executed: list = []
        FakeAsyncClient.responses = [
            # 第 1 轮：5 帧累计式 usage（100→500），真实输出 500，远低于 1000 预算；
            # 逐帧求和会记成 1500 直接爆表。
            [
                sse({"content": "查"}, usage={"completion_tokens": 100}),
                sse({"content": "一"}, usage={"completion_tokens": 200}),
                sse({"content": "下"}, usage={"completion_tokens": 300}),
                sse({"content": "。"}, usage={"completion_tokens": 400}),
                sse_tool_calls([("c1", "read_a", {"q": "1"})],
                               usage={"completion_tokens": 500}),
                DONE,
            ],
            [sse_tool_calls([("c2", "read_a", {"q": "2"})],
                            usage={"completion_tokens": 80}), DONE],
            [sse({"content": "两轮检索都完成了。"}), DONE],
        ]
        with patch.object(model_driver.settings, "TOOL_LOOP_TOKEN_BUDGET", 1000), \
             patch.object(model_driver.settings, "TOOL_LOOP_MAX_WALL_SECONDS", 0):
            events = await _drive(model="m", api_key="k", user_input="干活",
                                  tools=[_tool("read_a", executed)])
        self.assertEqual(len(executed), 2, "累计式 usage 不得把第 2 轮的工具禁掉")
        for req in FakeAsyncClient.requests[1:]:
            self.assertNotEqual(req.get("tool_choice"), "none",
                                "真实 500 token 被记成 1500 → 误触发强制收敛")
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(final["answer"], "两轮检索都完成了。")

    async def test_wall_clock_is_observational_and_loop_continues(self):
        """墙钟超过旧预算仍继续请求工具，不能 forced_final。"""
        executed: list = []
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "slow", {"q": "1"})]), DONE],
            [sse_tool_calls([("c2", "slow", {"q": "2"})]), DONE],
            [sse({"content": "继续执行。"}), DONE],
        ]
        with patch.object(model_driver.settings, "TOOL_LOOP_MAX_WALL_SECONDS", 1), \
             patch.object(model_driver.settings, "TOOL_LOOP_TOKEN_BUDGET", 0), \
             patch.object(model_driver.settings, "TOOL_CALL_TIMEOUT_SECONDS", 0):
            events = await _drive(model="m", api_key="k", user_input="干活",
                                  tools=[_tool("slow", executed, delay=1.1)])
        self.assertEqual(len(executed), 2, "墙钟预算只记 telemetry，不得截断循环")
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(final["answer"], "继续执行。")

    async def test_more_than_forty_rounds_is_not_a_terminal_condition(self):
        """超过旧 40 轮阈值仍可继续，且工具能力集合保持不变。"""
        executed: list = []
        rounds = 41
        FakeAsyncClient.responses = [
            [sse_tool_calls([(f"c{i}", "read_a", {"q": str(i)})]), DONE]
            for i in range(rounds)
        ] + [[sse({"content": "四十轮之后仍完成。"}), DONE]]
        with patch.object(model_driver.settings, "TOOL_LOOP_MAX_STEPS", 40), \
             patch.object(model_driver.settings, "TOOL_LOOP_TOKEN_BUDGET", 0), \
             patch.object(model_driver.settings, "TOOL_LOOP_MAX_WALL_SECONDS", 0):
            events = await _drive(model="m", api_key="k", user_input="干活",
                                  tools=[_tool("read_a", executed)])
        self.assertEqual(len(executed), rounds)
        self.assertTrue(all(req.get("tool_choice") != "none"
                            for req in FakeAsyncClient.requests[:rounds]))

        def provider_tool_names(request):
            return {
                str((item.get("function") or item).get("name") or "")
                for item in request["tools"]
            }

        self.assertEqual(
            provider_tool_names(FakeAsyncClient.requests[0]),
            provider_tool_names(FakeAsyncClient.requests[rounds - 1]),
        )
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(final["answer"], "四十轮之后仍完成。")


class StagnationTests(unittest.IsolatedAsyncioTestCase):
    async def test_identical_rounds_are_observation_not_terminal(self):
        """连续相同的一批调用不会单独产生终态或停用工具。"""
        executed: list = []
        same = [sse_tool_calls([("c1", "read_a", {"q": "同一个"})]), DONE]
        rounds = LoopState.STAGNATION_STOP_AT + 2
        FakeAsyncClient.responses = [list(same) for _ in range(rounds)] + [
            [sse({"content": "我卡住了，如实说明。"}), DONE]]
        with patch.object(model_driver.settings, "TOOL_LOOP_TOKEN_BUDGET", 0), \
             patch.object(model_driver.settings, "TOOL_LOOP_MAX_WALL_SECONDS", 0):
            events = await _drive(model="m", api_key="k", user_input="干活",
                                  tools=[_tool("read_a", executed)])
        self.assertEqual(len(executed), rounds)
        self.assertTrue(all(req.get("tool_choice") == "auto"
                            for req in FakeAsyncClient.requests[:rounds]))
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(final["answer"], "我卡住了，如实说明。")

    async def test_stagnation_does_not_inject_strategy_prompt(self):
        """重复 observation 只进入指标，不替模型注入换路或收尾指令。"""
        executed: list = []
        same = [sse_tool_calls([("c1", "read_a", {"q": "同一个"}),
                                ("c2", "read_b", {"q": "同一个"})]), DONE]
        # 重复计数 0/1/2：仍然允许模型继续决定下一步
        FakeAsyncClient.responses = [list(same) for _ in range(LoopState.STAGNATION_NUDGE_AT + 1)] + [
            [sse({"content": "换个思路：我直接基于已有结果作答。"}), DONE]]
        with patch.object(model_driver.settings, "TOOL_LOOP_TOKEN_BUDGET", 0), \
             patch.object(model_driver.settings, "TOOL_LOOP_MAX_WALL_SECONDS", 0):
            await _drive(model="m", api_key="k", user_input="干活",
                         tools=[_tool("read_a", executed), _tool("read_b", executed)])
        self.assertFalse(
            any(
                m.get("role") == "system"
                and "完全相同" in str(m.get("content") or "")
                for request in FakeAsyncClient.requests
                for m in request.get("messages") or []
            )
        )

    async def test_varying_rounds_are_not_flagged(self):
        """每轮参数在变=有推进，不得误判为停滞。"""
        executed: list = []
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "read_a", {"q": f"第{i}页"})]), DONE] for i in range(4)
        ] + [[sse({"content": "逐页读完了。"}), DONE]]
        with patch.object(model_driver.settings, "TOOL_LOOP_TOKEN_BUDGET", 0), \
             patch.object(model_driver.settings, "TOOL_LOOP_MAX_WALL_SECONDS", 0):
            events = await _drive(model="m", api_key="k", user_input="逐页读",
                                  tools=[_tool("read_a", executed)])
        self.assertEqual(len(executed), 4, "参数在变就是有推进，不该被拦")
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(final["answer"], "逐页读完了。")


class FetchToolWindowTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_precheck_rejects_model_invented_handle_before_public_step(self):
        issued = {"tool-result-" + ("a" * 32)}
        tool = model_driver._make_fetch_tool({}, {}, issued)

        self.assertIn(
            "禁止使用占位值或自行拼造",
            tool.precheck({"result_handle": "dummy_not_needed"}),
        )
        self.assertEqual(
            tool.precheck({"result_handle": next(iter(issued))}),
            "",
        )

    async def test_fetch_precheck_recovers_handles_already_visible_in_model_context(self):
        handle = "tool-result-" + ("b" * 32)
        visible = model_driver._result_handles_visible_to_model([
            {"role": "tool", "content": f'result_handle="{handle}"'},
        ])

        self.assertEqual(visible, {handle})

    async def test_fetch_window_cannot_be_truncated_again_by_main_loop(self):
        full = "x" * 20_000
        tool = model_driver._make_fetch_tool({}, {"internal-long": full})

        result = (await tool.execute({"call_id": "internal-long"})).model_content

        self.assertLess(len(result), 8_000,
                        "fetch_tool_result 自己超过主循环上限会再生一个 call_id，形成递归回取")
        self.assertIn('offset=7000', result)
        self.assertNotIn("结果较长已截断", result)


class ToolTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_hung_tool_is_aborted_as_failure(self):
        """卡死的工具到点中止并按普通失败回执处理，不挂死整个 Run。"""
        async def hang(args):
            await asyncio.sleep(30)
            return "never"

        stuck = MainTool(name="stuck", description="卡死", parameters={},
                         execute=hang, internal=True)
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "stuck", {"q": "1"})]), DONE],
            [sse({"content": "那个工具卡住了，我换个说法。"}), DONE],
        ]
        with patch.object(model_driver.settings, "TOOL_CALL_TIMEOUT_SECONDS", 1), \
             patch.object(model_driver.settings, "TOOL_LOOP_MAX_WALL_SECONDS", 0), \
             patch.object(model_driver.settings, "TOOL_LOOP_TOKEN_BUDGET", 0):
            events = await _drive(model="m", api_key="k", user_input="用那个工具",
                                  tools=[stuck])
        result = next(e for e in events if e["type"] == "tool_result")
        self.assertEqual(result["status"], "failed")
        self.assertIn("超过", result["preview"])
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual(final["answer"], "那个工具卡住了，我换个说法。")


if __name__ == "__main__":
    unittest.main()
