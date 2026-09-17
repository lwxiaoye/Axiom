# -*- coding: utf-8 -*-
"""回退直答防假装护栏（2026-07-28，2026-08-04 修订假故障口径）。

真机事故（2026-07-27，deepseek-v4-pro）：工具循环因暂态故障回退普通问答后，模型
0 次真实调用却输出「搜索结果概要」。护栏禁止假装执行。
2026-08-04：禁止「工具系统不可用」假故障措辞；pure_qa 与故障回退分护栏。
"""
from app.services.chat.plain_turn import (
    FALLBACK_NO_TOOLS_GUARD,
    PURE_QA_NO_TOOLS_GUARD,
    apply_fallback_no_tools_guard,
)


def test_fallback_injects_guard():
    base = "你是主对话助手。"
    out = apply_fallback_no_tools_guard(base, True)
    assert out.startswith(base), "原提示词必须完整保留在前"
    assert FALLBACK_NO_TOOLS_GUARD in out
    assert "不能声称" in out or "绝不能声称" in out


def test_normal_plain_turn_untouched():
    base = "你是主对话助手。"
    assert apply_fallback_no_tools_guard(base, False) == base, (
        "非回退直答不得注入——工具循环开关关闭的部署形态里 plain 是常规直答"
    )


def test_guard_forbids_the_exact_observed_failure():
    """锚死真机看到的失败形态：编造搜索结果/谎称已保存文件，护栏文案必须逐一点名。"""
    assert "搜索结果" in FALLBACK_NO_TOOLS_GUARD
    assert "已保存文件" in FALLBACK_NO_TOOLS_GUARD


def test_guard_bans_fake_platform_outage_copy():
    assert "工具系统临时不可用" not in FALLBACK_NO_TOOLS_GUARD
    assert "工具系统不可用" not in FALLBACK_NO_TOOLS_GUARD


def test_intentional_pure_qa_uses_distinct_guard():
    base = "你是主对话助手。"
    out = apply_fallback_no_tools_guard(base, True, intentional_pure_qa=True)
    assert PURE_QA_NO_TOOLS_GUARD in out
    assert FALLBACK_NO_TOOLS_GUARD not in out
    assert "工具系统临时不可用" not in out
    assert "工具系统不可用" not in out


def test_pure_qa_guard_bans_mechanical_ack_openers():
    assert "机械确认" in PURE_QA_NO_TOOLS_GUARD or "收到" in PURE_QA_NO_TOOLS_GUARD
    assert "直接" in PURE_QA_NO_TOOLS_GUARD
