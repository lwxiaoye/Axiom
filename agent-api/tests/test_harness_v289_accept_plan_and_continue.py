"""v2.89: no orphan accept plan; continue next-action; plan_incomplete covers in_progress."""
import asyncio
from pathlib import Path
from unittest.mock import patch

from app.services.agent_harness.orchestrator import _goal_next_action_text
from app.services.agent_harness.model_driver import (
    LoopState,
    _context_persisted_delivery_receipts,
    _extract_persisted_delivery_filename,
    _extract_persisted_delivery_filenames,
)
from app.services.chat.tools.base import MainTool


def _src(rel: str) -> str:
    return Path(__file__).resolve().parents[1].joinpath(rel).read_text(encoding="utf-8")


def test_accept_no_task_plan_seed():
    src = _src("app/services/agent_harness/orchestrator.py")
    accept = src[src.find("async def accept_harness_run"): src.find("async def start_resume_chat_run")]
    assert "task_plan_updated" not in accept
    assert "message_commentary" in accept
    assert "_goal_next_action_text" in accept


def test_continue_next_action_empty_model_driven():
    for msg in ("继续", "接着做", "continue", "继续改一下"):
        assert _goal_next_action_text(msg, pure_qa=False) == ""


def test_weather_next_action_empty_model_driven():
    text = _goal_next_action_text("你帮我看看今天漳州市的天气状况", pure_qa=False)
    assert text == ""


def test_plan_incomplete_includes_in_progress():
    st = LoopState()
    st.latest_plan_steps = [
        {"title": "写正文", "status": "in_progress"},
        {"title": "交付", "status": "completed"},
    ]
    assert st.plan_incomplete is True
    st.latest_plan_steps = [
        {"title": "写正文", "status": "completed"},
        {"title": "交付", "status": "completed"},
    ]
    assert st.plan_incomplete is False


def test_subagent_discovery_skipped_for_lookup_or_resume():
    src = _src("app/services/chat/main_tool_turn.py")
    assert "subagent_discovery_skipped" not in src
    assert "lookup_or_resume" not in src


def test_bare_control_does_not_emit_recovered_skill_as_visible_work():
    src = _src("app/services/chat/main_tool_turn.py")
    assert "if not bare_control_message(str(message or \"\")):" in src


def test_resume_bare_confirm_nudge_present():
    src = _src("app/services/agent_harness/model_driver.py")
    assert "net_resume_bare_confirm_nudge" not in src


def test_resume_block_bare_invent_present():
    src = _src("app/services/agent_harness/model_driver.py")
    assert "resume_block_bare_invent" not in src
    assert "net_resume_bare_force_confirm" not in src
    assert "resume_bare_confirm" not in src


def test_weather_numeric_via_search_scrub_not_search_quota():
    """v2.96：不再靠 lookup 强制收口夹带气温要求；数值靠 scrub + 模型自决。"""
    src = _src("app/services/agent_harness/model_driver.py")
    assert "net_chat_lookup_converge" not in src
    assert "chat_lookup_block_extra_search" not in src
    # 仍应有搜索结果回填/质检路径（turn_finalizer）
    from app.services.chat.turn_finalizer import scrub_false_search_hedge
    assert callable(scrub_false_search_hedge)


def test_scrub_search_fills_missing_units():
    from app.services.chat.turn_finalizer import scrub_false_search_hedge
    trace = [{
        "name": "search_web",
        "status": "completed",
        "preview": "漳州今天晴 26℃~35℃ 风力3级",
    }]
    out = scrub_false_search_hedge(
        "8月的漳州处于盛夏，通常炎热。如果你需要，我可以改天再帮你查一次带数值的天气实况。",
        trace=trace,
    )
    assert "℃" in out or "°C" in out


def test_drive_model_accepts_raw_user_message():
    src = _src("app/services/agent_harness/model_driver.py")
    assert "raw_user_message: Optional[str] = None" in src
    assert "_goal_input = _goal_src" in src
    call = _src("app/services/chat/main_tool_turn.py")
    assert "raw_user_message=str(message or" in call


def test_context_shows_prior_mutation_from_checkpoint():
    from app.services.agent_harness.model_driver import _context_shows_prior_mutation
    msgs = [{
        "role": "user",
        "content": "【断点现场（平台注入，强制遵守）】\n- continue-x.md\n已执行工具 write_file",
    }]
    assert _context_shows_prior_mutation(msgs, "继续") is True
    assert _context_shows_prior_mutation([{"role": "user", "content": "你好"}], "继续") is False

def test_bare_prior_gate_uses_context():
    src = _src("app/services/agent_harness/model_driver.py")
    # The legacy helper may remain for compatibility, but the active drive loop must not call it
    # as a keyword gate.
    assert src.count("_context_shows_prior_mutation(") == 1
    assert "_prior_mut2" not in src


def test_resume_product_nudge_defers_to_bare_prior():
    """恢复上下文不注入产品任务 nudge。"""
    src = _src("app/services/agent_harness/model_driver.py")
    assert "_prior_for_product" not in src
    assert "_bare_for_product" not in src
    assert "net_resume_product_nudge" not in src


def test_live_matrix_temp_regex_requires_units():
    src = Path(__file__).resolve().parents[1].joinpath("scripts/live_v289_matrix.py").read_text(encoding="utf-8")
    assert ("[0-9]+\s*(?:℃|°C|度)" in src) or (r"[0-9]+\s*(?:℃|°C|度)" in src) or (r"[0-9]+\s*(?:℃|°C)" in src)
    assert "|气温|温度" not in src.split("has_temp")[1][:80]
    assert r"\[图\d+\]" in src


def test_context_shows_prior_mutation_from_assistant_delivery():
    from app.services.agent_harness.model_driver import _context_shows_prior_mutation
    msgs = [{
        "role": "assistant",
        "content": "已把 continue-x.md 的内容改为 seed-continued（其余不变），已保存。",
    }]
    assert _context_shows_prior_mutation(msgs, "继续") is True


def test_weather_temp_nudge_present():
    src = _src("app/services/agent_harness/model_driver.py")
    assert "net_weather_temp_nudge" not in src
    assert "weather_temp_nudged" not in src


def test_scrub_climate_hedge_replaced_by_serp_units():
    from app.services.chat.turn_finalizer import scrub_false_search_hedge
    trace = [{
        "name": "search_web",
        "status": "completed",
        "preview": "漳州今天 阴转晴 27℃~35℃ 西风2级",
    }]
    ans = (
        "管理员，很抱歉，这一轮联网检索已达到次数上限，我仍未能拿到带 ℃ 数字的可核验气温数据。"
        "作为参考，8月上旬的漳州通常处于高温时段，白天最高气温常在 33℃ 上下（此为往年同期经验值）。[图1]"
    )
    out = scrub_false_search_hedge(ans, trace=trace)
    assert "27" in out and "℃" in out
    assert "往年同期" not in out
    assert "[图1]" in out


def test_scrub_normalizes_chinese_du_and_strips_budget_hedge():
    from app.services.chat.turn_finalizer import scrub_false_search_hedge
    trace = [{"name": "search_web", "status": "completed", "preview": "漳州港 37 度高温"}]
    ans = (
        "本轮检索次数已用尽，没能拿到可核验气温。间接线索提及 37 度高温，不能当作今日官方实测。[图1]"
    )
    out = scrub_false_search_hedge(ans, trace=trace)
    assert "℃" in out
    assert "37" in out
    assert "检索次数已用尽" not in out
    assert "[图1]" in out



def test_bare_confirm_only_state_and_guards():
    src = _src("app/services/agent_harness/model_driver.py")
    assert "bare_confirm_only: bool = False" in src
    assert "and not st.bare_confirm_only" not in src
    assert "if st.bare_confirm_only or str(st.force_converge" not in src
    st = LoopState()
    assert st.bare_confirm_only is False
    st.bare_confirm_only = True
    assert st.bare_confirm_only is True


def test_resume_incomplete_fact_is_stable_for_the_run():
    st = LoopState()
    assert st.resume_started_incomplete is False
    st.resume_started_incomplete = True
    st.latest_plan_steps = [{"title": "交付", "status": "completed"}]
    assert st.plan_incomplete is False
    assert st.resume_started_incomplete is True


def test_bare_confirm_requires_server_persisted_receipt():
    assistant_claim = [{
        "role": "assistant",
        "content": "波音飞机款式介绍.pptx 已完成并保存到我的文件，可直接使用。",
    }]
    recent_files_only = [{
        "role": "user",
        "content": (
            "【任务快照（平台注入，上一轮断点现场，强制沿用）】\n"
            "最近文件（仅供定位半成品，不代表本任务已交付）：波音飞机款式介绍.pptx"
        ),
    }]
    persisted = [{
        "role": "user",
        "content": (
            "【任务快照（平台注入，上一轮断点现场，强制沿用）】\n"
            "已持久化交付回执（平台已核验，可确认完成）：\n"
            "- 波音飞机款式介绍-source.zip（file_id=file-zip）\n"
            "- 波音飞机款式介绍.pptx（file_id=file-123）"
        ),
    }]

    assert _context_persisted_delivery_receipts(assistant_claim) == []
    assert _context_persisted_delivery_receipts(recent_files_only) == []
    assert _context_persisted_delivery_receipts(persisted) == [{
        "file_id": "file-123",
        "filename": "波音飞机款式介绍.pptx",
    }]
    assert _extract_persisted_delivery_filename(persisted) == "波音飞机款式介绍.pptx"
    assert _extract_persisted_delivery_filenames(persisted) == [
        "波音飞机款式介绍.pptx",
    ]


def test_bare_incomplete_allows_edit_not_search():
    src = _src("app/services/agent_harness/model_driver.py")
    assert "禁止 search_web/use_skill/download_url 从零重开" not in src
    assert "_incomplete2" not in src
    assert "_looks_incomplete" not in src


def test_extract_recent_delivery_filename_prefers_latest():
    from app.services.agent_harness.model_driver import _extract_recent_delivery_filename
    msgs = [
        {"role": "assistant", "content": "已生成 Agent市场三要点简报-cb0c1fe2.docx"},
        {"role": "assistant", "content": "已生成《Agent市场三要点简报.docx》，已保存到我的文件。"},
    ]
    assert _extract_recent_delivery_filename(msgs) == "Agent市场三要点简报.docx"


def test_bare_incomplete_survives_verify_stop_and_executes_repair():
    """The incomplete fact cannot disappear after a process-only assistant message."""
    from tests.test_loop_guard_batch2 import (
        FakeAsyncClient,
        _drive,
        sse,
        sse_tool_calls,
        DONE,
    )

    calls = []

    async def run_glob(args):
        calls.append(("glob", args))
        return "/workspace/files/boeing-deck/project.pptd"

    async def run_bash(args):
        calls.append(("bash", args))
        return "工程已修改，但尚未发布到我的文件"

    tools = [
        MainTool(
            name="glob", description="glob", parameters={}, execute=run_glob,
            internal=True, parallel_safe=True, idempotent=True,
            semantic_tags=("investigate",),
        ),
        MainTool(
            name="bash", description="bash", parameters={}, execute=run_bash,
            internal=True, effect_scope="user_files", idempotent=False,
            resource_locks=("user-files",),
            semantic_tags=("mutate", "productive", "revision_mutation", "artifact_producer"),
        ),
    ]
    FakeAsyncClient.responses = [
        [sse_tool_calls([("g1", "glob", {"pattern": "**/*.pptd"})]), DONE],
        [
            sse({"content": "已找到上次工程，正在核对。"}),
            sse_tool_calls([("v1", "bash", {"command": "ls -la /workspace/files/boeing-deck"})]),
            DONE,
        ],
        [sse_tool_calls([("b1", "bash", {"command": "python3 repair_and_publish.py"})]), DONE],
        [sse({"content": "波音飞机款式介绍 PPT 已完成，可直接使用。"}), DONE],
    ]

    history = [
        {"role": "user", "content": "制作一份波音飞机款式介绍 PPT"},
        {"role": "assistant", "content": "工程只完成了一部分，文件尚未交付。"},
    ]
    checkpoint = (
        "继续\n\n"
        "【任务快照（平台注入，上一轮断点现场，强制沿用）】\n"
        "最近文件（仅供定位半成品，不代表本任务已交付）：boeing-deck.pptd\n"
        "已执行工具：\n- bash (ok)：已创建工程"
    )
    with patch.multiple(
        "app.services.agent_harness.model_driver.settings",
        TOOL_LOOP_MAX_STEPS=5,
        TOOL_LOOP_TOKEN_BUDGET=0,
        TOOL_LOOP_MAX_WALL_SECONDS=0,
        TOOL_LOOP_QUALITY_EXTRA_STEPS=0,
    ):
        events = asyncio.run(_drive(
            model="gpt-5.5",
            api_key="test-key",
            history=history,
            user_input=checkpoint,
            raw_user_message="继续",
            tools=tools,
        ))

    productive = [args for name, args in calls if name == "bash" and "repair_and_publish" in args.get("command", "")]
    assert productive, "未交付续做不能被 bare 确认闸拦截真实修复"
    final = next(event for event in events if event.get("type") == "final")
    # The model's claim remains a claim until the Completion Verifier evaluates receipts; the
    # loop does not rewrite it with a keyword-based delivery warning.
    assert "可直接使用" in final.get("answer", ""), repr(events)
    assert "文件还没有成功写入" not in final.get("answer", ""), repr(events)


def test_bare_completed_confirms_only_persisted_receipt_filename():
    from tests.test_loop_guard_batch2 import FakeAsyncClient, _drive, sse, DONE

    calls = []

    async def run_bash(args):
        calls.append(args)
        return "unexpected"

    tool = MainTool(
        name="bash", description="bash", parameters={}, execute=run_bash,
        internal=True, effect_scope="user_files", idempotent=False,
        resource_locks=("user-files",),
        semantic_tags=("mutate", "productive", "artifact_producer"),
    )
    FakeAsyncClient.responses = [[
        sse({"content": "旧历史里的 other-deck.pptx 已完成，可直接使用。"}),
        DONE,
    ]]
    checkpoint = (
        "继续\n\n"
        "【任务快照（平台注入，上一轮断点现场，强制沿用）】\n"
        "已持久化交付回执（平台已核验，可确认完成）：\n"
        "- 波音飞机款式介绍.pptx（file_id=file-boeing-1）"
    )
    events = asyncio.run(_drive(
        model="gpt-5.5",
        api_key="test-key",
        history=[{"role": "user", "content": "制作一份波音飞机款式介绍 PPT"}],
        user_input=checkpoint,
        raw_user_message="继续",
        tools=[tool],
    ))

    assert calls == []
    final = next(event for event in events if event.get("type") == "final")
    # A recovery snapshot is evidence for the verifier, not a title-correction instruction for
    # the model's answer.
    assert "other-deck.pptx" in final.get("answer", "")
    assert "波音飞机款式介绍.pptx" not in final.get("answer", "")
