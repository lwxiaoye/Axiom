# -*- coding: utf-8 -*-
"""v2.38: resume checkpoint gate + delivery phrase expansion."""
from app.services.chat.turn_context_builder import needs_resume_checkpoint
from app.services.agent_harness.model_driver import _looks_like_final_delivery


def test_needs_resume_checkpoint_bare_continue():
    assert needs_resume_checkpoint("继续") is True
    assert needs_resume_checkpoint("接着做") is True
    assert needs_resume_checkpoint("继续完成刚才的PPT") is True
    assert needs_resume_checkpoint("从断点继续") is True
    assert needs_resume_checkpoint("你好") is False
    assert needs_resume_checkpoint("帮我做一个天气PPT") is False
    assert needs_resume_checkpoint("取消") is False


def test_looks_like_final_delivery_v238():
    assert _looks_like_final_delivery("PPT 已交付，请查收。") is True
    assert _looks_like_final_delivery("已生成 克莱汤普森.pptx 到我的文件") is True


def test_is_skill_explore_call():
    from app.services.agent_harness.model_driver import _is_skill_explore_call
    assert _is_skill_explore_call("use_skill", {"skill_id": "x"}) is True
    assert _is_skill_explore_call("bash", {"command": "cat /workspace/skills/foo/SKILL.md"}) is True
    assert _is_skill_explore_call("bash", {"command": "ls /workspace/files"}) is False
    assert _is_skill_explore_call("read_file", {"path": "skills/bar/components.md"}) is True
    # v2.46：真正执行技能脚本不算探查
    assert _is_skill_explore_call(
        "bash", {"command": "python3 /workspace/skills/ppt-studio/build_deck.py --out /workspace/files/a.pptx"}
    ) is False
    # 产物脚本后附带 ls/head 验收仍是 productive，不得污染探查计数并最终禁用 bash。
    assert _is_skill_explore_call(
        "bash",
        {"command": (
            "python3 /workspace/skills/ppt-studio/scripts/create_deck.py spec.json out && "
            "ls out/pages && head -20 out/deck.pptd"
        )},
    ) is False
    assert _is_skill_explore_call(
        "bash",
        {"command": (
            "python3 /workspace/skills/ppt-studio/scripts/export_pptx.py out/deck.pptd "
            "--output /workspace/files/a.pptx --force && ls /workspace/files/a.pptx"
        )},
    ) is False
    # The production exporter is a Python wrapper over a Node/WASM script.
    # Trailing verification must not turn either form into Skill exploration.
    assert _is_skill_explore_call(
        "bash",
        {"command": (
            "python3 /workspace/skills/ppt-studio/scripts/export_pptx.py "
            "/workspace/tmp/ppt-project/deck.pptd --output "
            "/workspace/tmp/ppt-project/deck.pptx && "
            "ls -lh /workspace/tmp/ppt-project/deck.pptx"
        )},
    ) is False
    assert _is_skill_explore_call(
        "bash",
        {"command": (
            "node /workspace/skills/ppt-studio/scripts/local-export/export-pptd.mjs "
            "/workspace/tmp/ppt-project/deck.pptd --no-sign && "
            "head -1 /workspace/tmp/ppt-project/deck.pptd"
        )},
    ) is False
    assert _is_skill_explore_call(
        "bash", {"command": "/workspace/skills/foo/scripts/run.sh && ls /workspace/files"}
    ) is False
    assert _is_skill_explore_call(
        "bash", {"command": "deno run --allow-read /workspace/skills/foo/scripts/run.ts && ls out"}
    ) is False
    assert _is_skill_explore_call(
        "bash", {"command": "make -f /workspace/skills/foo/scripts/Makefile && ls out"}
    ) is False
    # A path merely printed from an eval string, or one escaping scripts/ via
    # traversal, must not suppress the exploration guard.
    assert _is_skill_explore_call(
        "bash",
        {"command": "python3 -c \"print('/workspace/skills/foo/scripts/run.py')\" && cat /workspace/skills/foo/SKILL.md"},
    ) is True
    assert _is_skill_explore_call(
        "bash",
        {"command": "python3 /workspace/skills/foo/scripts/../SKILL.md && ls /workspace/skills/foo"},
    ) is True
    assert _is_skill_explore_call("bash", {"command": "ls /workspace/skills"}) is True


def test_skill_md_reread_and_soft_max():
    from app.services.agent_harness.model_driver import LoopState, _is_skill_md_reread
    assert _is_skill_md_reread("read_file", {"path": "/workspace/skills/x/SKILL.md"}) is True
    assert _is_skill_md_reread("bash", {"command": "cat /workspace/skills/x/SKILL.md"}) is True
    assert _is_skill_md_reread("bash", {"command": "python3 /workspace/skills/x/build.py"}) is False
    assert LoopState.SKILL_EXPLORE_SOFT_MAX == 2
    assert LoopState.SKILL_EXPLORE_MAX == 4


def test_needs_resume_checkpoint_continue_complete():
    assert needs_resume_checkpoint("继续完成刚才的任务") is True
    assert needs_resume_checkpoint("请继续完成") is True


def test_empty_skill_dir_probe():
    from app.services.agent_harness.model_driver import _is_empty_skill_dir_probe
    assert _is_empty_skill_dir_probe("bash", {"command": "ls /workspace/skills"}) is True
    assert _is_empty_skill_dir_probe("bash", {"command": "find /workspace/skills -maxdepth 2"}) is True
    assert _is_empty_skill_dir_probe("glob", {"pattern": "skills/**"}) is True
    assert _is_empty_skill_dir_probe("bash", {"command": "cat /workspace/skills/x/SKILL.md"}) is False
    assert _is_empty_skill_dir_probe(
        "bash", {"command": "python3 /workspace/skills/ppt/build.py --out /workspace/files/a.pptx"}
    ) is False
    assert _is_empty_skill_dir_probe("bash", {"command": "ls /workspace/files"}) is False


def test_strip_always_even_without_tools():
    from app.services.chat.turn_finalizer import scrub_false_tool_outage_claim
    out = scrub_false_tool_outage_claim(
        '我来查一下天气。今天晴。',
        tools_succeeded=False,
    )
    assert out == '今天晴。'

def test_skill_doc_reread_v250():
    """v2.96：只拦 SKILL.md/readme 主说明；组件/模板/设计说明放行。"""
    from app.services.agent_harness.model_driver import _is_skill_md_reread
    assert _is_skill_md_reread("read_file", {"path": "/workspace/skills/x/readme.md"}) is True
    assert _is_skill_md_reread("bash", {"command": "cat /workspace/skills/x/SKILL.md"}) is True
    # 组件/模板是动手所需——不得再 skill_md_reread_block
    assert _is_skill_md_reread("bash", {"command": "cat /workspace/skills/x/templates.md"}) is False
    assert _is_skill_md_reread("bash", {"command": "cat /workspace/skills/x/design.md"}) is False
    assert _is_skill_md_reread("bash", {"command": "cat /workspace/skills/x/components.md"}) is False
    assert _is_skill_md_reread("bash", {"command": "python3 /workspace/skills/x/build.py"}) is False


def test_chat_inline_image_only_goal_v250():
    from app.services.agent_harness.model_driver import _chat_inline_image_only_goal, _goal_looks_multi_step_product
    assert _chat_inline_image_only_goal("帮我看看漳州天气，配几张照片") is True
    assert _chat_inline_image_only_goal("做一份关于漳州天气的PPT，配几张照片") is False
    assert _chat_inline_image_only_goal("把这张图保存到我的文件") is False
    assert _goal_looks_multi_step_product("帮我做一份克莱汤普森PPT") is True
    assert _goal_looks_multi_step_product("今天天气怎么样") is False


def test_ppt_artifact_profile_cannot_be_downgraded_by_photo_only_followup():
    from app.services.agent_harness.model_driver import _effective_chat_lookup_goal

    profile = {
        "id": "artifact_coding",
        "artifact_kind": "presentation",
        "authoring_backend": "pptd",
    }
    assert _effective_chat_lookup_goal("换几张干净的风电场照片", profile) is False
    assert _effective_chat_lookup_goal("看看漳州天气，附几张照片", None) is True


def test_final_delivery_phrases_v250():
    from app.services.agent_harness.model_driver import _looks_like_final_delivery
    assert _looks_like_final_delivery("演示文稿可以下载了。") is True
    assert _looks_like_final_delivery("PPT已经做好，请查收") is True

def test_strip_double_mechanical_openers_v251():
    from app.services.chat.turn_finalizer import strip_leading_mechanical_ack
    out = strip_leading_mechanical_ack("好的。我先查一下天气。今天晴，26℃。")
    assert out.startswith("今天晴"), out
    out2 = strip_leading_mechanical_ack("我先查漳州实时天气，同时找东山岛的照片。漳州今天晴。")
    assert out2.startswith("漳州今天晴"), out2


def test_resume_search_restart_guard_present():
    from pathlib import Path
    src = Path("app/services/agent_harness/model_driver.py").read_text(encoding="utf-8")
    # Recovery facts are available to the model, but the Harness no longer blocks search or
    # forces a reuse decision from Chinese topic/file-name heuristics.
    assert "resume_block_search_restart" not in src
    assert "断点续做中：现场清单已有**与当前主题一致**的图片/成品文件" not in src
    assert "PPT 一次性沙箱续做：本轮还没 fetch_ppt_asset 成功时，必须允许重新搜图。" not in src
    assert "if _has_cp and _has_art and not _ppt_photos_unmet:" not in src

def test_chat_lookup_and_bash_http_v252():
    from app.services.agent_harness.model_driver import (
        _goal_is_chat_lookup,
        _goal_wants_workspace_product,
        _bash_is_http_side_channel,
        _trace_search_web_success_count,
        _chat_inline_image_only_goal,
    )
    assert _goal_is_chat_lookup("帮我看看今天漳州市的天气状况") is True
    assert _goal_is_chat_lookup("查一下漳州天气，并在对话里附上几张东山岛照片") is True
    assert _goal_wants_workspace_product("做一份关于漳州天气的PPT") is True
    assert _goal_is_chat_lookup("做一份关于漳州天气的PPT") is False
    # 2026-08-09：裸调研（未要求写报告/保存）默认对话交付，可走 lookup 纪律；
    # 明确要文件的调研才退出 lookup、开放写盘环。
    research = "我需要做一个市场调研，内容是关于现在的网页端的agent哪些比较好用"
    assert _goal_is_chat_lookup(research) is True
    assert _goal_is_chat_lookup(
        "做一份网页端 Agent 市场调研报告，保存到我的文件"
    ) is False
    from app.services.agent_harness.model_driver import _goal_wants_html_page_product
    assert _goal_wants_html_page_product(research) is False
    assert _bash_is_http_side_channel({"command": "curl -s https://example.com"}) is True
    assert _bash_is_http_side_channel({"command": "ls /workspace/files"}) is False
    assert _trace_search_web_success_count([
        {
            "name": "search_web",
            "status": "completed",
            "preview": "来源: 漳州天气 https://example.com/weather 今日 26℃",
        },
        {"name": "search_web", "status": "failed"},
    ]) == 1
    assert _chat_inline_image_only_goal("附上几张东山岛照片") is True


def test_collapse_repeated_answer_blocks_v252():
    from app.services.chat.turn_finalizer import collapse_repeated_answer_blocks
    text = (
        "今日漳州小雨转晴，26～35℃。" + chr(10)+chr(10) +
        "今日漳州小雨转晴，26～35℃。" + chr(10)+chr(10) +
        "午后注意防晒。"
    )
    out = collapse_repeated_answer_blocks(text)
    assert out.count("小雨转晴") == 1
    assert "午后注意防晒" in out


def test_chat_lookup_gate_markers_v252():
    from pathlib import Path
    src = Path("app/services/agent_harness/model_driver.py").read_text(encoding="utf-8")
    # 目标驱动升级：取消检索次数硬闸、强制收口和“先翻工作区”顺序引导。
    assert "net_chat_lookup_converge" not in src
    assert "chat_lookup_block_extra_search" not in src
    assert "chat_lookup_block_workspace_first" not in src
    # v2.97：bash 不再硬拦
    assert "chat_lookup_block_bash_http" not in src
    assert "chat_lookup_block_bash_first" not in src



def test_false_capability_nudge_v252():
    from app.services.agent_harness.model_driver import _should_nudge_false_capability_claim
    claim = "抱歉，联网查询服务当前被系统拦截，没法给出天气。"
    assert _should_nudge_false_capability_claim(
        round_content=claim,
        already_nudged=False,
        forced_final=False,
        tools_succeeded=False,
        search_available=True,
        chat_lookup=True,
    ) is True
    assert _should_nudge_false_capability_claim(
        round_content=claim,
        already_nudged=True,
        forced_final=False,
        tools_succeeded=False,
        search_available=True,
        chat_lookup=True,
    ) is False
    assert _should_nudge_false_capability_claim(
        round_content="今天漳州小雨 26℃。",
        already_nudged=False,
        forced_final=False,
        tools_succeeded=False,
        search_available=True,
        chat_lookup=True,
    ) is False


def test_resume_mode_requires_continue_v252():
    """Fresh weather must not enter resume_mode just because history has markers.

    v2.85: resume_mode follows user-wants-continue only; markers no longer AND-gate
    (missing markers used to force full restart on bare 继续). Self-inject covers holes.
    """
    from pathlib import Path
    from app.services.chat.turn_context_builder import needs_resume_checkpoint
    src = Path("app/services/agent_harness/model_driver.py").read_text(encoding="utf-8")
    assert "needs_resume_checkpoint as _need_resume_cp" in src
    assert "resume_mode = bool(_user_wants_resume)" in src
    assert "net_resume_marker_self_inject" in src
    assert needs_resume_checkpoint("继续") is True
    assert needs_resume_checkpoint("今天漳州天气怎么样") is False

def test_normalize_inline_image_refs_v252():
    from app.services.chat.turn_finalizer import normalize_inline_image_refs
    urls = ["https://example.com/a.jpg", "https://example.com/b.jpg"]
    out = normalize_inline_image_refs(
        "海岛如图：![a](https://example.com/a.jpg)", image_urls=urls,
    )
    assert "[图1]" in out
    out2 = normalize_inline_image_refs("有几张东山岛照片。", image_urls=urls)
    assert "[图1]" in out2 and "[图2]" in out2

def test_goal_looks_multi_step_staged_v252():
    from app.services.agent_harness.model_driver import _goal_looks_multi_step_product
    assert _goal_looks_multi_step_product(
        "请创建 report-resume-v252.md，两节：先只写背景，结论写 TODO，下一轮继续"
    ) is True
    assert _goal_looks_multi_step_product("今天天气怎么样") is False



def test_post_delivery_claim_with_tools_v253():
    """已有交付事实不应缩减模型工具能力；继续与否由模型和 Verifier 决定。"""
    from app.services.chat.run_policy import RunPolicySnapshot

    snapshot = RunPolicySnapshot(
        has_deliverable=True,
        delivery_checked=False,
        claimed_delivery=True,
    )
    assert snapshot.should_stop_post_delivery_batch(["edit_file"]) is False


def test_looks_like_final_delivery_zuowan_v253():
    from app.services.agent_harness.model_driver import _looks_like_final_delivery
    assert _looks_like_final_delivery("做完了，内容全都对，请查收。") is True
    assert _looks_like_final_delivery("已经做完，请查收") is True


def test_plan_toolish_humanize_v255():
    from app.services.agent_harness.model_driver import (
        _plan_titles_are_toolish,
        _humanize_toolish_plan_steps,
        _merge_keep_user_plan_titles,
    )
    toolish = [
        {"title": "加载所需技能", "status": "running"},
        {"title": "检索资料", "status": "pending"},
        {"title": "写入/生成文件", "status": "pending"},
    ]
    user = [
        {"title": "梳理结构与要点", "status": "running"},
        {"title": "写入正文内容", "status": "pending"},
        {"title": "核对并交付", "status": "pending"},
    ]
    assert _plan_titles_are_toolish(toolish) is True
    assert _plan_titles_are_toolish(user) is False
    hum = _humanize_toolish_plan_steps(toolish)
    assert hum[0]["title"] == "准备要用的能力"
    merged = _merge_keep_user_plan_titles(user, [
        {"title": "加载所需技能", "status": "completed"},
        {"title": "检索资料", "status": "running"},
        {"title": "写入/生成文件", "status": "pending"},
    ])
    assert merged[0]["title"] == "梳理结构与要点"
    assert merged[0]["status"] == "completed"
    assert merged[1]["status"] == "running"


def test_update_plan_keep_user_titles_marker_v255():
    from pathlib import Path
    src = Path("app/services/agent_harness/model_driver.py").read_text(encoding="utf-8")
    assert "net_plan_keep_user_titles" not in src
    assert "net_plan_humanize_toolish" not in src


def test_needs_resume_strips_inject_v256():
    from app.services.chat.turn_context_builder import needs_resume_checkpoint
    inj = "继续" + chr(10) + chr(10) + "【断点现场（平台注入，强制遵守）】" + chr(10) + "- draft.md"
    assert needs_resume_checkpoint(inj) is True
    inj2 = "继续完成刚才的PPT" + chr(10) + chr(10) + "【上轮对话锚点（同线程，强制沿用）】" + chr(10) + "x"
    assert needs_resume_checkpoint(inj2) is True
    assert needs_resume_checkpoint("帮我查漳州天气") is False


def test_provisional_plan_human_titles_v256():
    from app.services.agent_harness.model_driver import _provisional_plan_steps, _plan_titles_are_toolish
    steps = _provisional_plan_steps(["use_skill", "search_web", "write_file"])
    titles = [s["title"] for s in steps]
    assert "准备要用的能力" in titles
    assert "加载所需技能" not in titles
    assert _plan_titles_are_toolish(steps) is False


def test_loop_user_text_strips_inject_v256():
    from app.services.agent_harness.model_driver import _loop_user_text
    text = "继续" + chr(10) + chr(10) + "【断点现场（平台注入，强制遵守）】" + chr(10) + "- a.md"
    out = _loop_user_text(text, [])
    assert out.startswith("继续")
    assert "断点现场" not in out

def test_streaming_mechanical_stripper_v256():
    from app.services.chat.turn_finalizer import StreamingMechanicalStripper
    s = StreamingMechanicalStripper()
    assert s.feed("我先查漳州实时天气，同时搜东山岛的照片。") == ""
    out = s.feed("漳州今天晴，26℃。")
    full = (out or "") + s.flush()
    assert "我先查" not in full
    assert "漳州今天晴" in full
