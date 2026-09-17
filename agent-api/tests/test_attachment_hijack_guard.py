# -*- coding: utf-8 -*-
"""点名文件时不许拿无关附件顶替修订目标（2026-07-29 对抗审计 P0）。

事故形态（数据破坏级）：用户说「帮我更新 summary.json 里的字段」，而会话里躺着一个
历史附件「2026年度财务预算.xlsx」→ `references_file_context` 因 named_files 命中而去
取最近附件，`contextual_file_targets[0]` **无条件**被当成 revision_target，提示词随即
命令模型「保持同一 file_id/同名写回」→ 用户那份无关文件被静默覆盖。

recent_file_targets 是按时间取的（它的 docstring 自己写着"不按文件名回查"），所以
这条路上必须有名字匹配闸。这也是同一天里「我堵了裸词入口、又用新代码开了一个」的形态：
上午把裸词表换成"回指 or 点名文件"，而"点名文件"这一支恰好把附件劫持又打开了。

这条路径此前**零覆盖**：唯一驱动真 stream_chat 的对拍测试把 recent_file_targets 桩成
`[]`，正好把这条路排除在所有断言之外。
"""
import pytest

from app.services.chat.turn_decision import named_files


def _match_targets(message: str, targets: list, current: list | None = None) -> dict | None:
    """复刻 chat_service 里那道名字匹配闸的判定（与生产同口径）。

    直接测生产函数需要驱动整条 stream_chat + 桩掉 DB/LLM；这里锁**判定规则本身**，
    另加一条源码断言保证生产代码里真的有这道闸（防"测试对了、生产没接"）。
    """
    named = named_files(message)
    if named and not current:
        lowered = {n.strip().lower() for n in named}
        matched = [
            t for t in targets
            if str(t.get("filename") or "").strip().lower() in lowered
        ]
        return matched[0] if matched else None
    return targets[0] if targets else None


ATTACHMENTS = [
    {"file_id": "f-budget", "filename": "2026年度财务预算.xlsx"},
    {"file_id": "f-notes", "filename": "会议记录.docx"},
]


def test_named_file_not_in_attachments_yields_no_target():
    """事故原文：点名 summary.json，附件里没有同名 → 绝不能拿预算表顶上。"""
    got = _match_targets("帮我更新 summary.json 里的字段", ATTACHMENTS)
    assert got is None, f"拿了无关附件当修订目标：{got}"


def test_named_file_present_in_attachments_is_matched():
    """阳性对照：名字真对得上时必须解析出来，否则这道闸把正常修订也堵死了。"""
    got = _match_targets("帮我更新 会议记录.docx 的第二段", ATTACHMENTS)
    assert got is not None and got["file_id"] == "f-notes"


def test_no_named_file_keeps_recent_attachment_behaviour():
    """没点名文件（纯回指"刚才那份"）时保持原行为：最近附件仍可作目标。"""
    got = _match_targets("把刚才那份改简洁一点", ATTACHMENTS)
    assert got is not None and got["file_id"] == "f-budget"


def test_user_selected_files_bypass_the_gate():
    """用户在 composer 里勾选是显式选择，不该被名字匹配闸拦住。"""
    current = [{"file_id": "f-picked", "filename": "用户勾的.md"}]
    got = _match_targets("帮我更新 summary.json 里的字段", current, current=current)
    assert got is not None and got["file_id"] == "f-picked"


def test_production_code_has_the_gate():
    """接线断言：生产代码里必须真有这道闸（剥注释后判，避免被注释里的说明骗）。

    上面四条锁的是判定规则；这条锁"规则被真正接进 chat_service"。两者缺一，
    就会出现"测试全绿而生产没这道闸"——本仓库反复出现的形态。
    """
    from tests.test_compaction_boundaries import _code_only

    import inspect

    from app.services.agent_harness import orchestrator as cs

    # 走 AST 剥注释**与 docstring**（2026-07-29）：文本搜索分不开代码/注释/docstring/
    # 字符串字面量——而"为什么需要这道闸"的说明恰恰逐字写着被查的符号名，
    # 用裸 getsource 或"去掉 # 行"都会数到说明自己。
    code = _code_only(inspect.getsource(cs.HarnessOrchestrator.stream_chat))
    assert "def stream_chat" in code, "剥完连签名都没了，先修那个辅助"
    assert "_named_files_in_message(message)" in code, "名字匹配闸不见了"
    assert "_matched[0]" in code and "not current_file_targets" in code, (
        "闸的两个关键部件（匹配结果、勾选豁免）必须都在"
    )
