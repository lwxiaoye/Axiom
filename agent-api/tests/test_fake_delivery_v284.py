"""v2.84: 假交付硬闸——无真实文件不得终答「已交付」。"""

from app.services.agent_harness.model_driver import (
    _should_nudge_missing_file_deliverable,
    _looks_like_final_delivery,
    _user_requires_file_deliverable,
    _invalidate_incomplete_plan_steps,
    _close_incomplete_plan_steps,
)
from app.services.chat.turn_finalizer import (
    scrub_false_file_delivery_claim,
    tools_delivered_artifacts,
)


WORD_MSG = "用Word写一份很短的三要点简报，主题是Agent市场，直接交付docx"
FAKE_ANSWER = "已交付 `Agent市场简报.docx`，在「我的文件」里可直接下载使用。"


def test_user_requires_word():
    assert _user_requires_file_deliverable(WORD_MSG)
    assert _looks_like_final_delivery(FAKE_ANSWER)


def test_bash_without_files_not_delivered():
    trace = [{
        "name": "bash",
        "status": "completed",
        "preview": "字体设置那步出了点小问题，修正后重新生成。",
        "files": [],
    }]
    assert tools_delivered_artifacts(trace) is False


def test_structured_empty_artifacts_override_filename_mentions_in_summary():
    trace = [{
        "name": "bash",
        "status": "completed",
        "semantic_tags": ["artifact_producer"],
        "preview": "未同步：old-deck.pptx、archive.pdf",
        "observation": {
            "status": "succeeded",
            "summary": "文件区有 old-deck.pptx 未同步",
            "structured_data": {"ui": {"summary": "已运行命令", "detail": "ok"}},
            "artifact_refs": [],
        },
    }]
    assert tools_delivered_artifacts(trace) is False

    from app.services.agent_harness.model_driver import _trace_has_saved_deliverable
    assert _trace_has_saved_deliverable(trace) is False


def test_second_nudge_on_fake_claim():
    """未落盘时每次无工具终答都推回；额度用尽后交给 scrub。"""
    trace = [{
        "name": "bash",
        "status": "completed",
        "preview": "font error",
        "files": [],
    }]
    assert _should_nudge_missing_file_deliverable(
        user_message=WORD_MSG,
        round_content=FAKE_ANSWER,
        already_nudged=True,
        forced_final=False,
        has_executors=True,
        trace=trace,
        nudge_count=1,
        max_nudges=5,
    ) is True
    # 诚实半截同样继续推（v2.94）
    assert _should_nudge_missing_file_deliverable(
        user_message=WORD_MSG,
        round_content="内容已整理，请回复继续再生成 docx。",
        already_nudged=True,
        forced_final=False,
        has_executors=True,
        trace=trace,
        nudge_count=2,
        max_nudges=5,
    ) is True
    # 用尽额度后不再推
    assert _should_nudge_missing_file_deliverable(
        user_message=WORD_MSG,
        round_content=FAKE_ANSWER,
        already_nudged=True,
        forced_final=False,
        has_executors=True,
        trace=trace,
        nudge_count=5,
        max_nudges=5,
    ) is False


def test_first_nudge_still_on_empty():
    assert _should_nudge_missing_file_deliverable(
        user_message=WORD_MSG,
        round_content="调研结论如下。",
        already_nudged=False,
        forced_final=False,
        has_executors=True,
        trace=[],
        nudge_count=0,
        max_nudges=2,
    ) is True


def test_scrub_false_file_delivery_claim():
    out = scrub_false_file_delivery_claim(
        FAKE_ANSWER, need_file=True, tools_delivered=False,
    )
    assert "已交付" not in out
    assert "我的文件" in out
    # 真交付不 scrub
    ok = scrub_false_file_delivery_claim(
        FAKE_ANSWER, need_file=True, tools_delivered=True,
    )
    assert ok == FAKE_ANSWER


def test_scrub_false_file_delivery_claim_for_completed_usable_wording():
    out = scrub_false_file_delivery_claim(
        "波音飞机款式介绍 PPT 已完成，可直接使用。",
        need_file=True,
        tools_delivered=False,
    )
    assert "已完成" not in out
    assert "可直接使用" not in out
    assert "文件还没有成功写入" in out


def test_ppt_failure_does_not_reappend_unverified_source_success_summary():
    answer = (
        "威斯布鲁克生涯演示稿的内容、结构和配图已经全部做好：共 8 页。"
        "四张比赛照片都已选好并嵌入对应页面。"
        "但当前环境提示缺少 PPTD-WASM，尚未导出 PPTX。"
    )
    out = scrub_false_file_delivery_claim(answer, need_file=True, tools_delivered=False)
    assert "文件还没有成功写入" in out
    assert "全部做好" not in out
    assert "都已选好" not in out
    assert "已整理的内容要点" not in out
    assert scrub_false_file_delivery_claim(answer, need_file=False, tools_delivered=False) == answer


def test_honest_export_failure_remains_unchanged():
    answer = "源工程已写入，但导出命令失败，尚无可下载的 PPTX。"
    assert scrub_false_file_delivery_claim(answer, need_file=True, tools_delivered=False) == answer
    negative = "还没有全部做好，导出仍失败，不能下载。"
    assert scrub_false_file_delivery_claim(negative, need_file=True, tools_delivered=False) == negative


def test_plan_invalidated_when_no_deliverable():
    steps = [
        {"title": "整理内容", "status": "completed"},
        {"title": "写入文件", "status": "running"},
        {"title": "保存并交付", "status": "pending"},
    ]
    failed = _invalidate_incomplete_plan_steps(steps)
    assert failed[0]["status"] == "completed"
    assert failed[1]["status"] == "invalidated"
    assert failed[2]["status"] == "invalidated"
    assert failed[1]["reason"] == "run_completed_partial"
    closed = _close_incomplete_plan_steps(steps)
    assert closed[1]["status"] == "invalidated"
    assert closed[2]["status"] == "invalidated"
