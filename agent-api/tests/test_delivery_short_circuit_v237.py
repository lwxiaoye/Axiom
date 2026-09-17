# -*- coding: utf-8 -*-
"""v2.37/v2.58: delivery short-circuit helpers."""
from app.services.agent_harness.model_driver import (
    _looks_like_final_delivery,
    _trace_has_saved_deliverable,
)


def test_final_delivery_detects_chinese_done():
    assert _looks_like_final_delivery("PPT 已制作完成并通过验证。已交付 deck.pptx，已存入我的文件") is True
    assert _looks_like_final_delivery("好的") is False


def test_trace_saved_deliverable_from_preview():
    trace = [
        {
            "name": "bash",
            "status": "completed",
            "preview": "saved deck.pptx to my files 我的文件",
        }
    ]
    assert _trace_has_saved_deliverable(trace) is True


def test_final_delivery_short_please_check():
    assert _looks_like_final_delivery("PPT 已交付，请查收。") is True
    assert _looks_like_final_delivery("演示文稿可以下载了。") is True


def test_trace_saved_deliverable_from_files_meta():
    trace = [
        {
            "name": "bash",
            "status": "completed",
            "preview": "ok",
            "files": [{"filename": "deck.pptx"}],
        }
    ]
    assert _trace_has_saved_deliverable(trace) is True


def test_looks_like_final_delivery_natural_write_v258():
    assert _looks_like_final_delivery("已新建 harness-v257.md，内容为 v257-ok。")
    assert _looks_like_final_delivery("已写好 note.md，内容是 ok。")
    assert _looks_like_final_delivery("已创建 a.md。")
    assert not _looks_like_final_delivery("漳州今天晴，26℃。")
    assert not _looks_like_final_delivery("内容为高温晴热")
    assert not _looks_like_final_delivery("报告内容为背景与结论")


def test_bash_verify_only_and_incomplete_tail_v259():
    from app.services.agent_harness.model_driver import _bash_is_verify_only
    from app.services.chat.turn_finalizer import strip_trailing_incomplete_process
    assert _bash_is_verify_only({"command": "wc -w /workspace/files/a.md"})
    assert _bash_is_verify_only({"command": "cat /workspace/files/a.md | head"})
    assert not _bash_is_verify_only({"command": "python build.py"})
    assert not _bash_is_verify_only({"command": "echo x > a.md"})
    raw = "已交付 a.md。" + chr(10) + "内容 ok。" + chr(10) + "先完整查看文件确认实际情况："
    out = strip_trailing_incomplete_process(raw)
    assert "查看" not in out
    assert "ok" in out


def test_plan_rewrite_goal_oriented_v260():
    from app.services.agent_harness.model_driver import _rewrite_toolish_plan_for_user, _plan_titles_are_toolish
    toolish = [
        {"title": "加载所需技能", "status": "completed"},
        {"title": "检索资料", "status": "completed"},
        {"title": "执行命令完成加工", "status": "completed"},
        {"title": "写入/生成文件", "status": "completed"},
    ]
    assert _plan_titles_are_toolish(toolish)
    out = _rewrite_toolish_plan_for_user(toolish, "写一份简短调研报告 chrome-plan.md，约125字。不要搜索")
    titles = [s["title"] for s in out]
    assert "加载所需技能" not in titles
    assert "检索资料" not in titles
    assert any("写" in x or "保存" in x or "内容" in x for x in titles)


def test_script_not_deliverable_v261():
    from app.services.agent_harness.model_driver import (
        _trace_has_saved_deliverable,
        _user_requires_file_deliverable,
        _should_nudge_missing_file_deliverable,
    )
    from app.services.chat.turn_finalizer import tools_delivered_artifacts, scrub_false_tool_outage_claim

    assert _user_requires_file_deliverable("整合成 word 文档给我")
    py_trace = [{
        "name": "write_file",
        "status": "completed",
        "filename": "gen_report_a.py",
        "preview": "已新建 gen_report_a.py",
    }]
    assert _trace_has_saved_deliverable(py_trace) is False
    assert tools_delivered_artifacts(py_trace) is False
    doc_trace = [{
        "name": "bash",
        "semantic_tags": ["artifact_producer"],
        "status": "completed",
        "preview": "已保存 agent-market-report.docx 到我的文件",
        "files": [{"filename": "agent-market-report.docx"}],
    }]
    assert _trace_has_saved_deliverable(doc_trace) is True
    assert tools_delivered_artifacts(doc_trace) is True
    assert _should_nudge_missing_file_deliverable(
        user_message="找最厉害的 agent，做成 word 文档",
        round_content="调研结论如下。脚本已写好。",
        already_nudged=False,
        forced_final=False,
        has_executors=True,
        trace=py_trace,
    )
    cleaned = scrub_false_tool_outage_claim(
        "本轮环境限制了写入操作，Word 文档尚未落盘，你确认后我立即落盘。",
        tools_succeeded=True,
        tools_delivered=False,
    )
    assert "环境限制" not in cleaned
    assert "尚未落盘" not in cleaned
