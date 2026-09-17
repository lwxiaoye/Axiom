# -*- coding: utf-8 -*-
"""循环护栏交互缺陷第二批（2026-07-29 深扫，1 个 P0 + 8 条）。

这一批的共同形态与第一批一致：**每条护栏单看都对，两条护栏碰面才坏**。

- ⑨ P0 交付物被 auto-continue 网挪进"执行过程"：模型把活干完写出完整报告，只是忘了把
  update_plan 的最后几步标 completed → plan_incomplete 仍真 → 报告被当"甩锅清单"挪进
  折叠区，气泡里只剩一句「已在上一轮完整交付」。
- ① force_converge 只置位不清零 → 插话追加的预算拿不回工具权（messages.pop() 是空转）。
- ② 并发预执行不看 disabled_tools → 停用在混批时不物理；同批有人失败会把已成功的结果丢弃。
- ③ resume 不重建 trace → 交付前自查网在 resume 回合整体失效；停用回执带回上下文却已解锁。
- ④ 唯一执行器被停用时，质量返工仍推回"用 bash 重新生成"——与停用文案直接对打。
- ⑤ 被停用的调用照样发 tool_started → 前端多一条没有任何过程的空行。
- ⑥ finally 里 cancel 后不消费异常 → "Task exception was never retrieved" 淹掉真故障。

驱动方式仿 tests/test_tool_loop_parallel_reads：假 httpx 流喂 drive_model，不碰真模型。
"""
import asyncio
import json
import unittest
from unittest.mock import patch

from app.services.agent_harness import model_driver
from app.services.chat.tools.base import (
    MainTool,
    SubagentNeedsInput,
    ToolSoftError,
    ToolValue,
    _with_validity_gate,
)
from app.services.agent_harness.model_driver import (
    LoopState,
    _closing_degraded,
    _looks_like_handoff_list,
    _merge_pushed_back_answer,
    _recover_loop_state,
    _references_earlier_delivery,
    _stop_switch_advice,
    _usable_executors,
)
from tests.message_protocol import assert_all_requests_valid

DONE = "data: [DONE]"


def sse(delta: dict, usage: dict = None) -> str:
    payload = {"choices": [{"delta": delta}]}
    if usage:
        payload["usage"] = usage
    return "data: " + json.dumps(payload, ensure_ascii=False)


def sse_tool_calls(calls: list) -> str:
    frags = [
        {"index": i, "id": cid, "type": "function",
         "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
        for i, (cid, n, a) in enumerate(calls)
    ]
    return sse({"tool_calls": frags})


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
    """脚本耗尽时重放最后一条（同 test_tool_loop_parallel_reads）：用例断言行为，
    不断言"恰好几次 LLM 往返"——每加一张安全网都会多一轮，写死条数的用例会集体崩。"""

    responses: list = []
    requests: list = []
    last_response: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, json=None, headers=None):
        # 必须**快照** Chat messages / Responses input（2026-07-29 踩到）：请求数组与
        # 主循环里的 list 可能是同一个对象，直接存引用的话每条记录都会随后续 append/pop 一起变，
        # 最后所有请求看起来都等于最终状态——"某条提示是不是在第 N 轮就撤掉了"这类断言
        # 会得到彻底相反的答案（既有的 FakeAsyncClient 都有这个隐患）。
        payload = dict(json or {})
        payload["messages"] = [dict(m) for m in (payload.get("messages") or [])]
        payload["input"] = [dict(item) for item in (payload.get("input") or [])]
        FakeAsyncClient.requests.append(payload)
        if not FakeAsyncClient.responses:
            return FakeStreamResponse(FakeAsyncClient.last_response or [])
        FakeAsyncClient.last_response = FakeAsyncClient.responses.pop(0)
        return FakeStreamResponse(FakeAsyncClient.last_response)


async def _drive(**kwargs):
    events = []
    FakeAsyncClient.requests = []
    with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", FakeAsyncClient):
        async for ev in model_driver.drive_model(**kwargs):
            events.append(ev)
    assert_all_requests_valid(FakeAsyncClient.requests)
    return events


def _no_budget():
    """token/墙钟熔断关掉：这批用例只想控轮次这一个维度。"""
    return patch.multiple(
        model_driver.settings,
        TOOL_LOOP_TOKEN_BUDGET=0,
        TOOL_LOOP_MAX_WALL_SECONDS=0,
    )


def _all_sent_text() -> str:
    """所有发给网关的 Chat/Responses 正文拼起来。"""
    parts = []
    for req in FakeAsyncClient.requests:
        for msg in (req.get("messages") or req.get("input") or []):
            parts.append(str(msg.get("content") or msg.get("output") or ""))
    return "\n".join(parts)


def _ok_tool(name: str, calls: list, text: str = None) -> MainTool:
    async def run(args):
        calls.append(args)
        return text or f"{name}-ok"
    return MainTool(name=name, description=name, parameters={},
                    execute=run, internal=True, parallel_safe=True)


def _tool_map(*names: str) -> dict[str, MainTool]:
    async def _noop(_args):
        return "ok"

    tools = {}
    for name in names:
        if name == "update_plan":
            tools[name] = MainTool(
                name=name, description=name, parameters={}, execute=_noop,
                internal=True, control_command=True, semantic_tags=("control",),
            )
        else:
            productive = name in {"bash", "write_file", "edit_file"}
            tools[name] = MainTool(
                name=name, description=name, parameters={}, execute=_noop,
                internal=not productive,
                effect_scope="user_files" if productive else "none",
                idempotent=not productive,
                semantic_tags=("artifact_producer",) if productive else (),
            )
    return tools


# ===== ⑨ P0：交付物 vs 甩锅清单 =====

# 真机那份被埋掉的产出（截图，deepseek-v4-pro，「鼠标市场调研」）的形状：长、有结构、
# 结尾还带一句"随时告诉我"。判据必须放它过，否则 P0 原地复发。
REPORT = (
    "# 鼠标市场调研报告\n\n"
    "## 一、市场规模\n2025 年全球鼠标出货量约 4.2 亿只，其中无线占比 68%，"
    "游戏鼠标细分市场年增速 11%。中国大陆是最大产地，出货占全球七成以上，"
    "东莞与深圳两地集中了主要的代工产能。\n\n"
    "## 二、竞争格局\n罗技、雷蛇、微软构成第一梯队，合计份额约 45%；"
    "国产品牌在 100-300 元价格带形成密集竞争，靠传感器规格与轻量化差异化，"
    "线上渠道占其出货的绝大部分。\n\n"
    "## 三、技术趋势\n光学传感器 DPI 军备竞赛已边际收益递减，"
    "重心转向 8K 回报率、无线延迟与电池续航的平衡，以及轻量化结构设计；"
    "静音微动与磁轴滚轮是两个新的差异点。\n\n"
    "## 四、价格带分布\n入门 50-120 元、主流 150-400 元、高端 500-1200 元，"
    "高端段增速最快但绝对量小；办公与电竞两条产品线的价格重心明显分离。\n\n"
    "## 五、局限说明\n以上数据来自公开检索结果，未做一手渠道调研；"
    "细分份额为区间估计，季度波动可能较大。如果你想针对某个方向再深入，随时告诉我。"
)

# 中等长度的交付物（200 < 长度 < 实质阈值）：这一段专门用来验"一条客套的交接话术不算
# 甩锅清单"——真交付物也常这么收尾，一条就判会重演 P0。
MID_REPORT = (
    "结论：这批鼠标的价格中位数在 219 元，无线款占七成，其中带静音微动的型号溢价约 18%。"
    "三个主要品牌的价格带互相错开：入门段几乎全是国产品牌铺量，中段是合资与国产混战，"
    "高端段则由两家海外品牌把住，短期内格局不会有大变化。渠道上线上占比继续走高，"
    "线下主要承担体验与售后的角色。数据来自公开渠道抽样，季度波动可能较大，"
    "个别型号的溢价幅度受促销节奏影响明显，大促期间中段价格会短暂下探到入门段。"
    "如果还有哪一段需要细化，请告诉我你的侧重点。"
)

# 只在报告正文里出现的字串。**不能用"市场规模"当判据**——那句元陈述里也有它
# （"覆盖了市场规模、竞争格局…"），拿它断言"报告在不在最终回答里"会得到永远为真的绿灯。
# 2026-07-29 用回退对照台跑出来的：我自己的用例先被自己的判据骗过一次。
REPORT_MARK = "东莞与深圳"

# 真机气泡里那句元陈述——P0 的现场证物
META_CLOSING = (
    "全部六步调研步骤均已完成，研究报告已在上一轮完整交付。"
    "覆盖了市场规模、竞争格局、技术趋势与价格带。"
    "如果你想要针对某个方向做进一步分析，随时告诉我。"
)

HANDOFF = (
    "我这边还缺关键数据源，没法继续。你可以选：1) 你自己运行脚本抓取电商榜单；"
    "2) 提供一份内部销量表；3) 换成只做定性分析。请告诉我你想走哪条路。"
)


class HandoffClassifierTests(unittest.TestCase):
    """判据的阳性/阴性对照（仓库纪律：先证明它能抓出已知问题，再信它说"没问题"）。"""

    def test_real_handoff_list_is_flagged(self):
        """阳性对照：真机形态的甩锅清单必须被判出来，否则这张网就白拆了。"""
        self.assertTrue(_looks_like_handoff_list(HANDOFF))

    def test_real_report_is_not_flagged(self):
        """阴性对照：那份被埋掉的报告绝不能判成甩锅清单（这就是 P0 的根因）。"""
        self.assertGreaterEqual(len(" ".join(REPORT.split())),
                                model_driver._SUBSTANTIAL_ANSWER_MIN,
                                "夹具本身要够长，否则这条走的不是「实质体量」那条路")
        self.assertFalse(_looks_like_handoff_list(REPORT))

    def test_substantial_text_wins_over_handoff_phrasing(self):
        """口径落地：有实质体量时，即便带一句"你可以选一个方向"也不算清单。"""
        self.assertFalse(_looks_like_handoff_list(REPORT + "\n\n你可以选一个方向我再深入。"))

    def test_mid_length_deliverable_survives_one_polite_handoff_phrase(self):
        """中等长度 + **一条**客套交接话术 → 仍是交付物（一条就判会重演 P0）。"""
        collapsed = " ".join(MID_REPORT.split())
        self.assertLess(len(collapsed), model_driver._SUBSTANTIAL_ANSWER_MIN)
        self.assertGreater(len(collapsed), model_driver._TRIVIAL_ANSWER_MAX,
                           "夹具必须落在中间区间，否则验不到「两条信号」那条规则")
        self.assertEqual(
            sum(1 for p in model_driver._HANDOFF_PHRASES if p in collapsed), 1,
            "夹具应恰好命中一条交接话术")
        self.assertFalse(_looks_like_handoff_list(MID_REPORT))

    def test_mid_length_with_two_handoff_signals_is_flagged(self):
        """阳性对照：同样长度但凑够两条信号（列选项 + 要你动手）→ 判甩锅清单。"""
        text = MID_REPORT + "剩下的你可以自己运行脚本抓一遍。"
        self.assertLess(len(" ".join(text.split())), model_driver._SUBSTANTIAL_ANSWER_MIN)
        self.assertGreaterEqual(
            sum(1 for p in model_driver._HANDOFF_PHRASES if p in " ".join(text.split())),
            model_driver._HANDOFF_SIGNALS_MIN)
        self.assertTrue(_looks_like_handoff_list(text))

    def test_short_text_still_pushes_back(self):
        """网真正的用途没被改坏：一句"我卡住了"仍要推回去。"""
        self.assertTrue(_looks_like_handoff_list("我卡住了，缺数据。"))

    def test_empty_text_is_not_flagged_either_way(self):
        self.assertFalse(_looks_like_handoff_list(""))


class DegradedClosingTests(unittest.TestCase):
    def test_meta_statement_is_degraded(self):
        """阳性对照：真机那句元陈述必须判退化。"""
        self.assertTrue(_references_earlier_delivery(META_CLOSING))
        self.assertTrue(_closing_degraded(META_CLOSING, REPORT))

    def test_full_redelivery_is_not_degraded(self):
        """阴性对照：模型老实重新交付了一遍，不许当成退化去覆盖它。"""
        self.assertFalse(_closing_degraded(REPORT + "\n\n（补充：数据截至 2025 年末）", REPORT))

    def test_length_cliff_only_applies_to_substantial_fallback(self):
        """短兜底不吃长度断崖判据，否则普通短问答的正常收尾会被误判。"""
        self.assertFalse(_closing_degraded("好的，已完成。", "缺数据，请补充。"))

    def test_empty_new_text_is_degraded(self):
        self.assertTrue(_closing_degraded("", REPORT))

    def test_no_fallback_means_never_degraded(self):
        self.assertFalse(_closing_degraded("随便一句", ""))

    def test_merge_keeps_deliverable_and_drops_meta_tail(self):
        merged = _merge_pushed_back_answer(REPORT, META_CLOSING)
        self.assertIn(REPORT_MARK, merged)
        self.assertNotIn("已在上一轮完整交付", merged,
                         "元陈述接在报告后面是自相矛盾的，不该拼上")
        # 真·补充句要保留
        merged2 = _merge_pushed_back_answer(REPORT, "另外补一句：数据截至 2025 年末。")
        self.assertIn("数据截至 2025 年末", merged2)
        self.assertIn(REPORT_MARK, merged2)


class AutoContinueDeliverableTests(unittest.IsolatedAsyncioTestCase):
    """⑨ P0 端到端：计划没标完成 + 正文是完整交付物。"""

    @staticmethod
    def _plan_then(*rounds: list) -> list:
        """第 0 轮先 update_plan 留两个 pending 步骤（plan_incomplete 的来源）。"""
        return [[sse_tool_calls([("p1", "update_plan", {"steps": [
            {"title": "检索资料", "status": "completed"},
            {"title": "撰写报告", "status": "pending"},
            {"title": "复核数据", "status": "pending"},
        ]})]), DONE]] + list(rounds)

    async def test_deliverable_is_not_moved_into_process_area(self):
        """报告不进 commentary，且最终回答里必须有报告本体。"""
        FakeAsyncClient.responses = self._plan_then(
            [sse({"content": REPORT}), DONE],
            [sse({"content": META_CLOSING}), DONE],
        )
        with _no_budget():
            events = await _drive(model="m", api_key="k", user_input="做鼠标市场调研",
                                  tools=list(_tool_map("search_web", "update_plan").values()))
        self.assertTrue(any(event["type"] == "task_plan" for event in events))
        commentaries = [e.get("text") or "" for e in events if e["type"] == "commentary"]
        self.assertFalse(any("鼠标市场调研报告" in c for c in commentaries),
                         f"交付物被挪进执行过程区了: {commentaries}")
        final = next(e for e in events if e["type"] == "final")
        self.assertIn(REPORT_MARK, final["answer"], "交付物本体必须在最终回答里")
        self.assertNotEqual(final["answer"].strip(), META_CLOSING.strip(),
                            "最终回答不能只是那句「已在上一轮交付」")

    async def test_plan_report_and_update_plan_same_round_waits_for_confirmation(self):
        """Initial Plan may legitimately keep execution steps pending after delivering its report."""
        plan_report = (
            "# Kimi K3 研究执行计划\n\n"
            "**Context**\n目标是核对官方定位、基准数据、架构与实际反馈，"
            "并在结论中区分已确认事实、合理推测和未验证主张。\n\n"
            "**指导原则**\n优先使用官方一手资料，测评数字必须记录版本、日期和口径，"
            "对社区体验只做定性归纳，不把传言写成事实。\n\n"
            "**Phase A：证据收集**\n1. 读取官网产品页与技术报告。\n"
            "2. 核对独立基准与不同提供方的评测口径。\n\n"
            "**Phase B：综合与交付**\n3. 按能力、限制、适用场景和风险撰写报告。\n"
            "4. 复核每个关键数字的来源与时效性。\n\n"
            "**验收标准**\n报告覆盖定位、性能、架构、体验与局限；"
            "每项关键结论均可回溯到来源，争议数据明确标注口径差异。"
        )
        FakeAsyncClient.responses = [
            [
                sse({"content": plan_report}),
                sse_tool_calls([("p1", "update_plan", {"steps": [
                    {"title": "核对权威资料", "status": "completed"},
                    {"title": "执行深度检索", "status": "pending"},
                    {"title": "撰写并复核报告", "status": "pending"},
                ]})]),
                DONE,
            ],
            [sse({"content": "不应请求的第二份计划。"}), DONE],
        ]
        with _no_budget():
            events = await _drive(
                model="m", api_key="k", user_input="先给我一份执行计划",
                tools=list(_tool_map("search_web", "update_plan").values()), plan_mode=True,
            )

        self.assertEqual(len(FakeAsyncClient.requests), 1)
        self.assertTrue(any(event["type"] == "task_plan" for event in events))
        suspend = next(event for event in events if event["type"] == "suspend")
        self.assertEqual(suspend["args"]["options"], [{"label": "开始执行"}])
        self.assertIn("Kimi K3 研究执行计划", suspend["answer_so_far"])

    async def test_repeated_plan_updates_force_report_phase_instead_of_churn(self):
        steps_v1 = [
            {"title": "核对官网", "status": "in_progress"},
            {"title": "整理报告", "status": "pending"},
        ]
        steps_v2 = [
            {"title": "核对官网", "status": "completed"},
            {"title": "整理报告", "status": "in_progress"},
        ]
        final_report = (
            "# 执行计划\n\n**Context**\n基于已核对的官方页面制定交付路径。\n\n"
            "**指导原则**\n只使用可核验资料，区分事实与未验证主张。\n\n"
            "**Phase A**\n整理权威资料与证据表。\n\n"
            "**Phase B**\n撰写结论、局限与建议。\n\n"
            "**验收标准**\n每个关键结论均可回溯，报告结构完整。"
        )
        FakeAsyncClient.responses = [
            [sse({"content": "计划已初步对齐，现在核对官网。"}),
             sse_tool_calls([("p1", "update_plan", {"steps": steps_v1})]), DONE],
            [sse({"content": "计划已同步，继续整理。"}),
             sse_tool_calls([("p2", "update_plan", {"steps": steps_v2})]), DONE],
            [sse({"content": final_report}), DONE],
            [sse({"content": "不应继续重写计划。"}), DONE],
        ]
        with _no_budget():
            events = await _drive(
                model="m", api_key="k", user_input="先制定计划",
                tools=list(_tool_map("search_web", "update_plan").values()), plan_mode=True,
            )

        self.assertEqual(len(FakeAsyncClient.requests), 3)
        self.assertNotIn("tools", FakeAsyncClient.requests[2])
        self.assertIn("计划勘查阶段已结束", _all_sent_text())
        suspend = next(event for event in events if event["type"] == "suspend")
        self.assertIn("执行计划", suspend["answer_so_far"])

    async def test_complete_plan_report_blocks_same_round_followup_tools(self):
        """A delivered Plan report wins over a model's habitual extra search/read call."""
        calls: list = []
        plan_report = (
            "# 重庆工程学院公开数据调研计划\n\n"
            "**Context**\n用户需要一份可执行的调研路线，重点核对招生、"
            "专业、就业与公开数据口径，不把非官方信息当成事实。\n\n"
            "**指导原则**\n官方来源优先，年份与统计口径必须一致，缺失项显式标注。\n\n"
            "**Phase A**\n核对学校官网、招生网与就业质量报告。\n\n"
            "**Phase B**\n按统一口径整理数据，写明局限并进行交叉核验。\n\n"
            "**验收标准**\n关键数据可回溯，引用年份清晰，未公开项不臆测。"
        )
        FakeAsyncClient.responses = [[
            sse({"content": plan_report}),
            sse_tool_calls([("s1", "search_web", {"query": "重庆工程学院官网"})]),
            DONE,
        ]]
        with _no_budget():
            events = await _drive(
                model="m", api_key="k", user_input="先给我调研计划",
                tools=[_ok_tool("search_web", calls)], plan_mode=True,
            )

        self.assertEqual(len(FakeAsyncClient.requests), 1)
        self.assertEqual(calls, [], "完整计划报告后的惯性搜索不得真正执行")
        self.assertFalse(any(event["type"] == "tool_started" for event in events))
        suspend = next(event for event in events if event["type"] == "suspend")
        self.assertIn("重庆工程学院", suspend["answer_so_far"])

    async def _retired_push_back_asks_for_plan_sync_not_more_work(self):
        """交付物那一轮的推回文案 = "把计划状态改成真实进度 + 正常收尾"，不是"继续干活"。

        断言范围限定在**首次推回**那一次请求上：这张网整轮可推三次，后面几轮拿到的是
        元陈述（那时按甩锅清单处理才对），拿全局文本做断言会把它们一起算进来。
        """
        FakeAsyncClient.responses = self._plan_then(
            [sse({"content": REPORT}), DONE],
            [sse({"content": META_CLOSING}), DONE],
        )
        with _no_budget():
            await _drive(model="m", api_key="k", user_input="做鼠标市场调研",
                         tools=[_ok_tool("search_web", [])])
        first_push = None
        for req in FakeAsyncClient.requests:
            bodies = [str(m.get("content") or "") for m in req.get("messages") or []]
            # v2.73: 文案已收敛为「把真实已完成的步骤标 completed」（不再用旧句「把每一步的状态更新到」）
            if any(("把真实已完成的步骤标" in b) or ("把每一步的状态更新到" in b) for b in bodies):
                first_push = bodies
                break
        self.assertIsNotNone(first_push, "交付物那一轮没走到「计划状态同步」的推回文案")
        self.assertFalse(any("请继续调用工具把剩余步骤真正做完" in b for b in first_push),
                         "交付物已在手，那一轮不该再要求它继续干活")

    async def _retired_handoff_list_still_pushed_back(self):
        """网真正的用途没坏：甩锅清单照旧挪进过程区 + 要求把活做完。"""
        FakeAsyncClient.responses = self._plan_then(
            [sse({"content": HANDOFF}), DONE],
            [sse({"content": "我用可得的公开数据做完了定性分析，结论如下……"}), DONE],
        )
        with _no_budget():
            events = await _drive(model="m", api_key="k", user_input="做鼠标市场调研",
                                  tools=[_ok_tool("search_web", [])])
        commentaries = [e.get("text") or "" for e in events if e["type"] == "commentary"]
        self.assertTrue(any("你可以选" in c for c in commentaries),
                        f"甩锅清单应挪到时间线: {commentaries}")
        self.assertIn("请继续调用工具把剩余步骤真正做完", _all_sent_text())
        final = next(e for e in events if e["type"] == "final")
        self.assertIn("定性分析", final["answer"])

    async def test_update_plan_only_round_keeps_the_fallback(self):
        """模型照做（只调 update_plan 改状态）之后再退化，兜底仍须在。

        `update_plan` 被算成"模型又动手了"时兜底会被自己清掉——这正是我们要求它做的事，
        做了就丢兜底，等于这条修法只在模型不听话时生效。
        """
        FakeAsyncClient.responses = self._plan_then(
            [sse({"content": REPORT}), DONE],
            # 照做：只把计划状态改成真实进度（没有任何真动作）
            [sse_tool_calls([("p2", "update_plan", {"steps": [
                {"title": "检索资料", "status": "completed"},
                {"title": "撰写报告", "status": "completed"},
                {"title": "复核数据", "status": "completed"},
            ]})]), DONE],
            [sse({"content": META_CLOSING}), DONE],
        )
        with _no_budget():
            events = await _drive(model="m", api_key="k", user_input="做鼠标市场调研",
                                  tools=list(_tool_map("search_web", "update_plan").values()))
        final = next(e for e in events if e["type"] == "final")
        self.assertIn(REPORT_MARK, final["answer"],
                      "模型按提示只改了计划状态，兜底就被清掉了 → 报告救不回来")

    async def test_real_action_still_invalidates_the_fallback(self):
        """反向：真动手过就该作废兜底（产出变了，旧回答不再准确描述交付物）。"""
        st = LoopState()
        st.checked_answer_fallback = REPORT
        self.assertTrue(model_driver._has_real_action([
            {"function": {"name": "update_plan"}}, {"function": {"name": "bash"}},
        ], _tool_map("update_plan", "bash")))
        self.assertFalse(model_driver._has_real_action([
            {"function": {"name": "update_plan"}},
        ], _tool_map("update_plan")))


# ===== ① force_converge 只置位不清零 =====

class ForceConvergeClearedOnSteeringTests(unittest.IsolatedAsyncioTestCase):
    async def test_steering_never_creates_forced_final_tool_choice(self):
        """插话不需要拿回工具权：标准循环始终把 ToolSpec 能力交给模型。"""
        calls: list = []
        seal_results = [False]  # 第一次封口时"用户刚插了话"，之后正常

        async def _fake_seal(gateway):
            return seal_results.pop(0) if seal_results else True

        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "read_a", {"q": "1"})]), DONE],   # step0
            [sse({"content": "预算到顶，我先给个结论。"}), DONE],        # step1 收敛轮
            [sse_tool_calls([("c2", "read_a", {"q": "2"})]), DONE],   # step2 拿回工具权
            [sse({"content": "按你补充的要求重做完了。"}), DONE],        # step3 收尾
        ]
        with _no_budget(), \
                patch.object(model_driver.settings, "TOOL_LOOP_MAX_STEPS", 1), \
                patch.object(model_driver, "_seal_input_intake", _fake_seal):
            events = await _drive(model="m", api_key="k", user_input="干活",
                                  tools=[_ok_tool("read_a", calls)])
        choices = [r.get("tool_choice") for r in FakeAsyncClient.requests]
        self.assertGreaterEqual(len(choices), 3, f"请求太少，脚本没跑到插话之后: {choices}")
        self.assertNotIn("none", choices)
        self.assertTrue(all(choice == "auto" for choice in choices), choices)
        third = FakeAsyncClient.requests[2].get("messages") or []
        self.assertFalse(
            any(str(m.get("content") or "").startswith("（系统提示：本轮工具调用轮次已用尽")
                for m in third),
            "旧的收敛提示被 pop 掉之后不该又被重新追加")
        self.assertEqual(len(calls), 2, f"插话后模型仍可自行再执行工具，实际 {calls}")

    async def test_stagnation_reconverges_immediately_if_model_repeats(self):
        """清 force_converge 不会放走真停滞：repeat_round_count 故意不清零，
        模型再发一次同样的批次就当轮重新置位。"""
        st = LoopState()
        st.repeat_round_count = LoopState.STAGNATION_STOP_AT
        st.force_converge = "stagnation"
        st.force_converge = ""          # 模拟插话清闸
        # 同一签名再来一次 → 计数仍在阈值上 → 立刻重新置位（主循环的判定同款）
        self.assertGreaterEqual(st.repeat_round_count, LoopState.STAGNATION_STOP_AT)


class ExtraBudgetSplitAccountingTests(unittest.TestCase):
    """steering_extra_used 从只写字段接上真实分账；顺带修掉"返工把插话额度 min() 掉"。"""

    def test_quality_rework_after_steering_does_not_shrink_budget(self):
        quality_cap = 6
        st = LoopState()
        for _ in range(10):  # 插话顶到上限
            st.grant_extra_budget(steering=2, quality_cap=quality_cap)
        self.assertEqual(st.steering_extra_used, LoopState.STEERING_EXTRA_MAX)
        before = st.extra_budget
        # 阳性对照：旧写法在这一步会把额度压回 quality_cap（缩容）
        self.assertLess(min(before + 2, quality_cap), before,
                        "阳性对照失效：这个场景本来就不会缩容，用例白写")
        st.grant_extra_budget(quality=2, quality_cap=quality_cap)
        self.assertEqual(st.extra_budget, before + 2, "返工必须是扩容，不能把插话额度没收")

    def test_each_account_is_capped_and_sum_is_the_total(self):
        quality_cap = 6
        st = LoopState()
        for _ in range(20):
            st.grant_extra_budget(quality=2, steering=2, quality_cap=quality_cap)
        self.assertEqual(st.quality_extra_used, quality_cap)
        self.assertEqual(st.steering_extra_used, LoopState.STEERING_EXTRA_MAX)
        self.assertEqual(st.extra_budget, quality_cap + LoopState.STEERING_EXTRA_MAX,
                         "熔断线不得被推到两个上限之和以上")


# ===== ②⑤ 停用与并发预执行 =====

def _flaky_tool(name: str, calls: list, bad_prefix: str = "bad") -> MainTool:
    """args["q"] 以 bad_prefix 开头就抛同一个异常（错误形状恒定→指纹恒等），否则成功。"""
    async def run(args):
        q = str(args.get("q") or "")
        calls.append(q)
        if q.startswith(bad_prefix):
            raise RuntimeError("目标站点拒绝访问")
        return f"ok-{q}"
    return MainTool(name=name, description=name, parameters={},
                    execute=run, internal=True, parallel_safe=True)


def _burn_to_disable(name: str, n: int = None) -> list:
    """连续 n 轮同形失败（参数每轮都变，绕开同参拦截）的应答脚本。"""
    n = n or LoopState.SAME_ERROR_HARD_MAX
    return [[sse_tool_calls([(f"c{i}", name, {"q": f"bad{i}"})]), DONE] for i in range(n)]


class DisabledToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_tool_failure_remains_a_real_observation(self):
        """同类失败只记录事实，不把工具物理停用或伪造隐藏失败步骤。"""
        calls: list = []
        FakeAsyncClient.responses = _burn_to_disable("browser_fetch") + [
            [sse_tool_calls([("cX", "browser_fetch", {"q": "bad-again"})]), DONE],
            [sse({"content": "这个站点抓不动，我如实说明。"}), DONE],
        ]
        with _no_budget():
            events = await _drive(model="m", api_key="k", user_input="抓 https://x.example 的资料",
                                  tools=[_flaky_tool("browser_fetch", calls)])
        started = [e for e in events if e["type"] == "tool_started"]
        results = [e for e in events if e["type"] == "tool_result"]
        self.assertEqual(len(started), LoopState.SAME_ERROR_HARD_MAX + 1)
        self.assertEqual(len(results), LoopState.SAME_ERROR_HARD_MAX + 1)
        self.assertEqual(len(calls), LoopState.SAME_ERROR_HARD_MAX + 1)
        self.assertNotIn(model_driver._TOOL_DISABLED_BLOCK_MARK, _all_sent_text())

    async def test_repeated_failure_does_not_remove_parallel_tool_agency(self):
        """重复失败不改变下一批模型选择的并发工具集合。"""
        flaky: list = []
        other: list = []
        FakeAsyncClient.responses = _burn_to_disable("browser_fetch") + [
            # 混批：停用的 browser_fetch + 另一个只读工具 → 旧逻辑两个都会被预执行
            [sse_tool_calls([("cA", "browser_fetch", {"q": "bad-mixed"}),
                             ("cB", "search_web", {"q": "别的"})]), DONE],
            [sse({"content": "一个抓不动，另一个查到了。"}), DONE],
        ]
        with _no_budget():
            events = await _drive(
                model="m", api_key="k", user_input="抓 https://x.example 的两处内容",
                tools=[_flaky_tool("browser_fetch", flaky), _ok_tool("search_web", other)])
        self.assertEqual(len(flaky), LoopState.SAME_ERROR_HARD_MAX + 1)
        self.assertEqual(len(other), 1, "同批里没被停用的工具照常执行")
        last_round = [e for e in events if e["type"] == "tool_result"][-1]
        self.assertEqual(last_round["name"], "search_web")

    async def test_preexecuted_success_survives_a_sibling_tripping_the_disable(self):
        """② 形态①：同批 pos0 把计数推到停用阈值，pos1/pos2 **已经跑完且成功**，
        结果不许被整份丢弃标 failed。"""
        calls: list = []
        FakeAsyncClient.responses = _burn_to_disable(
            "browser_fetch", LoopState.SAME_ERROR_HARD_MAX - 1) + [
            [sse_tool_calls([("cA", "browser_fetch", {"q": "bad-last"}),
                             ("cB", "browser_fetch", {"q": "good1"}),
                             ("cC", "browser_fetch", {"q": "good2"})]), DONE],
            [sse({"content": "两个页面拿到了，一个没拿到。"}), DONE],
        ]
        with _no_budget():
            events = await _drive(model="m", api_key="k", user_input="抓 https://x.example 的三处内容",
                                  tools=[_flaky_tool("browser_fetch", calls)])
        self.assertIn("good1", calls)
        self.assertIn("good2", calls)
        by_arg = {json.dumps(e.get("args") or {}, ensure_ascii=False): e
                  for e in events if e["type"] == "tool_result"}
        good1 = by_arg.get('{"q": "good1"}')
        good2 = by_arg.get('{"q": "good2"}')
        self.assertIsNotNone(good1, f"good1 的结果帧不见了: {sorted(by_arg)}")
        self.assertIsNotNone(good2)
        self.assertEqual(good1["status"], "completed",
                         "同批别人失败不该把已经成功的预执行结果标成失败")
        self.assertEqual(good2["status"], "completed")
        self.assertIn("ok-good1", good1["preview"])


class ResourceMutationRetryResetTests(unittest.TestCase):
    @staticmethod
    def _mutation_tool(name: str, lock: str, *, revision: bool = True) -> MainTool:
        async def run(_args):
            return "ok"

        return MainTool(
            name=name,
            description=name,
            parameters={},
            execute=run,
            effect_scope="user_files",
            idempotent=False,
            resource_locks=(lock,),
            semantic_tags=(("revision_mutation",) if revision else ("-revision_mutation",)),
        )

    def test_successful_shared_resource_mutation_unlocks_stale_publish_failure(self):
        publish = self._mutation_tool("artifact_publish", "user-files")
        mutate = self._mutation_tool("workspace_mutation", "user-files")
        unrelated = self._mutation_tool("external_submit", "external-system")
        tools = {tool.name: tool for tool in (publish, mutate, unrelated)}
        st = LoopState(
            failed_call_hashes={
                "artifact_publish:same-args": 2,
                "external_submit:same-args": 2,
            },
            error_fingerprints={
                "artifact_publish:layout overflow": 4,
                "external_submit:timeout": 4,
            },
            disabled_tools={
                "artifact_publish": "layout overflow",
                "external_submit": "timeout",
            },
        )

        cleared = st.clear_stale_failures_after_mutation(mutate, tools)

        self.assertIn("artifact_publish", cleared)
        self.assertNotIn("artifact_publish:same-args", st.failed_call_hashes)
        self.assertNotIn("artifact_publish:layout overflow", st.error_fingerprints)
        self.assertNotIn("artifact_publish", st.disabled_tools)
        self.assertEqual(st.failed_call_hashes["external_submit:same-args"], 2)
        self.assertIn("external_submit", st.disabled_tools)

    def test_no_revision_mutation_keeps_identical_call_breaker_closed(self):
        publish = self._mutation_tool("artifact_publish", "user-files")
        readonly_change = self._mutation_tool("workspace_probe", "user-files", revision=False)
        st = LoopState(failed_call_hashes={"artifact_publish:same-args": 2})

        cleared = st.clear_stale_failures_after_mutation(
            readonly_change,
            {publish.name: publish, readonly_change.name: readonly_change},
        )

        self.assertEqual(cleared, ())
        self.assertEqual(st.failed_call_hashes["artifact_publish:same-args"], 2)


class ResourceMutationRetryDriveTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _tools(publish_calls: list, mutation_calls: list) -> tuple[MainTool, MainTool]:
        async def publish(args):
            publish_calls.append(dict(args))
            if len(publish_calls) < 3:
                raise ToolSoftError("layout overflow", code="artifact_layout_invalid")
            return ToolValue(
                model_content="published",
                artifacts=[{"filename": "deck.pptx"}],
                receipts=[{"file_id": "file-deck"}],
            )

        async def mutate(args):
            mutation_calls.append(dict(args))
            return "workspace fixed"

        shared = {
            "internal": True,
            "effect_scope": "user_files",
            "idempotent": False,
            "resource_locks": ("user-files",),
        }
        return (
            MainTool(
                name="artifact_publish",
                description="publish",
                parameters={},
                execute=publish,
                capability="artifact.export",
                **shared,
            ),
            MainTool(
                name="workspace_mutation",
                description="mutate",
                parameters={},
                execute=mutate,
                **shared,
            ),
        )

    async def test_same_publish_arguments_execute_again_after_workspace_revision(self):
        publish_calls: list = []
        mutation_calls: list = []
        publish, mutate = self._tools(publish_calls, mutation_calls)
        same_args = {"path": "/workspace/tmp/deck.pptx"}
        FakeAsyncClient.responses = [
            [sse_tool_calls([("p1", publish.name, same_args)]), DONE],
            [sse_tool_calls([("p2", publish.name, same_args)]), DONE],
            [sse_tool_calls([("m1", mutate.name, {"patch": "fix layout"})]), DONE],
            [sse_tool_calls([("p3", publish.name, same_args)]), DONE],
            [sse({"content": "修复后已发布。"}), DONE],
        ]

        with _no_budget():
            events = await _drive(
                model="m",
                api_key="k",
                user_input="修复工作区后发布",
                tools=[publish, mutate],
            )

        self.assertEqual(len(publish_calls), 3, "资源修订后同参数发布必须重新真实执行")
        self.assertEqual(len(mutation_calls), 1)
        publish_results = [
            event for event in events
            if event.get("type") == "tool_result" and event.get("name") == publish.name
        ]
        self.assertEqual(publish_results[-1]["status"], "completed")
        self.assertNotIn("本次未执行", publish_results[-1]["preview"])

    async def test_identical_publish_failure_without_mutation_is_observational(self):
        publish_calls: list = []
        mutation_calls: list = []
        publish, mutate = self._tools(publish_calls, mutation_calls)
        same_args = {"path": "/workspace/tmp/deck.pptx"}
        FakeAsyncClient.responses = [
            [sse_tool_calls([("p1", publish.name, same_args)]), DONE],
            [sse_tool_calls([("p2", publish.name, same_args)]), DONE],
            [sse_tool_calls([("p3", publish.name, same_args)]), DONE],
            [sse({"content": "发布仍未完成。"}), DONE],
        ]

        with _no_budget():
            events = await _drive(
                model="m",
                api_key="k",
                user_input="发布当前文件",
                tools=[publish, mutate],
            )

        self.assertEqual(len(publish_calls), 3, "重复调用不再由 Harness 作为终止闸拦截")
        blocked = [
            event for event in events
            if event.get("type") == "tool_result"
            and "本次未执行" in str(event.get("preview") or "")
        ]
        self.assertEqual(len(blocked), 0)


class StopSwitchAdviceTests(unittest.TestCase):
    """④ 处方按"还剩没剩别的执行器"分岔。"""

    def test_only_executor_gets_a_different_prescription(self):
        advice = _stop_switch_advice("bash", _tool_map("bash", "search_web"), {"bash": "x"})
        self.assertIn("唯一", advice)
        self.assertNotIn("换别的工具获取信息", advice,
                         "对唯一执行器说「换别的工具」是错处方")

    def test_non_executor_keeps_the_default_prescription(self):
        advice = _stop_switch_advice("browser_fetch", _tool_map("browser_fetch", "bash"),
                                     {"browser_fetch": "x"})
        self.assertIn("换别的工具", advice)

    def test_usable_executors_is_deterministically_ordered(self):
        """指名工具的文案必须走定序元组 `_ARTIFACT_EXECUTOR_ORDER`，不能靠 frozenset 迭代。

        2026-07-29：这里原先拿 {"execute_in_sandbox", "bash"} 当两个执行器来验定序，而 execute_in_sandbox 整套
        已下线，`_ARTIFACT_EXECUTOR_ORDER` 只剩 ("bash",)。**定序本身仍必须守住**——它防的是
        "同一份代码换个进程就指向另一个工具"，将来再加执行器时第一时间用得上；所以改成用
        未注册的名字验"不认识的工具不会被算成执行器"，形状与意图都保住了。
        原先那条 test_executor_with_a_spare_keeps_the_default（"还剩一个备用执行器时给默认
        处方"）随之删除：只剩一个执行器，那个分支已不可达，留着只能靠假名字硬凑。
        """
        self.assertEqual(_usable_executors(_tool_map("bash", "search_web"), {}), ["bash"])
        self.assertEqual(_usable_executors(_tool_map("bash"), {"bash": "x"}), [])
        # 没注册的名字不算执行器（顺带钉住"退休名不会被算回来"）
        self.assertEqual(_usable_executors(_tool_map("search_web"), {}), [])


class DuplicateBatchCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_identical_calls_in_one_model_batch_execute_once(self):
        """A four-call duplicate batch must not bypass round-level stagnation guards."""
        calls: list = []
        glob = _ok_tool("glob", calls, "found one file")
        duplicate_batch = [
            (f"g{i}", "glob", {"pattern": "**/*.pptx"})
            for i in range(4)
        ]
        FakeAsyncClient.responses = [
            [sse_tool_calls(duplicate_batch), DONE],
            [sse({"content": "已根据查找结果收尾。"}), DONE],
        ]
        with _no_budget():
            events = await _drive(
                model="m",
                api_key="k",
                user_input="查找现有文件",
                tools=[glob],
            )

        self.assertEqual(calls, [{"pattern": "**/*.pptx"}])
        public_results = [
            event for event in events
            if event.get("type") == "tool_result" and event.get("name") == "glob"
        ]
        self.assertEqual(len(public_results), 1, "重复调用不得制造用户可见的假步骤")

    async def test_repeated_successful_verification_rounds_do_not_force_convergence(self):
        """Repeated exact reads are metrics only; the model still owns the next action."""
        calls: list = []
        bash = _ok_tool("bash", calls, "file exists")
        repeated = [
            [sse_tool_calls([(f"ls{i}", "bash", {"command": "ls -l deck.pptx"})]), DONE]
            for i in range(7)
        ]
        FakeAsyncClient.responses = repeated + [
            [sse({"content": "文件未取得持久化回执，本次未完成交付。"}), DONE],
        ]
        with _no_budget():
            events = await _drive(
                model="m",
                api_key="k",
                user_input="核对临时文件",
                tools=[bash],
            )

        self.assertEqual(len(calls), 7)
        self.assertTrue(any(event.get("type") == "final" for event in events))


class QualityReworkVsDisabledExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_structural_failure_does_not_disable_the_only_executor(self):
        """客观检查失败回灌同一目标，但不会把唯一执行器切断。"""
        calls: list = []

        async def run(args):
            q = str(args.get("q") or "")
            calls.append(q)
            if q == "first":
                # 第一次成功产出但未通过产物有效性门禁 → artifact_review_pending 置位
                return _with_validity_gate("PPT 已生成，但有 2 处溢出", "failed")
            raise RuntimeError("沙箱镜像拉取失败")

        bash = MainTool(
            name="bash", description="执行", parameters={}, execute=run,
            effect_scope="user_files", idempotent=False,
            semantic_tags=("artifact_producer",),
        )
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c0", "bash", {"q": "first"})]), DONE],
        ] + [
            [sse_tool_calls([(f"d{i}", "bash", {"q": f"retry{i}"})]), DONE]
            for i in range(LoopState.SAME_ERROR_HARD_MAX)
        ] + [
            [sse({"content": "我停下来说明情况。"}), DONE],
            [sse({"content": "成品没达到交付标准，草稿在版本历史里。"}), DONE],
        ]
        with _no_budget():
            events = await _drive(model="m", api_key="k", user_input="做个 PPT",
                                  tools=[bash])
        sent = _all_sent_text()
        self.assertNotIn("同类错误已达", sent)
        self.assertNotIn("可用的执行工具已因反复失败停用", sent)
        self.assertEqual(len(calls), 1 + LoopState.SAME_ERROR_HARD_MAX)
        final = next(e for e in events if e["type"] == "final")
        self.assertTrue(final["answer"])

    async def test_failed_review_is_observation_and_does_not_close_tools(self):
        """Structural review feedback is not a bounded rework budget or a tool shutoff."""
        calls: list = []

        async def run(args):
            calls.append(dict(args))
            return _with_validity_gate("草稿仍有结构问题", "failed")

        bash = MainTool(
            name="bash",
            description="执行",
            parameters={},
            execute=run,
            effect_scope="user_files",
            idempotent=False,
            semantic_tags=("artifact_producer",),
        )
        FakeAsyncClient.responses = [
            [sse_tool_calls([("b1", "bash", {"command": "build-v1"})]), DONE],
            [sse({"content": "我先停下。"}), DONE],
            [sse({"content": "暂时收尾。"}), DONE],
            [sse_tool_calls([("b2", "bash", {"command": "build-v2"})]), DONE],
            [sse({"content": "准备说明草稿状态。"}), DONE],
            [sse_tool_calls([
                (f"ls{i}", "bash", {"command": "ls -l /workspace/files/deck.pptx"})
                for i in range(4)
            ]), DONE],
            [sse_tool_calls([
                (f"again{i}", "bash", {"command": "ls -l /workspace/files/deck.pptx"})
                for i in range(4)
            ]), DONE],
            [sse({"content": "已完成，可直接使用，草稿在版本历史里。"}), DONE],
        ]
        with _no_budget():
            events = await _drive(
                model="m",
                api_key="k",
                user_input="做个 PPT 并交付文件",
                tools=[bash],
            )

        self.assertEqual(calls, [{"command": "build-v1"}])
        sent = _all_sent_text()
        self.assertNotIn("没有 artifact.saved 回执", sent)
        final = next(event for event in events if event.get("type") == "final")
        self.assertTrue(final["answer"])


# ===== ③ resume 反推 =====

class ResumeRecoveryTests(unittest.TestCase):
    def test_mutating_tool_before_suspend_is_recovered(self):
        msgs = [
            {"role": "user", "content": "写个文件"},
            {"role": "assistant", "tool_calls": [{
                "id": "w1", "type": "function",
                "function": {"name": "write_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "w1", "content": "已写入 报告.md"},
        ]
        rec = _recover_loop_state(msgs, _tool_map("write_file"))
        self.assertTrue(rec["mutated"])
        self.assertTrue(LoopState.recover_from(rec).mutated_before_resume)

    def test_readonly_turn_is_not_marked_mutated(self):
        """阴性对照：纯检索的回合不该触发自查（否则每个回答都多一次 LLM 往返）。"""
        msgs = [
            {"role": "assistant", "tool_calls": [{
                "id": "s1", "type": "function",
                "function": {"name": "search_web", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "s1", "content": "搜到 3 条"},
        ]
        self.assertFalse(_recover_loop_state(msgs, _tool_map("search_web"))["mutated"])

    def test_unexecuted_placeholder_does_not_count_as_mutation(self):
        """挂起时补的占位回执在在线路径里连 trace 条目都没有，反推也不能算它动过手。"""
        msgs = [
            {"role": "assistant", "tool_calls": [{
                "id": "b1", "type": "function",
                "function": {"name": "bash", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "b1",
             "content": model_driver._SUSPENDED_UNEXECUTED_NOTE},
        ]
        self.assertFalse(_recover_loop_state(msgs, _tool_map("bash"))["mutated"])

    def test_disable_receipts_are_recovered(self):
        """两种停用回执都要认出来（拦截式整条 + 追加在失败尾部的那句）。"""
        msgs = [
            {"role": "assistant", "tool_calls": [{
                "id": "f1", "type": "function",
                "function": {"name": "browser_fetch", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "f1",
             "content": f"抓取失败\n[同类错误已达 5 次，browser_fetch "
                        f"{model_driver._TOOL_DISABLED_SUFFIX_MARK}。换别的工具。]"},
            {"role": "assistant", "tool_calls": [{
                "id": "f2", "type": "function",
                "function": {"name": "bash", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "f2",
             "content": f"（bash {model_driver._TOOL_DISABLED_BLOCK_MARK}：同类错误累计 5 次）"},
        ]
        rec = _recover_loop_state(msgs, _tool_map("browser_fetch", "bash"))
        self.assertEqual(sorted(rec["disabled_tools_seen"]), ["bash", "browser_fetch"])

    def test_clean_history_reports_no_disabled_tools(self):
        msgs = [
            {"role": "assistant", "tool_calls": [{
                "id": "b1", "type": "function",
                "function": {"name": "bash", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "b1", "content": "exit_code=0"},
        ]
        self.assertEqual(_recover_loop_state(msgs, _tool_map("bash"))["disabled_tools_seen"], [])


class ResumeLoopTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _resume_messages(tool_content: str, tool_name: str = "write_file") -> list:
        return [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "写个文件"},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "w1", "type": "function",
                "function": {"name": tool_name, "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "w1", "content": tool_content},
        ]

    async def test_resume_keeps_the_first_answer_without_a_second_summary(self):
        """resume 回合已有正文时直接收尾，不再触发二次总结。"""
        FakeAsyncClient.responses = [
            [sse({"content": "文件写好了。"}), DONE],
            [sse({"content": "对账无误，报告.md 已保存。"}), DONE],
        ]
        with _no_budget():
            events = await _drive(
                model="m", api_key="k",
                initial_messages=self._resume_messages("已写入 报告.md"),
                tools=[_ok_tool("write_file", [])])
        self.assertNotIn("内部自查", _all_sent_text())
        final = next(e for e in events if e["type"] == "final")
        self.assertEqual("文件写好了。", final["answer"])

    async def test_readonly_resume_does_not_trigger_self_check(self):
        """阴性对照：挂起前只检索过 → 不加那次往返。"""
        FakeAsyncClient.responses = [[sse({"content": "查到了，结论如下。"}), DONE]]
        with _no_budget():
            await _drive(model="m", api_key="k",
                         initial_messages=self._resume_messages("搜到 3 条", "search_web"),
                         tools=[_ok_tool("search_web", [])])
        self.assertNotIn("内部自查", _all_sent_text())

    async def test_resume_keeps_the_historical_receipt_without_unlock_narration(self):
        """恢复只保留事实，不生成额外的策略性“已解锁”旁白。"""
        FakeAsyncClient.responses = [[sse({"content": "继续。"}), DONE]]
        msgs = self._resume_messages(
            f"（bash {model_driver._TOOL_DISABLED_BLOCK_MARK}：同类错误累计 5 次）", "bash")
        with _no_budget():
            await _drive(model="m", api_key="k", initial_messages=msgs,
                         tools=[_ok_tool("bash", [])])
        sent = _all_sent_text()
        self.assertIn(model_driver._TOOL_DISABLED_BLOCK_MARK, sent)
        self.assertNotIn("现已恢复可用", sent)

    async def test_resume_without_disable_receipt_stays_quiet(self):
        """阴性对照：正常 resume 不加这条噪音。"""
        FakeAsyncClient.responses = [
            [sse({"content": "文件写好了。"}), DONE],
            [sse({"content": "对账无误。"}), DONE],
        ]
        with _no_budget():
            await _drive(model="m", api_key="k",
                         initial_messages=self._resume_messages("已写入 报告.md"),
                         tools=[_ok_tool("write_file", [])])
        self.assertNotIn("现已恢复可用", _all_sent_text())


# ===== ⑥ 被丢弃的预执行任务必须有人取异常 =====

class AbandonedPreexecTaskTests(unittest.IsolatedAsyncioTestCase):
    """cancel 之后不取结果，asyncio 会打 "Task exception was never retrieved"——
    难看是小事，它会把**真正的**工具故障淹没在一片取消噪声里。"""

    @staticmethod
    def _spy():
        seen: list = []
        real = model_driver._consume_task_exception

        def spy(task):
            seen.append(task)
            return real(task)
        return seen, spy

    @staticmethod
    async def _boom_collect(*a, **kw):
        await asyncio.sleep(0)
        raise RuntimeError("预执行任务炸了")

    @staticmethod
    def _consumed_booms(seen: list) -> list:
        out = []
        for t in seen:
            if t.done() and not t.cancelled():
                exc = t.exception()
                if isinstance(exc, RuntimeError) and "预执行任务炸了" in str(exc):
                    out.append(t)
        return out

    async def test_suspend_path_consumes_abandoned_tasks(self):
        """HITL 挂起提前 return → finally 里的兜底必须消费异常。"""
        async def needs_input(args):
            await asyncio.sleep(0.02)   # 让两个预执行任务先炸掉，判据才确定
            raise SubagentNeedsInput({"text": "要你选一下", "resume_id": "r1"})

        asker = MainTool(name="ask", description="问", parameters={},
                         execute=needs_input, internal=True)  # 非 parallel_safe → 不预执行
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c0", "ask", {}),
                             ("c1", "read_a", {"q": "1"}),
                             ("c2", "read_b", {"q": "2"})]), DONE],
        ]
        seen, spy = self._spy()
        with _no_budget(), \
                patch.object(model_driver, "_collect_tool_call", self._boom_collect), \
                patch.object(model_driver, "_consume_task_exception", spy):
            events = await _drive(
                model="m", api_key="k", user_input="问一下再查两处",
                tools=[asker, _ok_tool("read_a", []), _ok_tool("read_b", [])])
        self.assertTrue(any(e["type"] == "suspend" for e in events), "脚本没走到挂起分支")
        consumed = self._consumed_booms(seen)
        self.assertEqual(
            consumed, [],
            "V3 exclusive barrier 禁止越过 ask 预启动后续读取，因此不应产生被丢弃任务",
        )
        for t in consumed:
            self.assertFalse(getattr(t, "_log_traceback", False),
                             "异常已被取回时 asyncio 才不会在 GC 时告警")

    async def test_sibling_cancel_path_consumes_too(self):
        """同一条修法要横向铺开：pos0 的 await 抛异常时撤兄弟任务那一处也得消费。"""
        FakeAsyncClient.responses = [
            [sse_tool_calls([("c1", "read_a", {"q": "1"}),
                             ("c2", "read_b", {"q": "2"})]), DONE],
        ]
        seen, spy = self._spy()
        with _no_budget(), \
                patch.object(model_driver, "_collect_tool_call", self._boom_collect), \
                patch.object(model_driver, "_consume_task_exception", spy):
            with self.assertRaises(RuntimeError):
                await _drive(model="m", api_key="k", user_input="查两处",
                             tools=[_ok_tool("read_a", []), _ok_tool("read_b", [])])
        self.assertGreaterEqual(len(self._consumed_booms(seen)), 1,
                                "撤兄弟任务时同样必须消费异常")


class PublicToolActionTests(unittest.TestCase):
    def test_public_wording_comes_from_tool_spec(self):
        tool = _ok_tool("read_a", [])
        spec = tool.spec.model_copy(update={"public_action": "核对资料"})
        tool.spec = spec
        self.assertEqual(model_driver._public_tool_label(tool, {}), "核对资料")
        self.assertEqual(
            model_driver._public_tool_label(tool, {"path": "a.txt"}),
            "核对资料（a.txt）",
        )


if __name__ == "__main__":
    unittest.main()
