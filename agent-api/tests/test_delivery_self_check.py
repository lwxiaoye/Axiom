# -*- coding: utf-8 -*-
"""交付前收尾（2026-08-09：已有正文不再二次总结）。

原话：「模型不管是开没开计划模式，都会审查一遍之后，确认没有问题再交付给用户」。

第一次已经生成的正文是用户看到的事实答案；不能把它降级成过程说明后再让模型重写一次，
否则正文格式会在第二次总结时发生漂移。只有第一次确实为空时，才允许一次内部补齐。

三条边界都要有断言，因为每一条都对应一种真实代价：
① 只在动过手的回合触发 —— 否则每个纯问答都多一次 LLM 往返，延迟翻倍且无对账对象；
② 整轮最多一次 —— 自查不该引出自查循环；
③ 草稿诚实收尾已触发时不再自查 —— 那条路径已判定"接受不完美并如实告知"，
   再问"没做完就去修"会直接矛盾。
"""
from app.services.agent_harness.model_driver import LoopState, _turn_mutated
from app.services.chat.tools.base import MainTool


async def _noop(_args):
    return "ok"


def _tool(name: str, *, effect_scope: str = "none") -> MainTool:
    return MainTool(
        name=name,
        description=name,
        parameters={},
        execute=_noop,
        internal=True,
        effect_scope=effect_scope,
        idempotent=effect_scope == "none",
        resource_locks=(("resource",) if effect_scope != "none" else ()),
    )


def test_only_mutating_turns_get_checked():
    tools = {
        "writer": _tool("writer", effect_scope="user_files"),
        "reader": _tool("reader"),
    }
    assert _turn_mutated([{"name": "writer"}], tools) is True
    # 纯问答 / 纯检索：不触发（否则每个回答都翻倍延迟，而且没有对账对象）
    assert _turn_mutated([], tools) is False
    assert _turn_mutated([{"name": "reader"}], tools) is False


def test_mutation_signal_is_derived_from_tool_spec():
    writer = _tool("custom_writer", effect_scope="user_files")
    assert "mutate" in writer.spec.semantic_tags
    assert _turn_mutated([{"name": "custom_writer"}], {writer.name: writer}) is True


def test_state_flag_defaults_to_unchecked():
    assert LoopState().delivery_checked is False


def test_check_is_at_most_once_and_skipped_after_draft_closing():
    """完成验证统一裁决；Harness 不再注入交付前自查推回。"""
    import inspect

    from app.services.agent_harness import model_driver

    src = inspect.getsource(model_driver)
    assert "CompletionVerifier" in src
    assert "net_delivery_accept_first_answer" not in src
    assert "net_empty_tool_action" not in src


def test_first_answer_path_does_not_append_a_second_summary_prompt():
    """已有正文直接进入最终答案，避免二次总结改变格式。"""
    import inspect

    from app.services.agent_harness import model_driver

    src = inspect.getsource(model_driver)
    assert "net_delivery_accept_first_answer" not in src
    assert "net_refuse_text_stop_undelivered_file" not in src
    # Compatibility helpers are retained for old imports, but the active loop
    # must not call them as a delivery/strategy gate.
    assert src.count("should_defer_forced_final_for_missing_file(") == 1
    assert src.count("should_enter_execution_mode(") == 1
    assert "net_execution_mode" not in src
    assert "delivery_tokens_from_usage" in src
    assert "usage_delivery_tokens" in src
    # 旧的二次总结提示不得再出现在执行路径中。
    assert "内部自查，收尾前只做一次" not in src


# ---------- 这张网绝不能把一次成功变成硬失败（2026-07-28 P2） ----------
#
# 前三张网各自依赖某个罕见信号先亮，这一张对**每个动过手的回合**都跑，暴露面大一个量级。
# 而被推回去时那份正文走的是 commentary、**不进 answer_parts** —— 自查那一轮只要返回空正文
# 且无工具调用，`"".join(answer_parts)` 就是空串，直接撞上「模型返回了空回答」判整轮 failed；
# `spawn_partial_persist` 拿到的也是空串，那段本来完好的回答**一个字都不落库**。

import asyncio  # noqa: E402
import json  # noqa: E402
from unittest.mock import patch  # noqa: E402

from app.services.agent_harness import model_driver  # noqa: E402
from app.services.chat.tools.base import MainTool, ToolValue  # noqa: E402


def _sse(delta: dict) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


def _sse_calls(calls: list) -> str:
    return _sse({"tool_calls": [
        {"index": i, "id": cid, "type": "function",
         "function": {"name": n, "arguments": json.dumps(a)}}
        for i, (cid, n, a) in enumerate(calls)
    ]})


_DONE = "data: [DONE]"


class _FakeResp:
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


class _FakeClient:
    responses: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, _m, _u, json=None, headers=None):  # noqa: A002
        return _FakeResp(_FakeClient.responses.pop(0))

    async def post(self, *a, **k):
        raise AssertionError("本用例不该走非流式降级")


def _writer(calls: list) -> MainTool:
    async def run(args):
        calls.append(args)
        return ToolValue(
            model_content="（已保存到「我的文件」：季度报告.pptx）",
            artifacts=[{"filename": "季度报告.pptx", "file_id": "pptx-file-1"}],
        )
    return MainTool(
        name="bash",
        description="bash",
        parameters={},
        execute=run,
        internal=True,
        output_model=ToolValue,
        effect_scope="user_files",
        idempotent=False,
        resource_locks=("user-files",),
    )


async def _drive(responses):
    _FakeClient.responses = list(responses)
    events = []
    with patch("app.services.agent_harness.model_driver.httpx.AsyncClient", _FakeClient):
        async for ev in model_driver.drive_model(
            model="m", api_key="k", user_input="做份 PPT", tools=[_writer([])],
        ):
            events.append(ev)
    return events


def test_empty_self_check_round_keeps_the_original_answer():
    """自查轮一个字都没说（弱模型上很常见：它把「都对得上就直接给最终回答」当成"无需补充"）
    → 必须用被推回的原回答收尾，而不是抛「模型返回了空回答」把整轮判失败。"""
    events = asyncio.run(_drive([
        [_sse_calls([("c1", "bash", {"command": "python3 build.py"})]), _DONE],
        [_sse({"content": "季度报告.pptx 已生成并保存到「我的文件」。"}), _DONE],
        [_DONE],   # 自查轮：空正文、无工具调用
    ]))
    final = [e for e in events if e["type"] == "final"]
    assert final, "不能把一次成功的交付判成硬失败"
    assert final[0]["answer"] == "季度报告.pptx 已生成并保存到「我的文件」。"


def test_first_answer_is_kept_instead_of_being_revised_by_a_second_round():
    """第一次正文就是最终正文，后续模型响应不应覆盖它。"""
    events = asyncio.run(_drive([
        [_sse_calls([("c1", "bash", {"command": "python3 build.py"})]), _DONE],
        [_sse({"content": "做完了。"}), _DONE],
        [_sse({"content": "季度报告.pptx 已保存；封面页还缺一张配图。"}), _DONE],
    ]))
    final = [e for e in events if e["type"] == "final"]
    assert final[0]["answer"] == "做完了。"
    assert not any(
        e["type"] == "commentary" and e.get("text") == "做完了。"
        for e in events
    )
    assert len(_FakeClient.responses) == 1, "不应再发起第二轮模型总结"


def test_first_answer_does_not_trigger_a_second_tool_round():
    """第一次正文收尾后不再触发二次工具调用。"""
    events = asyncio.run(_drive([
        [_sse_calls([("c1", "bash", {"command": "python3 build.py"})]), _DONE],
        [_sse({"content": "初稿对过了，格式看着没问题。"}), _DONE],
        # 若旧逻辑仍存在，这里会触发第二次 bash；新契约下该响应不会被消费。
    ]))
    final = [e for e in events if e["type"] == "final"]
    assert final, "工具已成功后不应硬失败"
    answer = final[0]["answer"]
    assert answer == "初稿对过了，格式看着没问题。"


def test_state_carries_the_pushed_back_answer():
    from app.services.agent_harness.model_driver import LoopState as _LS
    assert _LS().checked_answer_fallback == ""
