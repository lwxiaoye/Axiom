"""v2.85: 继续=resume 不重开；未交付时任务板失败收口。"""

from app.services.chat.turn_context_builder import needs_resume_checkpoint
from app.services.agent_harness.model_driver import (
    _invalidate_incomplete_plan_steps,
    _close_incomplete_plan_steps,
)
from app.services.chat.turn_finalizer import scrub_false_file_delivery_claim


def test_continue_phrases_need_resume():
    assert needs_resume_checkpoint("继续") is True
    assert needs_resume_checkpoint("接着做") is True
    assert needs_resume_checkpoint("继续完成刚才的文档") is True
    assert needs_resume_checkpoint("在原来基础上继续") is True
    assert needs_resume_checkpoint("帮我查一下今天漳州天气") is False


def test_all_resume_phrases_share_plan_inheritance_gate():
    import inspect

    from app.services.agent_harness.orchestrator import HarnessOrchestrator
    from app.services.chat.turn_decision import bare_control_message

    assert needs_resume_checkpoint("请直接交付") is True
    assert needs_resume_checkpoint("现在导出给我") is True
    assert needs_resume_checkpoint("直接交付ppt") is True
    assert needs_resume_checkpoint("不用审查直接交付") is True
    assert bare_control_message("请直接交付") is False
    source = inspect.getsource(HarnessOrchestrator.stream_chat)
    resume_projection = source[
        source.index("_resume_meta: Optional[dict]"):
        source.index("yield channel.thread", source.index("_resume_meta: Optional[dict]"))
    ]
    assert "if _need_resume_cp(_rs_msg) or resume_source_run_id:" in resume_projection
    assert "inherit_latest_thread_plan" in resume_projection
    assert "if bare_control_message(_rs_msg):" not in resume_projection


def test_incomplete_plan_invalidated_not_completed():
    steps = [
        {"title": "整理内容", "status": "completed"},
        {"title": "写入文件", "status": "running"},
        {"title": "保存并交付", "status": "pending"},
    ]
    failed = _invalidate_incomplete_plan_steps(steps)
    assert [s["status"] for s in failed] == ["completed", "invalidated", "invalidated"]
    assert failed[1]["reason"] == "run_completed_partial"
    closed = _close_incomplete_plan_steps(steps)
    assert closed[1]["status"] == "invalidated"
    assert closed[2]["status"] == "invalidated"


def test_scrub_incomplete_signals_task_board():
    ans = scrub_false_file_delivery_claim(
        "已交付 Agent市场简报.docx，请下载",
        need_file=True,
        tools_delivered=False,
    )
    assert "已交付" not in ans
    assert "文件还没有成功写入" in ans


def test_resume_mode_skips_product_seed_source():
    src = open("app/services/agent_harness/model_driver.py", encoding="utf-8").read()
    assert "net_resume_marker_self_inject" in src
    assert "【恢复观察】" in src
    assert "恢复现场中的文件" in open("app/services/chat/turn_context_builder.py", encoding="utf-8").read()


def test_resume_mode_uses_raw_user_not_loop_goal_v287():
    """续做判定必须用原始用户句，不能用 _loop_user_text 回溯的 seed。"""
    from pathlib import Path
    candidates = [
        Path("/app/app/services/agent_harness/model_driver.py"),
        Path(__file__).resolve().parents[1] / "app/services/agent_harness/model_driver.py",
    ]
    text = next(c.read_text(encoding="utf-8") for c in candidates if c.exists())
    assert "_need_resume_cp(_resume_raw)" in text
    assert "_loop_user_text(user_input, messages) or str(user_input" not in text.split("resume_mode = bool(_user_wants_resume)")[0][-500:]
    # Resume only restores facts; it must not synthesize a delivery decision from a
    # keyword or the historical “mutated” bit.
    assert "_resume_can_skip" not in text
    assert "Tool choice remains the model's decision" in text
