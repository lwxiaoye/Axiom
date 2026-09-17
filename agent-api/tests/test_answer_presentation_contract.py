"""主对话终答内容结构契约。"""

import inspect
from types import SimpleNamespace

from app.services.chat.turn_context_builder import _build_system_prompt
from app.services.chat import main_tool_turn, plain_turn
from app.services.chat.turn_finalizer import (
    persist_assistant_turn,
    scrub_internal_runtime_disclosure,
    scrub_public_runtime_text,
)
from app.services.agent_harness import model_driver


def test_final_answer_prioritizes_result_over_process_recap():
    prompt = _build_system_prompt()

    assert "最终回答只写用户需要的结果、关键依据、边界或未完成项" in prompt
    assert "不要复述过程区已经展示的动作" in prompt
    assert "不要写「我先…然后…最后…」" in prompt


def test_public_answer_keeps_internal_tool_receipts_private_by_default():
    prompt = _build_system_prompt()

    assert "stdout、stderr、命令文本、退出码、运行时或依赖版本" in prompt
    assert "只供内部判断，不得复制到公开过程说明或最终回答" in prompt
    assert "除非用户明确要求排查这些技术细节" in prompt
    assert "不要倾倒内部日志或原始回执" in prompt


def test_internal_runtime_commentary_and_final_reminders_are_filtered():
    commentary = "已确认 Node 和 Python 存在，npm 不在路径中；接下来走离线导出。"
    final = (
        "PPTX 和 PPTD 项目均已交付。\n\n"
        "需要的话可以运行 npx open-kimi-ppt-skill serve 打开本地编辑器。"
    )

    assert not model_driver.is_user_visible_commentary(commentary)
    assert model_driver.is_user_visible_commentary("版式已确定，接下来生成两页演示文稿。")
    plan_notes = (
        "计划需要调整以匹配实际工作流程。步骤2需要搜索照片和读取规范 (investigation)，"
        "步骤3需要写文件 (productive)，后续还需要验证和导出。让我先修正计划结构。"
    )
    assert not model_driver.is_user_visible_commentary(plan_notes)
    assert model_driver.should_hold_public_content_delta(plan_notes, forced_final=False)
    assert not model_driver.should_hold_public_content_delta("8", forced_final=False)
    assert not model_driver.should_hold_public_content_delta(
        plan_notes, forced_final=True,
    )
    assert scrub_internal_runtime_disclosure(final) == "PPTX 和 PPTD 项目均已交付。"
    assert scrub_internal_runtime_disclosure(final, allow_internal=True) == final


def test_answer_sized_tool_round_draft_is_not_public_commentary():
    draft = (
        "入学报到怎么办理\n\n"
        "报到时间、所需证件和现场缴费流程已经核对清楚。\n\n"
        "- 已缴费：领取宿舍钥匙后到学院报到。\n"
        "- 未缴费：先到学院报到，再缴费并安排床位。\n\n"
        "校园风光\n\n"
        "学校官网展示了教学楼、图书馆、实训公园、足球场和校园夜景。\n"
    ) * 4

    assert not model_driver.is_user_visible_commentary(draft, kind="tool_round")
    assert model_driver.is_user_visible_commentary(draft, kind="native_commentary")
    assert model_driver.is_user_visible_commentary(draft, kind="plan")


def test_plan_report_detection_keeps_full_markdown_out_of_short_intros():
    intro = "你有几份现成的 iPhone 相关图片，但 Word 文档的方向还需要对齐几个关键点："
    report = (
        "## 选购指南\n\n"
        "**Context**：把 iPhone 15/16/17 写成三到五页 Word。\n\n"
        "**Phase A**：整理参数与价格\n\n"
        "**验收**：含对比表，可直接发给用户。"
    )
    assert not model_driver.looks_like_plan_report(intro)
    assert model_driver.looks_like_plan_report(report)
    screenshot = (
        "Phase C: 文档生成与交付\n\n"
        "步骤 4 — 生成并交付 Word 文档\n\n"
        "使用 python-docx 生成带标题、段落和表格的 .docx。\n\n"
        "验收标准\n"
        "- 文件可打开且覆盖 6 个维度\n\n"
        "待你拍板\n"
        "无 —— 需求明确，可直接开始执行"
    )
    assert model_driver.looks_like_plan_report(screenshot)
    dump = (
        "当前搜索服务不可用，没法拉取最新评测数据，所以没法引用实时榜单。\n\n"
        "我仍可根据训练知识对比 Codex、Claude Agent、DeepSeek Coder、MarsCode。\n\n"
        "写正式文档前会再尝试搜索。现在拆解任务步骤："
    )
    assert not model_driver.looks_like_plan_report(dump)
    natural = (
        "## Agent 开发笔记\n\n"
        "给同事一份入门笔记，最后输出 Word。\n\n"
        "1. 先定读者和篇幅\n"
        "2. 列出核心概念与最小示例\n"
        "3. 用 python-docx 生成文档并保存到我的文件"
    )
    assert model_driver.looks_like_plan_report(natural)


def test_public_runtime_text_removes_compaction_tags_and_standalone_receipt_markers():
    text = (
        "版式已经确定。\n"
        "<thinking>Continuing after compaction with export</thinking>\n"
        "[stderr]\n"
        "导出继续完成。"
    )
    assert scrub_public_runtime_text(text) == "版式已经确定。\n\n导出继续完成。"


def test_public_runtime_text_removes_prefixed_sandbox_and_ppt_runtime_lines():
    text = (
        "已完成页面排版。\n"
        "/workspace/__main__.sh: line 1: npx: command not found\n"
        "[open-kimi-ppt] local WASM export: /workspace/outputs/deck.pptx\n"
        "open-kimi-ppt image export failed: PPTD manifest must contain pages\n"
        "产物已保存到我的文件。"
    )

    assert scrub_public_runtime_text(text) == "已完成页面排版。\n\n产物已保存到我的文件。"


def test_long_answer_uses_short_paragraphs_without_generic_heading_template():
    prompt = _build_system_prompt()

    assert "长回答先用一小段直接给结论（1–3 句）" in prompt
    assert "后续每段只讲一个信息任务，通常 2–4 句" in prompt
    assert "不要机械套用「结论 / 要点 / 说明」" in prompt
    assert "不要在结尾把开头结论换个说法再总结一次" in prompt


def test_long_answer_forbids_stuck_bold_sections_and_flattened_tables():
    prompt = _build_system_prompt()

    assert "**结论摘要**正文" in prompt
    assert "小标题，加粗内容后必须立即换行并空一行" in prompt
    assert "表头、分隔行和每条数据必须各自独占一行" in prompt
    assert "严禁把多行表格压成一行连续竖线" in prompt


def test_live_answer_pipeline_never_rewrites_markdown_structure():
    """模型流是排版事实源；完成/落库/展示边界不得再调用启发式结构整形。"""
    live_boundaries = (
        plain_turn.stream_llm_round,
        main_tool_turn.run_agent_turn,
        model_driver.drive_model,
        persist_assistant_turn,
    )
    for boundary in live_boundaries:
        assert "normalize_answer_structure(" not in inspect.getsource(boundary), boundary.__name__


def test_plain_turn_separates_transient_reasoning_summary_from_public_text():
    chunk = SimpleNamespace(
        content="",
        content_blocks=[
            {"type": "reasoning", "reasoning": "先核对条件"},
            {"type": "text", "text": "结论"},
        ],
    )

    assert plain_turn._chunk_stream_parts(chunk) == [
        ("reasoning", "先核对条件"),
        ("text", "结论"),
    ]


def test_plain_turn_keeps_chat_completion_text_compatibility():
    chunk = SimpleNamespace(content="普通回答", content_blocks=[])

    assert plain_turn._chunk_stream_parts(chunk) == [("text", "普通回答")]
