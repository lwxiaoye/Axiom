# -*- coding: utf-8 -*-
"""v2.76 contract baseline: deliver early and hide process scripts."""
from __future__ import annotations

from app.services.files.deliverable import is_deliverable
from app.services.sandbox.visual_review import _merge_into_review


def test_objective_render_error_is_reported_without_scores():
    entry = {"review": {"status": "passed", "summary": "结构可打开"}}
    verdict = {
        "passed": False,
        "requirement_checks": [],
        "issues": [{"severity": "error", "message": "第 2 页标题溢出", "fix": "缩短标题"}],
        "next_actions": ["修正第 2 页标题"],
    }
    _merge_into_review(entry, verdict)
    assert entry["review"]["status"] == "failed"
    assert "第 2 页标题溢出" in (entry["review"].get("summary") or "")
    assert not any(key.endswith("_score") for key in entry["review"]["quality"])


def test_clean_render_check_has_no_quality_score():
    entry = {"review": {"status": "passed", "summary": "结构可打开"}}
    verdict = {
        "passed": True,
        "requirement_checks": [],
        "issues": [],
        "next_actions": [],
    }
    _merge_into_review(entry, verdict)
    summary = entry["review"].get("summary") or ""
    assert "未发现明确错误" in summary
    assert not any(key.endswith("_score") for key in entry["review"]["quality"])


def test_process_script_not_deliverable():
    assert is_deliverable("gen_agent_brief.py", "generated") is False
    assert is_deliverable("Agent市场简报.docx", "generated") is True
    assert is_deliverable("deck.pptx", "generated") is True


def test_budget_observation_does_not_create_product_termination():
    src = open("app/services/agent_harness/model_driver.py", encoding="utf-8").read()
    assert "budget_observation" in src
    assert "product_block_rework_after_deliverable" not in src
    assert "net_product_delivered_force_final" not in src


def test_emit_file_meta_skips_process_files():
    src = open("app/services/chat/tools/paths.py", encoding="utf-8").read()
    assert "process_file" in src
    assert "不进 files 元数据" in src or "process_file" in src

def test_product_bash_thrash_gates_present_v277():
    src = open("app/services/agent_harness/model_driver.py", encoding="utf-8").read()
    assert "stagnation_observation" in src
    assert "product_block_search_capabilities" not in src
    assert "product_block_bash_verify_thrash" not in src
    assert "net_product_bash_force_write" not in src


def test_script_handoff_and_office_write_guard_v277():
    from app.services.agent_harness.model_driver import _answer_claims_script_handoff
    assert _answer_claims_script_handoff("生成脚本已写入，可直接运行生成 docx")
    assert not _answer_claims_script_handoff("已交付 Agent市场简报.docx")
    src = open("app/services/chat/tools/paths.py", encoding="utf-8").read()
    assert "不要用 write_file 写过程脚本" not in src
    assert "_office_product_goal" not in src


def test_product_blocks_search_capabilities_from_step0_v278():
    src = open("app/services/agent_harness/model_driver.py", encoding="utf-8").read()
    assert "capability_broker" in src
    assert "search_capabilities" in src
    assert "产物任务不需要 search_capabilities" not in src
    assert "禁止 search_capabilities" not in src


def test_bash_not_hard_blocked_v297():
    """v2.97：bash 不硬拦；download 仅在用户显式不要落盘时拦。"""
    src = open("app/services/agent_harness/model_driver.py", encoding="utf-8").read()
    assert "chat_lookup_block_bash_http" not in src
    assert "chat_lookup_block_bash_first" not in src
    assert "chat_lookup_block_bash_write" not in src
    assert "payload_tools" in src
    assert "chat_lookup_block_download" not in src
    # v2.96：不设检索次数上限 / 不强制 chat_lookup 收口——模型自判信息是否够用
    assert "net_chat_lookup_converge" not in src
    assert "chat_lookup_block_extra_search" not in src

def test_bash_meta_filters_process_files_v279():
    src = open("app/services/chat/tools/shell.py", encoding="utf-8").read()
    assert "filter_rows(saved)" in src
    assert "process_files" in src

def test_resume_nudge_no_rebuild_v279():
    src = open("app/services/agent_harness/model_driver.py", encoding="utf-8").read()
    assert "恢复观察" in src
    assert "resume_observation" in src
    assert "net_plan_first_nudge" not in src


def test_file_deliverable_cjk_word_boundary_v279():
    from app.services.agent_harness.model_driver import (
        _user_requires_file_deliverable,
        _answer_claims_write_blocked,
        _should_nudge_missing_file_deliverable,
    )
    msg = "用Word写一份很短的三要点简报，主题是Agent市场，直接交付docx"
    assert _user_requires_file_deliverable(msg) is True
    assert _answer_claims_write_blocked("未能调用文件写入工具，无法直接生成并交付 .docx") is True
    assert _should_nudge_missing_file_deliverable(
        user_message=msg,
        round_content="draft only",
        already_nudged=False,
        forced_final=False,
        has_executors=True,
        trace=[],
    ) is True


def test_v281_advance_plan_after_tool():
    from app.services.chat.main_tool_turn import _advance_plan_after_tool
    # 检索工具：检索步骤保持 running，不得误完成后续「整理」
    plan = [
        {"title": "查找最新天气", "status": "running"},
        {"title": "整理结论并回复", "status": "pending"},
    ]
    out = _advance_plan_after_tool(plan, "search_web")
    assert out[0]["status"] == "running"
    assert out[1]["status"] == "pending"

    # 多轮检索不得把 整理/交付 连标 completed（用户反馈假同步）
    plan_research = [
        {"title": "检索市场调研资料", "status": "running"},
        {"title": "整理对比与结论", "status": "pending"},
        {"title": "整理交付", "status": "pending"},
    ]
    mid = plan_research
    for _ in range(5):
        mid = _advance_plan_after_tool(mid, "search_web")
    assert mid[0]["status"] == "running"
    assert mid[1]["status"] == "pending"
    assert mid[2]["status"] == "pending"

    # 写工具才收口当前步骤并推进
    plan2 = [
        {"title": "整理文档结构", "status": "running"},
        {"title": "撰写正文", "status": "pending"},
        {"title": "保存并交付", "status": "pending"},
    ]
    out2 = _advance_plan_after_tool(plan2, "bash")
    assert out2[0]["status"] == "completed"
    assert out2[1]["status"] == "running"
    assert out2[2]["status"] == "pending"

    # 检索阶段结束后，search 不再误推后续步骤
    past_search = [
        {"title": "检索资料", "status": "completed"},
        {"title": "整理对比与结论", "status": "running"},
        {"title": "整理交付", "status": "pending"},
    ]
    stuck = _advance_plan_after_tool(past_search, "search_web")
    assert stuck[1]["status"] == "running"
    assert stuck[2]["status"] == "pending"


def test_v299_plan_binds_tool_intent_detail():
    """任务协作内容保真：To-do detail 绑定真实工具意图，非空目标模板。"""
    from app.services.chat.main_tool_turn import (
        _advance_plan_after_tool,
        _tool_plan_detail,
    )

    assert _tool_plan_detail("search_web", {"query": "网页端 Agent 对比"}) == "网页端 Agent 对比"
    assert _tool_plan_detail("write_file", {"filename": "/workspace/files/report.md"}) == "report.md"
    assert _tool_plan_detail("bash", {"command": "python gen.py && ls"})  # 有摘要即可

    plan = [
        {"title": "检索市场调研资料", "status": "pending"},
        {"title": "整理对比与结论", "status": "pending"},
        {"title": "整理交付", "status": "pending"},
    ]
    # 工具 start：检索步 running + detail=query
    mid = _advance_plan_after_tool(
        plan, "search_web", tool_args={"query": "Manus vs 通义"}, phase="start",
    )
    assert mid[0]["status"] == "running"
    assert "Manus vs 通义" in mid[0].get("detail", "")
    assert mid[1]["status"] == "pending"

    # 多轮检索累加 detail，不推进后续
    mid = _advance_plan_after_tool(
        mid, "search_web", tool_args={"query": "OpenAI Canvas 能力"}, phase="success",
    )
    assert mid[0]["status"] == "running"
    assert "OpenAI Canvas" in mid[0].get("detail", "")
    assert "Manus" in mid[0].get("detail", "")
    assert mid[1]["status"] == "pending"
    assert mid[2]["status"] == "pending"

    # 写工具 success：收口当前检索步，推进下一步；detail 落到完成步（若空）
    plan_write = [
        {"title": "整理文档结构", "status": "running", "detail": ""},
        {"title": "撰写正文", "status": "pending"},
        {"title": "保存并交付", "status": "pending"},
    ]
    out = _advance_plan_after_tool(
        plan_write, "write_file",
        tool_args={"filename": "调研报告.md"},
        phase="success",
    )
    assert out[0]["status"] == "completed"
    assert "调研报告.md" in out[0].get("detail", "")
    assert out[1]["status"] == "running"
    # 写动作意图跟到新 running 步（非检索标题）
    assert "调研报告.md" in out[1].get("detail", "")

    # 写工具不得把文件名绑到检索步骤
    research = [
        {"title": "检索资料", "status": "running", "detail": "Agent 对比"},
        {"title": "整理交付", "status": "pending"},
    ]
    started = _advance_plan_after_tool(
        research, "write_file", tool_args={"filename": "out.md"}, phase="start",
    )
    assert started[0]["detail"] == "Agent 对比"  # 检索 detail 不被覆盖
    assert "out.md" in started[1].get("detail", "")


def test_tool_success_uses_the_terminal_finalizer():
    src = open("app/services/chat/main_tool_turn.py", encoding="utf-8").read()
    assert "turn_finalizer.finalize_terminal" in src


def test_v281_pump_schedules_recovery_instead_of_fake_failure():
    src = open("app/services/chat/run_hub.py", encoding="utf-8").read()
    assert "recover_run_after_error" in src
    assert "已 completed，吞掉泵层异常假失败" not in src


def test_v281_resume_natural_phrases():
    from app.services.chat.turn_context_builder import needs_resume_checkpoint
    assert needs_resume_checkpoint("继续")
    assert needs_resume_checkpoint("接着做")
    assert needs_resume_checkpoint("在原来基础上继续")
    assert needs_resume_checkpoint("网络断了继续")
    assert needs_resume_checkpoint("继续，不要重做")
    assert not needs_resume_checkpoint("帮我写一份报告")


def test_v281_assistant_message_keeps_reasoning():
    from app.services.agent_harness.model_driver import _assistant_message
    m = _assistant_message("hi", tool_calls=[{"id": "1"}], reasoning="think")
    assert m["role"] == "assistant"
    assert m["content"] == "hi"
    assert m["reasoning_content"] == "think"
    assert m["tool_calls"][0]["id"] == "1"
    m2 = _assistant_message("x")
    assert "reasoning_content" not in m2


def test_v281_no_plain_fallback_for_file_deliverable():
    src = open("app/services/chat/main_tool_turn.py", encoding="utf-8").read()
    assert "_persist_recovery_evidence" in src
    assert "Any execution failure belongs to the recovery owner" in src
