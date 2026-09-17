# -*- coding: utf-8 -*-
"""计划轮续接必须改写 messages[0]（2026-07-28）。

事故形态：用户点「开始执行」后，工具集已按 tool_env 快照放开写权限，而 messages[0]
仍是计划轮那份 system prompt——里面写着「本轮到此为止，不要创建、编辑、覆盖任何文件，
用户点开始执行之后的下一轮才动手」。"下一轮"是什么，模型从消息序列里无从判断。

挂起游标带走的是整条 messages，而 drive_model 在 initial_messages 非空时**完全
忽略** system_prompt 参数，所以 messages[0] 就是模型这一轮唯一的系统指令——不改写它，
就是给模型两条相反的指令。

真机实测里 deepseek-v4-flash 两个分支都表现正确（点执行真的写了文件、点改一改没写），
所以这是**纵深防御缺口而非可复现故障**——但两边指令相反本身就不该留着。
"""
from app.services.chat.turn_context_builder import (
    _PLAN_GUARD_ANCHOR,
    _tool_env_snapshot,
    rewrite_plan_guard_for_execution,
)
from app.services.chat.turn_decision import TurnDecision


def _plan_prompt() -> str:
    """真实的计划轮 system prompt（基础段 + 计划约束段），与生产同源。"""
    block = TurnDecision(
        intent="execute", authority="inspect", reason_code="plan_mode", plan_mode=True,
    ).prompt_block()
    return "你是助手，可以使用工具。\n\n" + block


def test_anchor_still_matches_the_real_plan_prompt():
    """锚点必须真的出现在生产提示词里——失配就等于这条改写静默失效。

    这是本文件最重要的一条：改写靠一句原文做锚，文案一改锚点就失配，而失配是**静默**的
    （rewrite 返回 False，续接轮照旧带着「不要动手」跑）。这条断言让文案改动必须显式同步。
    """
    assert _PLAN_GUARD_ANCHOR in _plan_prompt(), (
        f"计划轮提示词里已经没有锚点 {_PLAN_GUARD_ANCHOR!r} —— "
        "要么同步 _PLAN_GUARD_ANCHOR，要么这条续接改写从此不再生效")


def test_rewrite_drops_the_do_not_act_constraint():
    messages = [{"role": "system", "content": _plan_prompt()},
                {"role": "user", "content": "先给我一份计划"}]

    assert rewrite_plan_guard_for_execution(messages) is True

    out = messages[0]["content"]
    assert "不要创建、编辑、覆盖任何文件" not in out
    assert "先规划，不动手" not in out
    assert "用户点「开始执行」之后的下一轮才动手" not in out
    assert "规划阶段到此结束" in out
    # 替换文案不该去引用一段模型已经看不到的原文（那会指向空气）
    assert "前面那条" not in out
    # 基础段（工具能力等）必须保留——只砍计划约束那一段
    assert "你是助手，可以使用工具。" in out
    # 其余消息不动
    assert messages[1] == {"role": "user", "content": "先给我一份计划"}


def test_rewrite_removes_the_confirm_card_instruction():
    """计划约束段里还有「必须调用 ask_user_choice 出确认卡」——留着模型会在执行轮又弹一张。"""
    messages = [{"role": "system", "content": _plan_prompt()}]
    rewrite_plan_guard_for_execution(messages)
    assert "ask_user_choice" not in messages[0]["content"]


def test_rewrite_injects_execution_guard_without_the_anchor():
    """锚点丢失时仍注入执行指令，否则模型会继续以为自己在计划模式。"""
    messages = [{"role": "system", "content": "一段与计划模式无关的提示词"}]
    assert rewrite_plan_guard_for_execution(messages) is True
    assert "规划阶段到此结束" in messages[0]["content"]
    assert "一段与计划模式无关的提示词" in messages[0]["content"]


def test_rewrite_finds_anchor_beyond_the_first_system_message():
    messages = [
        {"role": "system", "content": "前置上下文"},
        {"role": "system", "content": _plan_prompt()},
    ]
    assert rewrite_plan_guard_for_execution(messages) is True
    assert "先规划，不动手" not in messages[1]["content"]
    assert "规划阶段到此结束" in messages[1]["content"]


def test_rewrite_inserts_guard_when_head_is_not_system():
    messages = [{"role": "user", "content": "x"}]
    assert rewrite_plan_guard_for_execution(messages) is True
    assert messages[0]["role"] == "system"
    assert "规划阶段到此结束" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "x"}
    assert rewrite_plan_guard_for_execution([]) is False


def test_snapshot_carries_plan_mode():
    """快照不带 plan_mode，续接侧就无从判断该不该改写。"""
    snap = _tool_env_snapshot(
        action_authority="inspect", turn_intent="execute", revision_mode=False,
        allow_create=True, revision_target=None, skill_ids=[], user_message="先给我个计划",
        attachments=None, plan_mode=True,
    )
    assert snap["plan_mode"] is True
    # 默认不开：普通轮次不该被当成计划轮改写
    plain = _tool_env_snapshot(
        action_authority="mutate", turn_intent="execute", revision_mode=False,
        allow_create=True, revision_target=None, skill_ids=[], user_message="做个 PPT",
        attachments=None,
    )
    assert plain["plan_mode"] is False


# ---- 计划模式开关的粘性（2026-07-28）----
# 计划模式的正常终点是 waiting_user（挂在确认卡上），前端 settleTaskMode 只在**真终态**
# 拨回开关，所以它会一直亮着。此时用户打「取消」「继续」这类控制语，本来只是想停下来，
# 却被 is_plan_profile 无条件改写成「请给我一份完整的计划报告」，又收到一份计划。
# auto 路由那条路本来就有 authority 守卫，显式开关这条此前没有。
def test_control_messages_do_not_become_plan_requests():
    """裸控制语不许被计划模式开关变成一份计划报告。

    2026-07-29 判据换向：原先断言 `authority not in (inspect, mutate)` —— 那是拿**授权**
    当"是不是控制语"的代理判据。「继续」在无进行中 Run 时其实是"接着把活干完"、需要写
    权限（扣押轮的唯一出口就是让用户回这句），一旦它变成 mutate，靠 authority 认控制语的
    守卫就静默失效。现在两件事各问一次：授权按语义给，控制语由 bare_control_message 答，
    chat_service 的守卫改用后者。这条测试跟着断**真判据**。
    """
    from app.services.chat.turn_decision import bare_control_message

    for text in ("取消", "停止", "继续", "暂停", "继续吧"):
        assert bare_control_message(text) is True, (
            f"{text!r} 没被认成裸控制语——计划模式开关会把它变成一次完整的计划报告")


def test_stop_words_keep_zero_authority():
    """「暂停/取消」这类**停止**语义仍必须零授权（它们任何时候都不是写请求）。

    与上一条分开：停止类的授权契约没变，只有「继续」放开了。
    """
    from app.services.chat.turn_decision import decide_turn

    for text in ("取消", "停止", "暂停"):
        d = decide_turn(text, has_selected_files=False, active_run=False)
        assert d.authority == "none", f"{text!r} 是「别做了」，不该拿到写权限"


def test_continue_gets_write_authority_without_active_run():
    """扣押轮的出口：无进行中 Run 时「继续」必须能写，否则引导语指向一个更紧的边界。"""
    from app.services.chat.turn_decision import decide_turn

    assert decide_turn("继续", has_selected_files=False, active_run=False).authority == "mutate"
    assert decide_turn("继续", has_selected_files=False, active_run=True).authority == "none", (
        "有进行中 Run 时它仍是 Run 控制词")


def test_real_work_still_enters_plan_mode():
    """守卫不能误伤真正的干活消息，否则计划模式整个失效。"""
    from app.services.chat.turn_decision import bare_control_message, decide_turn

    for text in ("帮我做一份PPT", "先给我一份计划", "改一下第二页"):
        d = decide_turn(text, has_selected_files=False, active_run=False)
        assert d.authority in ("inspect", "mutate"), f"{text!r} 不该被守卫挡掉"
        assert bare_control_message(text) is False, f"{text!r} 不是控制语，不该被守卫拦下"


# ---- 计划轮续接必须升到 mutate（2026-07-28 真机事故）----
# 用户点「开始执行」后，模型回「因当前是规划轮，实际动手将在下一轮进行……等待下一轮启动」，
# 并在自查里写明原因：「当前会话可用工具列表中不包含 bash 和 write_file」。
#
# 根因是「按快照恢复授权」这个修复本身：它对其它挂起场景都对（只读轮挂起一次不该换回写
# 工具），唯独对计划模式正好是反的——计划轮快照里存的就是 inspect，忠实恢复 = 执行轮永远
# 只读。计划模式的整个设计是「计划轮只读、确认后的那一轮放开」，续接点是唯一的放开处。
def test_plan_resume_escalates_to_mutate():
    """从 _resume_orchestration 源码断言：只有用户明确批准执行才把授权升到 mutate。"""
    import inspect

    from app.services.agent_harness.orchestrator import HarnessOrchestrator

    src = inspect.getsource(HarnessOrchestrator._resume_orchestration)
    assert "resume_value" in inspect.signature(HarnessOrchestrator._resume_orchestration).parameters
    from pathlib import Path
    orch_src = Path(__file__).resolve().parents[1].joinpath(
        "app/services/agent_harness/orchestrator.py"
    ).read_text(encoding="utf-8")
    assert "resume_value=resume_value" in orch_src
    assert 'hung_in_plan_mode = bool(tool_env.get("plan_mode"))' in src, "没读快照里的 plan_mode"
    assert "approve_exec = choice_approves_plan(resume_value) or choice_approves_plan(fed)" in src
    assert "plan_execution_already_unlocked" in src
    assert "persist_plan_execution_unlock" in src
    assert "exit_plan_mode_tool_env" in src
    assert 'env_authority = "mutate" if executing_now' in src, (
        "计划轮续接没在批准执行时升到 mutate —— 用户点了「开始执行」却拿不到 bash/write_file")
    assert "env_allow_create = True if executing_now" in src, (
        "执行轮要能产出新文件，allow_create 不该被计划轮快照限制")
    assert "executing_now and rewrite_plan_guard_for_execution" in src, (
        "「我要改一改」不得改写执行 guard，否则批准失去可验证含义")
    assert "_PLAN_EXECUTION_GUARD" in src
    assert "plan_mode=bool(env_plan_mode)" in src
    assert "choice_skips_plan(resume_value)" in src
    assert '"capability_scope": "default"' in src
    assert "_unlock_plan_execution_before_resume" in orch_src


def test_resume_api_promotes_capability_scope_only_after_execute_approval():
    from pathlib import Path

    src = Path(__file__).resolve().parents[1].joinpath("app/api/router.py").read_text(
        encoding="utf-8"
    )
    assert '"capability_scope": (' in src
    assert '"default" if next_phase == "executing" else "planning"' in src
    assert 'state_patch["agent_mode"] = "standard"' in src
    assert "exit_plan_mode" in src
    assert "persist_plan_execution_unlock" in src


def test_execution_guard_covers_approved_execution_only():
    """执行 guard 只覆盖「已确认执行」；改计划仍停在计划轮，不再写「要求修改后本轮做完」。"""
    from app.services.chat.turn_context_builder import _PLAN_EXECUTION_GUARD as g

    assert "只读约束已经解除" in g
    assert "用户已经确认执行这份计划" in g
    assert "不要再弹出让用户批准修订的选项卡" in g
    assert "要求修改" not in g
    assert "在本轮继续做完" not in g
    assert "下一轮" in g and "没有下一轮在等着你" in g


def test_plan_mode_turn_itself_is_still_read_only():
    """放开只发生在续接轮——计划轮本身必须仍是只读，否则整个模式就没意义了。"""
    import inspect

    from app.services.agent_harness import orchestrator as cs

    src = inspect.getsource(cs.HarnessOrchestrator._chat_stream_body) if hasattr(
        cs.HarnessOrchestrator, "_chat_stream_body") else ""
    # 计划轮把授权压成 inspect 的那行（在 stream 主体里）
    full = inspect.getsource(cs)
    assert 'replace(turn_decision, authority="inspect", plan_mode=True)' in full, (
        "计划轮不再压成 inspect —— 那模型在规划阶段就能动手了")
    assert src is not None
