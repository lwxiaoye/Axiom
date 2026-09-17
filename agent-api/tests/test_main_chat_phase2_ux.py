# -*- coding: utf-8 -*-
"""阶段 2：模型流式过程 + 答案去重。"""
import json

from app.services import sse_protocol
from app.services.agent_harness.orchestrator import _goal_next_action_text
from app.services.chat.turn_finalizer import collapse_exact_double_answer
from app.services.chat.plain_turn import PURE_QA_NO_TOOLS_GUARD, apply_fallback_no_tools_guard


def _frame(payload: str) -> dict:
    return json.loads(payload.split("data: ", 1)[1])


def test_agent_initial_progress_is_not_scripted():
    next_action = _goal_next_action_text(
        "帮我查一下今天北京天气",
        web_search=True,
        pure_qa=False,
    )
    # 脚本化开场下线：恒空，可见内容由模型产出
    assert next_action == ""


def test_double_answer_collapsed_for_persist():
    raw = "已新建 agent-diag-x.md，正文为 hello-x。已新建 agent-diag-x.md，正文为 hello-x。"
    # 精确半段重复
    half = "已新建 agent-diag-x.md，正文为 hello-x。"
    assert collapse_exact_double_answer(half + half) == half
    assert collapse_exact_double_answer(raw) == half or collapse_exact_double_answer(raw) == raw.strip()


def test_pure_qa_guard_not_fallback_outage():
    out = apply_fallback_no_tools_guard("sys", True, intentional_pure_qa=True)
    assert PURE_QA_NO_TOOLS_GUARD in out
    assert "工具系统不可用" not in out


def test_double_answer_collapse_whitespace_and_punct_variants():
    """P2.2：覆盖空白分隔与句末标点近似翻倍。"""
    base = "已新建 agent-diag-x.md，正文为 hello-x。"
    assert collapse_exact_double_answer(base + "\n" + base) == base
    assert collapse_exact_double_answer(base + " " + base) == base
    # 后半缺句号
    head = "文件已写入工作区。"
    assert collapse_exact_double_answer(head + head.rstrip("。")) == head

def test_double_answer_collapse_near_duplicate_sentences():
    """P2.2：相邻近似句 / 整块重放（矩阵 T2/T3 形态）。"""
    t2 = (
        "已新建 `live-m0-diag.md`，正文仅含 `hello-live`一行。"
        "已新建 `live-m0-diag.md`，正文仅含 `hello-live` 一行。"
        "已新建 `live-m0-diag.md`，正文仅含 `hello-live`一行，已保存到「我的文件」。"
    )
    out2 = collapse_exact_double_answer(t2)
    assert out2.count("已新建") == 1
    assert "我的文件" in out2
    assert "hello-live" in out2

    t3 = (
        "已完成。数字83810205已写入 live-bash-calc.txt，文件已保存到「我的文件」，内容回显确认无误。"
        "已完成。数字 83810205 已写入 live-bash-calc.txt，文件已保存到「我的文件」，内容回显确认无误。"
    )
    out3 = collapse_exact_double_answer(t3)
    assert out3.count("已完成") == 1
    assert "83810205" in out3
    assert "live-bash-calc.txt" in out3

    # 正常多句不被误伤
    ok = "这是正常的一段不会被误伤的答案。下一句继续展开细节。"
    assert collapse_exact_double_answer(ok) == ok


def test_double_answer_collapse_live_matrix_samples():
    """P2.2：覆盖 2026-08-05 live_matrix T2/T3 真机近义重放。"""
    t2 = (
        "已建好 `live-m0-diag.md`，正文内容为 `hello-live`，已保存到「我的文件」。"
        "已建好 `live-m0-diag.md`，正文内容为 `hello-live`，已保存到「我的文件」。"
        "已建好文件 `live-m0-diag.md`，正文内容为 `hello-live`，已保存到「我的文件」。"
        "如需继续诊断，随时可以告诉我。"
    )
    out2 = collapse_exact_double_answer(t2)
    assert out2.count("已建好") == 1
    assert "hello-live" in out2
    assert "我的文件" in out2
    assert "如需继续诊断" in out2

    t3 = (
        "已写入。live-bash-calc.txt内容为83810205（无换行符），并已保存到「我的文件」。"
        "已写入。live-bash-calc.txt 内容为 83810205（无换行符），并已保存到「我的文件」。"
        "已完成。数字83810205已通过 bash写入 live-bash-calc.txt，文件内容即为83810205（不含换行），并已保存到「我的文件」。"
    )
    out3 = collapse_exact_double_answer(t3)
    assert "83810205" in out3
    assert "live-bash-calc.txt" in out3
    assert "我的文件" in out3
    # 不应再出现两段几乎相同的「已写入」叙述
    assert out3.count("已写入") <= 1
    # 同句整块不重放
    assert out3.count("并已保存到「我的文件」") <= 1
