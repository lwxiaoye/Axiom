# -*- coding: utf-8 -*-
"""三条 P0 修复的**真门禁**（2026-07-29 对抗审计第二轮的结论）。

背景值得写清楚：我为这三条 P0 写过 `test_loop_guard_interactions.py` 与
`test_compaction_boundaries.py`，**它们全是空转的**——对抗审计把每条缺陷注回去，
整仓 1477 条全绿。三处失效各有不同的骗法：

1. nonce 泄漏那条：只测了 `base.py` 的两个纯函数（`_with_validity_gate`/`_pop_validity_gate`），
   **从没触到 `drive_model` 里的实际顺序**；而且其中一条断言的是"顺序反了会坏"
   这个事实本身，把顺序改回坏的照样绿。
2. arg_error 豁免那条：源码文本锚点 `参数残缺**不是工具坏了**` **是一句注释**，
   `inspect.getsource` 带注释 → 它证明的是"注释还在"。把 `if arg_error:` 改成
   `if arg_error and False:`（注释原样留着）→ 全绿。
3. compaction 世代校验那条：只测了"`_bump_epoch` 会改数"，没测"数变了就丢弃结果"。
   删掉 `maybe_compact` 里的世代校验 → 全绿。

共同教训：**源码文本断言与纯函数断言都不能替代驱动真实执行路径**；而"测试没在测它
声称测的东西"只能靠变异注入发现，全绿从来不是证据。所以这个文件一律驱动
`drive_model` / `maybe_compact` 本体，并在每条上方写明"注入什么缺陷时我必须变红"。
"""
import json
import unittest
from unittest.mock import patch

from app.services.agent_harness import model_driver
from app.services.chat.tools.base import MainTool, _with_validity_gate
from app.services.memory import context_service as cs
from tests.test_loop_guard_batch2 import (
    DONE,
    FakeAsyncClient,
    _all_sent_text,
    _drive,
    _no_budget,
    sse,
    sse_tool_calls,
)


def _tool_result_texts() -> list[str]:
    """检查实际发送的回执；Responses 不使用 Chat 的 messages/role=tool。"""
    return [
        str(message.get("content") or message.get("output") or "")
        for request in FakeAsyncClient.requests
        for message in (request.get("messages") or request.get("input") or [])
        if message.get("role") == "tool" or message.get("type") == "function_call_output"
    ]


def _gate_tool(name: str, status: str, body: str = "产物已生成") -> MainTool:
    """回执**末尾**带 artifact_validity_gate 标记的工具（与 shell.py 的真实形态一致）。"""
    async def run(args):
        return _with_validity_gate(body, status)
    return MainTool(
        name=name, description=name, parameters={}, execute=run,
        internal=True, parallel_safe=False, effect_scope="user_files",
        semantic_tags=("artifact_producer",),
    )


class TestNoncePrivacyInRealLoop(unittest.IsolatedAsyncioTestCase):
    """① 防伪 nonce 绝不能流进模型上下文或 SSE。

    注入下述任一缺陷时本用例必须变红：
    - 把广撒网提醒/止损提示的追加挪到 `_pop_validity_gate` **之前**（原缺陷形态）
    - 去掉 `_pop_validity_gate` 调用
    """

    async def test_gate_marker_and_nonce_never_reach_model_or_sse(self):
        # bash 连调 15 次并全部带 failed 门禁：第 15 次会同时命中
        # 「广撒网提醒」与「门禁 failed」，正是原缺陷的触发点。
        calls = [(f"c{i}", "bash", {"command": f"echo {i}"}) for i in range(15)]
        FakeAsyncClient.responses = (
            [[sse_tool_calls([c]), DONE] for c in calls]
            + [[sse({"content": "都做完了。"}), DONE]]
        )
        with _no_budget(), patch.multiple(
            model_driver.settings, TOOL_LOOP_MAX_STEPS=40,
        ):
            events = await _drive(model="m", api_key="k", user_input="跑一批脚本",
                                  tools=[_gate_tool("bash", "failed")])

        # 1) 模型上下文：所有发给网关的消息正文里不许出现标记或 nonce
        sent = _all_sent_text()
        self.assertNotIn("artifact_validity_gate", sent,
                         "门禁标记流进了模型上下文——nonce 泄漏，模型可伪造 passed")

        # 2) SSE：tool_result / commentary 等任何帧都不许带标记
        blob = json.dumps(events, ensure_ascii=False, default=str)
        self.assertNotIn("artifact_validity_gate", blob,
                         "门禁标记流进了 SSE/落库轨迹")

        # 3) 阳性对照：门禁**确实被解析到了**（否则上面两条可能只是因为标记从未产生）
        self.assertTrue(
            "[内部检查：" in sent or "质量" in sent or "检查" in sent,
            "没有任何返工/检查痕迹——说明门禁根本没被解析，这条用例此刻是瞎的",
        )


class TestArgErrorExemptionInRealLoop(unittest.IsolatedAsyncioTestCase):
    """② 参数残缺不得把唯一执行器物理停用。

    注入下述缺陷时必须变红：把 `if arg_error:` 改成 `if arg_error and False:`
    （注释原样留着——原来的源码文本断言正是这样被骗过的）。
    """

    async def test_five_malformed_calls_do_not_disable_the_tool(self):
        # 5 个 arguments 非法 JSON 的 bash 调用（arg_error 路径），随后一个**合法**调用
        # 走「参数必须是 JSON 对象」那一支：合法 JSON 但不是对象，SSE 解析不受影响，
        # 主循环里 arg_error 照样置位（比造非法 JSON 更稳，不会连流式解析一起搞坏）。
        bad5 = sse({"tool_calls": [
            {"index": i, "id": f"b{i}", "type": "function",
             "function": {"name": "bash", "arguments": "[1,2,3]"}}
            for i in range(5)
        ]})
        ran: list = []

        async def run(args):
            ran.append(args)
            return "bash-ok"

        tool = MainTool(name="bash", description="bash", parameters={},
                        execute=run, internal=True, parallel_safe=False)
        # **一条消息里 5 个残缺调用**（真机原形态：模型输出被截断/分片丢失）。
        # 不能写成 5 轮——同签名连发 3 轮会先撞停滞检测，压根到不了停用阈值，
        # 那样测的就不是这条豁免了（这一步是照对抗审计给的原始触发序列还原的）。
        FakeAsyncClient.responses = (
            [[bad5, DONE]]
            + [[sse_tool_calls([("g1", "bash", {"command": "echo ok"})]), DONE]]
            + [[sse({"content": "完成。"}), DONE]]
        )
        with _no_budget(), patch.multiple(model_driver.settings, TOOL_LOOP_MAX_STEPS=40):
            events = await _drive(model="m", api_key="k", user_input="跑个脚本", tools=[tool])

        sent = _all_sent_text()
        # 关键断言：合法调用**真的执行了**（缺陷版里 bash 已被停用，永远执行不到）
        self.assertTrue(ran, "5 次参数残缺把 bash 物理停用了——本 Run 再也产不出任何产物")
        self.assertNotIn("本轮已停用", sent,
                         "参数残缺被当成工具故障并触发停用（处方是重发参数，不是换工具）")
        # 阳性对照：arg_error 回执确实出现过（否则这条用例没在测 arg_error 路径）
        self.assertIn("本次调用未执行", sent, "没有任何 arg_error 回执，用例此刻是瞎的")
        self.assertFalse(
            any(
                event.get("type") in {"tool_started", "tool_result"}
                and event.get("name") == "bash"
                and "本次调用未执行" in str(event.get("preview") or "")
                for event in events
            ),
            "参数/策略守卫拒绝只能回灌模型，不得制造用户可见工具失败",
        )


class TestCompactionEpochGuardReallyDiscards(unittest.IsolatedAsyncioTestCase):
    """③ 世代变了必须**丢弃**压缩结果，不只是"数会变"。

    注入下述缺陷时必须变红：删掉 `maybe_compact` 里的
    `if _epoch_of(thread_id) != epoch_at_start: return False`。
    """

    async def test_summary_is_discarded_when_epoch_bumped_midway(self):
        tid = "th-epoch-real"
        cs._summary_epochs.pop(tid, None)
        rows = [
            type("R", (), {"id": i, "role": "user" if i % 2 else "assistant",
                           "content": "内容" * 400})()
            for i in range(1, 12)
        ]
        wrote: list = []

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, *a, **k):
                return type("R", (), {"scalars": lambda self=None: type(
                    "S", (), {"all": lambda self=None: rows})()})()

            async def get(self, *a, **k):
                return None

            def add(self, obj):
                wrote.append(obj)

            async def commit(self):
                pass

        async def fake_post(*a, **k):
            # LLM 返回摘要的同一时刻，模拟用户编辑重发把摘要作废
            cs._bump_epoch(tid)
            return type("Resp", (), {
                "status_code": 200,
                "json": lambda self=None: {
                    "choices": [{"message": {"content": "【目标】压出来的摘要"}}]},
                "text": "",
            })()

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            post = staticmethod(fake_post)

        with patch.object(cs, "runtime_session", lambda: FakeSession), \
             patch.object(cs, "get_summary", side_effect=None) as gs, \
             patch.object(cs.httpx, "AsyncClient", FakeClient), \
             patch("app.core.database.async_session", FakeSession):
            gs.return_value = None
            ok = await cs.maybe_compact(tid, "m", "k", trigger_tokens=10)

        self.assertFalse(ok, "世代在生成期间被作废，压缩结果必须丢弃（否则已删分支复活成摘要）")
        self.assertEqual(wrote, [], "作废后仍写回了摘要行——幽灵摘要原状")


if __name__ == "__main__":
    unittest.main()


class TestCorrectionClosingIsNotDegraded(unittest.IsolatedAsyncioTestCase):
    """④ 模型用文字**更正**上一版时，不许把旧错版本复活并压在更正之前。

    对抗审计 P1：`_closing_degraded` 的长度断崖（new < 0.4*old）把"更正"误判成"退化"，
    `_merge_pushed_back_answer` 又把旧文当 base → 用户第一眼看到的正是被更正掉的错数字，
    更正句沉到几百字之后。而"用文字如实说明"是自查提示**自己许可**的路径。

    注入下述缺陷时必须变红：删掉 `_looks_like_correction` 那条短路。
    """

    def test_correction_closing_not_treated_as_degraded(self):
        from app.services.agent_harness.model_driver import _closing_degraded, _merge_pushed_back_answer
        old = "本季度营收 1200 万元。" + "详细分析" * 200          # 长报告，含错数字
        new = "更正：上文营收数字有误，实际为 780 万元，以此为准。"   # 短，但是更正

        self.assertFalse(_closing_degraded(new, old),
                         "更正被当成退化 → 错版本会被复活并放在最前面")
        # 阳性对照：真元陈述仍必须判退化（否则这条短路把判据整个放开了）
        self.assertTrue(_closing_degraded("研究报告已在上一轮完整交付。", old))
        # 阴性对照：纯粹变短的无实质收尾仍算退化
        self.assertTrue(_closing_degraded("好的。", old))

    def test_merge_puts_correction_after_but_keeps_it_visible(self):
        """真被判退化时的合并顺序不变（这条锁住我没有顺手改坏合并逻辑）。"""
        from app.services.agent_harness.model_driver import _merge_pushed_back_answer
        merged = _merge_pushed_back_answer("报告正文", "补充一句说明。")
        self.assertTrue(merged.startswith("报告正文"))
        self.assertIn("补充一句说明。", merged)


class TestTailNotesSurviveContextTruncation(unittest.IsolatedAsyncioTestCase):
    """⑤ 止损/停用提示不能被"长结果截断"从模型上下文里切掉。

    对抗审计 P1:止损提示追加在 result **尾部**,而入上下文时从**头部**截 8000 ——
    对失败输出天然很长的工具(browser_fetch 抓大页、bash 长 stderr、traceback),
    3→4→5 次的升级提示**全都是隐形的**,模型第一次知情要等到第 6 次被硬停。
    安全网坏掉时的表现恰恰是"一切正常",所以这条不修等于止损静默失效。

    注入下述缺陷时必须变红:把 `fed` 改回裸 `result[:8000]`。
    """

    async def test_stop_loss_notice_reaches_model_with_huge_error_body(self):
        long_err = "TracebackTracebackTraceback" * 600  # ≈16000 字符,远超 8000

        async def boom(args):
            from app.services.chat.tools.base import ToolSoftError
            raise ToolSoftError(long_err)

        tool = MainTool(name="browser_fetch", description="fetch", parameters={},
                        execute=boom, internal=True, parallel_safe=False)
        # 同工具连失败 3 次(每次换 url,绕开同参拦截)→ 第 3 次触发 SAME_ERROR_MAX 换路提示
        FakeAsyncClient.responses = (
            [[sse_tool_calls([(f"f{i}", "browser_fetch", {"url": f"https://x/{i}",
                                                          "prompt": "读正文"})]), DONE]
             for i in range(3)]
            + [[sse({"content": "抓不到,改用搜索。"}), DONE]]
        )
        with _no_budget(), patch.multiple(model_driver.settings, TOOL_LOOP_MAX_STEPS=20):
            await _drive(model="m", api_key="k", user_input="读 https://x.example 的这几个页面",
                         tools=[tool])

        tool_msgs = _tool_result_texts()
        self.assertTrue(tool_msgs, "没有任何 tool 回执进上下文,用例此刻是瞎的")
        joined = "\n".join(tool_msgs)
        # 阳性对照:确认错误正文确实很长(否则截断根本没发生,这条用例测不到东西)
        self.assertTrue(any(len(m) > 5000 for m in tool_msgs),
                        "错误正文不够长,截断没触发——用例前提失效")
        self.assertIn("同类错误已出现", joined,
                      "止损换路提示被长结果截断切掉了——模型永远看不到升级,止损静默失效")


class TestInternalNoticesStayOutOfSse(unittest.IsolatedAsyncioTestCase):
    """⑥ 内部止损/收敛指令不得进 SSE preview 与落库 trace。

    对抗审计 P2:这些是给**编排器和模型**看的控制指令("必须改变方法:换工具…"
    "本轮已停用" "先基于已有结果推进或收尾"),此前随 result 一起进 tool_result.preview
    → 执行卡输出面板、落库 trace,刷新回放照样显示。用户在界面上读到平台对模型下的命令,
    而那段代码上方的注释自己就写着「控制标记只供编排器消费…不能进入 SSE/历史」。

    注入下述缺陷时必须变红:把 `public_preview` 改回裸 `result[:2000]`。
    """

    async def test_stop_loss_notice_not_in_tool_result_preview(self):
        async def boom(args):
            from app.services.chat.tools.base import ToolSoftError
            raise ToolSoftError("这个网页取不到正文——常见原因是站点要求登录。")

        tool = MainTool(name="browser_fetch", description="fetch", parameters={},
                        execute=boom, internal=True, parallel_safe=False)
        FakeAsyncClient.responses = (
            [[sse_tool_calls([(f"f{i}", "browser_fetch", {"url": f"https://x/{i}",
                                                          "prompt": "读正文"})]), DONE]
             for i in range(3)]
            + [[sse({"content": "抓不到,改用搜索。"}), DONE]]
        )
        with _no_budget(), patch.multiple(model_driver.settings, TOOL_LOOP_MAX_STEPS=20):
            events = await _drive(model="m", api_key="k", user_input="读 https://x.example 的这几个页面",
                                  tools=[tool])

        previews = [str(e.get("preview") or "") for e in events
                    if e.get("type") == "tool_result"]
        self.assertTrue(previews, "没有 tool_result 帧,用例此刻是瞎的")
        blob = "\n".join(previews)
        self.assertNotIn("同类错误已出现", blob,
                         "内部止损指令进了 SSE preview——用户会读到平台对模型下的命令")
        self.assertNotIn("必须改变方法", blob)
        # 阳性对照:真实错误正文**必须还在**（剥的是尾注,不是把 preview 剥空）
        self.assertIn("取不到正文", blob, "把该留的错误信息也剥掉了,用户看不到失败原因")
        # 同一份文案确实进了模型上下文(证明它没被整体丢弃,只是不对外)
        sent = "\n".join(_tool_result_texts())
        self.assertIn("同类错误已出现", sent, "止损提示连模型也没收到,那是另一个 bug")
