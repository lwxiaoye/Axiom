# -*- coding: utf-8 -*-
"""2026-08-09：用户未明确要求文档时，默认对话交付、禁止主动落盘。

真机形态：用户只说「完成怪物猎人游戏的调研，看看值不值得完」→ 模型却
write_file 成「怪物猎人游戏调研报告.md」并保存到「我的文件」。
产品口径：第一优先级不是生成文件，而是对话作答；需要时再问是否做成文档。
"""
from app.services.agent_harness.model_driver import (
    _goal_oriented_plan_titles,
    _goal_prefers_chat_over_file_deliverable,
    _goal_wants_workspace_product,
    _user_requires_file_deliverable,
)


MH_RESEARCH = "我想完成怪物猎人游戏的调研，看看他们的游戏值不值得完"
EXPLICIT_REPORT = "帮我做一份怪物猎人调研报告，保存到我的文件"
EXPLICIT_MD = (
    "帮我联网调研 DeepSeek V4，整理成一份简短的选型建议 markdown，"
    "保存到我的文件，文件名：deepseek-v4-选型建议.md"
)


def test_plain_research_does_not_require_file():
    assert _user_requires_file_deliverable(MH_RESEARCH) is False
    assert _goal_wants_workspace_product(MH_RESEARCH) is False
    assert _goal_prefers_chat_over_file_deliverable(MH_RESEARCH) is True


def test_plain_research_plan_is_dialogue_not_write_file():
    titles = _goal_oriented_plan_titles(MH_RESEARCH)
    joined = " ".join(titles)
    assert "对话回复" in joined or "整理结论" in joined or "整理对比" in joined
    assert "写入文件" not in joined
    assert "保存并交付" not in joined
    assert "写入文件并交付" not in joined


def test_explicit_report_still_wants_file_and_plan_writes():
    assert _user_requires_file_deliverable(EXPLICIT_REPORT) is True
    assert _goal_prefers_chat_over_file_deliverable(EXPLICIT_REPORT) is False
    titles = _goal_oriented_plan_titles(EXPLICIT_REPORT)
    joined = " ".join(titles)
    assert "写入文件" in joined or "保存并交付" in joined or "写入文件并交付" in joined


def test_do_a_research_report_counts_as_file_even_without_save_phrase():
    """「做一份调研报告」本身就是文档意图，不依赖「保存到我的文件」。"""
    msg = "做一份怪物猎人调研报告"
    assert _user_requires_file_deliverable(msg) is True
    assert _goal_prefers_chat_over_file_deliverable(msg) is False


def test_explicit_save_md_not_chat_prefer():
    assert _user_requires_file_deliverable(EXPLICIT_MD) is True
    assert _goal_prefers_chat_over_file_deliverable(EXPLICIT_MD) is False


def test_system_prompt_has_chat_vs_file_rule():
    """基础提示词保留「对话内容与文件产物」的交付面区分，但由模型按目标决定是否落盘；
    旧的「对话结论 vs 文件产物 / 先问再写」关键词硬规则已随 Harness 规范退役。"""
    from pathlib import Path
    src = Path("app/services/chat/turn_context_builder.py").read_text(encoding="utf-8")
    assert "对话内容与文件产物" in src
    assert "模型根据用户目标决定是否检索、获取资源、写入工作区或只在对话中回答" in src
    assert "平台不按中文关键词强制下载、检索顺序、文件工具或附图形式" in src


def test_research_mode_prompt_compiles_report_instead_of_write_file():
    from app.services.chat.turn_decision import TurnDecision
    block = TurnDecision(
        intent="execute",
        authority="mutate",
        research_profile=True,
        reason_code="research",
    ).prompt_block()
    assert "阶段机" in block
    assert "交叉验证" in block
    assert "不要自己 write_file" in block
    assert "对话正文就是研究报告" in block or "写在对话正文里" in block
    assert "停止搜索并在对话中给出结论" in block


def test_no_keyword_hard_gate_blocks_write_file():
    """`chat_prefers_block_write` 硬闸已退役：Harness 不再凭中文关键词软拒 write_file
    （规范：「不再用中文关键词猜测是否要文件」）。`_goal_prefers_chat_over_file_deliverable`
    仅作为计划标题等启发式的输入保留，不得再长回一条阻断工具调用的闸。"""
    from pathlib import Path
    src = Path("app/services/agent_harness/model_driver.py").read_text(encoding="utf-8")
    assert "chat_prefers_block_write" not in src
    assert "_goal_prefers_chat_over_file_deliverable" in src
