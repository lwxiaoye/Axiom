# -*- coding: utf-8 -*-
"""apply_context_budget 纯函数单测（2026-07-28）。

compaction 机制（context_service，§13）实现完整但此前零专项单测——384 行关键机制
只有 parity harness 间接引用。这里锁住窗口裁剪的三条行为契约：
① 预算内不动原文；② 有摘要时剔除已覆盖段并拼摘要块；③ 超预算也不能丢弃未摘要原文。
"""
from dataclasses import dataclass

from app.services.memory.context_service import apply_context_budget


@dataclass
class _Row:
    id: int
    role: str
    content: str


def _rows(n: int, size: int = 40) -> list:
    return [_Row(id=i + 1, role="user" if i % 2 == 0 else "assistant",
                 content=f"消息{i + 1}" + "字" * size) for i in range(n)]


def test_within_budget_keeps_all_rows_untouched():
    rows = _rows(4)
    kept, summary_block, dropped = apply_context_budget(rows, None, hard_cap_tokens=10_000)
    assert kept == rows
    assert summary_block == ""
    assert dropped == 0


def test_summary_covers_old_rows_and_injects_block():
    rows = _rows(4)
    summary = {"summary": "【目标】测试\n【已完成】前两条已覆盖", "covered_message_id": 2}
    kept, summary_block, dropped = apply_context_budget(rows, summary, hard_cap_tokens=10_000)
    assert [m.id for m in kept] == [3, 4], "covered_message_id 之前的消息应被摘要替代"
    assert "早前对话摘要" in summary_block and "【已完成】" in summary_block
    assert dropped == 0


def test_over_budget_retains_all_unsummarized_rows():
    rows = _rows(6, size=200)
    kept, _block, dropped = apply_context_budget(rows, None, hard_cap_tokens=120)
    assert dropped == 0
    assert kept == rows, "压缩失败不能抹掉未摘要的原始要求"


def test_replacement_history_is_used_verbatim():
    rows = _rows(4)
    exact = "【压缩检查点】目标：做PPT；已完成调研"
    summary = {
        "summary": "会被忽略的重包装",
        "covered_message_id": 2,
        "replacement_history": [{"role": "user", "content": exact}],
    }
    kept, summary_block, dropped = apply_context_budget(rows, summary, hard_cap_tokens=10_000)
    assert [m.id for m in kept] == [3, 4]
    assert summary_block == exact
    assert dropped == 0


def test_summary_only_replaces_covered_rows_even_over_budget():
    rows = _rows(8, size=200)
    summary = {"summary": "旧段摘要", "covered_message_id": 4}
    kept, summary_block, dropped = apply_context_budget(rows, summary, hard_cap_tokens=120)
    assert [m.id for m in kept] == [5, 6, 7, 8]
    assert summary_block
    assert kept[-1].id == 8
    assert dropped == 0
