"""v2.94: 用户明确要交付时，本轮必须落盘——禁止「请回复继续」半截收工。"""

from app.services.agent_harness.model_driver import (
    LoopState,
    _answer_defers_delivery_to_user,
    _should_nudge_missing_file_deliverable,
    _user_requires_file_deliverable,
)
from app.services.chat.turn_finalizer import scrub_false_file_delivery_claim


PPT_MSG = "可以制作一份ppt给我吗？要求精美一点，我要去宣传这个游戏，内容丰富"


def test_ppt_request_requires_file():
    assert _user_requires_file_deliverable(PPT_MSG) is True
    assert _user_requires_file_deliverable("用Word写一份三要点简报") is True


def test_defers_continue_pattern():
    body = (
        "本轮 PPT 的制作没有最终落成文件。"
        "调研与配图已就绪，排版编译被中断。"
        "请回复「继续」，我会基于现有素材完整生成并交付。"
    )
    assert _answer_defers_delivery_to_user(body) is True
    assert _answer_defers_delivery_to_user("已交付报告.docx，请查收。") is False


def test_honest_half_still_nudged_after_first():
    """旧逻辑第二次只打假交付——诚实「请继续」会过关。v2.94 必须继续推。"""
    empty_trace = [{"name": "search_web", "status": "completed", "files": []}]
    assert _should_nudge_missing_file_deliverable(
        user_message=PPT_MSG,
        round_content="素材已齐，请回复继续，我再生成 pptx。",
        already_nudged=True,
        forced_final=False,
        has_executors=True,
        trace=empty_trace,
        nudge_count=1,
        max_nudges=5,
    ) is True
    assert _should_nudge_missing_file_deliverable(
        user_message=PPT_MSG,
        round_content="素材已齐，请回复继续。",
        already_nudged=True,
        forced_final=False,
        has_executors=True,
        trace=empty_trace,
        nudge_count=4,
        max_nudges=5,
    ) is True
    assert _should_nudge_missing_file_deliverable(
        user_message=PPT_MSG,
        round_content="素材已齐，请回复继续。",
        already_nudged=True,
        forced_final=False,
        has_executors=True,
        trace=empty_trace,
        nudge_count=5,
        max_nudges=5,
    ) is False


def test_nudge_max_raised():
    assert LoopState.MISSING_FILE_NUDGE_MAX >= 5


def test_scrub_defers_continue_without_inviting_user():
    out = scrub_false_file_delivery_claim(
        "请回复继续，我会交付 pptx。",
        need_file=True,
        tools_delivered=False,
    )
    assert "没有可下载" in out or "未能完成交付" in out
    assert "请回复继续" not in out
    assert "你可以回复" not in out
    # 假交付仍 scrub，且不再诱导用户说继续
    out2 = scrub_false_file_delivery_claim(
        "已交付《宣传.pptx》，请查收。",
        need_file=True,
        tools_delivered=False,
    )
    assert "已交付" not in out2
    assert "请回复「继续」" not in out2
    assert "你可以回复「继续」" not in out2
