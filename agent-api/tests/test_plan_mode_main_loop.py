# -*- coding: utf-8 -*-
"""计划模式走主循环、不进 DAG（2026-07-27 用户拍板）。

原话：「现在就不需要有 dag 了」/「跑最原始的 loop 已经够用了」/「跑 plan mode 的时候，模型会
向用户澄清需求，问清楚用户要什么之后，整理成一份完整的计划报告，然后根据计划去一步步执行」。

关键设计：计划模式**不靠 prompt 求模型别动手**，而是把授权压成 inspect —— Harness 的铁律是
「工具集合本身即授权边界」，于是 build_tools 这一轮**物理上**不给写工具。

顺带解决的两件事（都写成断言，免得以后被"优化"掉）：
1. 前摇：DAG 路径要先阻塞跑完 investigate（≤4 轮）+ plan_graph（1~2 轮）才出第一个字，
   最坏 95s 全静默；计划变成正文之后第一个 token 立刻就到。
2. 「整图被拒 → 规划未通过校验 → 回退普通执行」这一整类失败消失 —— 模型写不出"不合法的散文"。
"""
import pytest
import inspect
from pathlib import Path

from app.services.chat.tools import build_tools
from app.services.chat.turn_decision import TurnDecision


def _plan_decision() -> TurnDecision:
    return TurnDecision(
        intent="execute", authority="inspect", reason_code="plan_mode", plan_mode=True)


def test_plan_mode_block_forbids_acting_and_demands_a_report():
    block = _plan_decision().prompt_block()
    assert "先规划，不动手" in block
    assert "update_plan" in block
    # Codex Plan Mode：环境落地 → 意图收敛 → 实施收敛，最终决策完备。
    for part in (
        "环境落地（先查后问）",
        "意图收敛",
        "实施收敛",
        "无需再替你做产品或技术决策",
        "<proposed_plan>",
        "## Summary",
        "## Key Changes",
        "## Test Plan",
        "## Assumptions",
        "公开 API、接口、schema、type、I/O",
    ):
        assert part in block, f"Codex 风格计划契约里缺 {part}"
    assert "能从代码、文件、系统或已有材料确认的事实自己确认" in block
    assert "一次性调用 `ask_user_choice`" in block
    assert "按子系统或行为分组" in block
    assert "完整替代计划" in block
    # 本轮到此为止
    assert "不要创建、编辑、覆盖任何文件" in block
    # 计划末尾必须出一张可点的确认卡（用户明确要「计划卡」）。走现成的 ask_user_choice：
    # 它在流式路径里无条件注册（main_tool_turn.py，在 build_tools 之外），HITL 挂起/续接
    # 链路是现成的 —— 不需要为出卡新造机制。
    assert "ask_user_choice" in block
    assert "开始执行" in block
    assert "只给「开始执行」" in block
    assert "不要自造" in block
    assert "一整份新的计划报告" in block
    assert "好，请执行此计划。" in block
    assert "跳过" in block


def test_proposed_plan_block_becomes_one_public_plan_item():
    from app.services.agent_harness.plan_content import (
        extract_proposed_plan,
        public_plan_text,
    )

    raw = (
        "这段块外说明不应进入计划卡。\n"
        "<proposed_plan>\n"
        "# 修复主 Agent\n\n"
        "## Summary\n完成协议修复。\n\n"
        "## Key Changes\n- 修复终态。\n\n"
        "## Test Plan\n- 覆盖失败路径。\n\n"
        "## Assumptions\n- 无。\n"
        "</proposed_plan>\n"
        "块外尾注也不进入卡片。"
    )
    body = extract_proposed_plan(raw)
    assert body is not None
    assert body.startswith("# 修复主 Agent")
    assert "块外" not in body
    assert public_plan_text(raw) == body


def test_unclosed_proposed_plan_at_completed_eof_is_still_renderable():
    from app.services.agent_harness.plan_content import extract_proposed_plan

    assert extract_proposed_plan(
        "<proposed_plan>\n# 标题\n\n## Summary\n计划正文"
    ) == "# 标题\n\n## Summary\n计划正文"


def test_plan_resume_transcript_keeps_visible_choice_and_hides_skip_control():
    from app.services.agent_harness.orchestrator import (
        HarnessOrchestrator,
        _plan_resume_transcript_text,
    )

    assert _plan_resume_transcript_text("好，请执行此计划。") == "好，请执行此计划。"
    assert _plan_resume_transcript_text(["收窄范围", "保留表格"]) == "收窄范围 保留表格"
    assert _plan_resume_transcript_text("__PLAN_SKIP__") == ""
    assert _plan_resume_transcript_text("跳过") == ""

    source = inspect.getsource(HarnessOrchestrator.resume_chat)
    accepted = source.index("consume_resume_token")
    persisted = source.index("persist_user_message")
    published = source.index("yield channel.input_accepted()")
    assert accepted < persisted < published


def test_plan_mode_does_not_leak_into_normal_turns():
    normal = TurnDecision(intent="execute", authority="mutate", reason_code="x")
    assert normal.plan_mode is False
    assert "先规划，不动手" not in normal.prompt_block()


@pytest.mark.asyncio
async def test_plan_mode_turn_gets_no_write_tools():
    """授权边界靠工具集合，不靠提示词——这条是计划模式"不会动手"的**物理**保证。"""
    tools = await build_tools(
        token="t", knowledge_ids=None, web_enabled=True, user_id="u1", thread_id="th1",
        newapi_key="k", run_id="r1", user_message="先给我个计划",
        turn_intent="execute", action_authority="inspect",
    )
    from app.services.agent_harness.contracts import RunSnapshot
    from app.services.agent_harness.policy import HarnessPolicy
    run = RunSnapshot(
        run_id="r1", thread_id="th1", user_id="u1", agent_mode="plan",
        phase="planning", capability_scope="planning", state_version=0,
        goal_revision=0, plan_version=0, event_cursor=0,
        execution_profile={"id": "interactive"},
    )
    visible = [tool for tool in tools if HarnessPolicy().evaluate(tool.spec, run).allowed]
    names = {tool.name for tool in visible}
    writers = names & {"write_file", "edit_file"}
    assert not writers, f"计划模式这一轮不该有任何写工具，实际漏出：{writers}"
    # 读取与临时计算能力必须在，否则计划只能凭空生成。
    assert {"read_file", "glob", "bash"} <= names
    bash = next(tool for tool in visible if tool.name == "bash")
    assert bash.spec.effect_scope.value == "scratch"
    assert "artifact_producer" not in bash.spec.semantic_tags


@pytest.mark.asyncio
async def test_plan_confirmation_allows_single_start_option():
    from app.services.chat.tools import SubagentNeedsInput, ToolSoftError
    from app.services.agent_harness.model_driver import build_ask_user_tool

    tool = build_ask_user_tool()
    with pytest.raises(SubagentNeedsInput) as raised:
        await tool.execute({
            "question": "这份计划可以开始执行吗？",
            "options": [{"label": "开始执行"}],
        })
    result = raised.value.result
    assert result["ask_user"] is True
    options = result["interactive"]["params"]["userSelectOptions"]
    assert [item["value"] for item in options] == ["开始执行"]

    with pytest.raises(ToolSoftError, match="至少需要 2"):
        await tool.execute({
            "question": "选一个方向",
            "options": [{"label": "只要这一个"}],
        })


def test_platform_forces_plan_confirmation_when_model_skips_ask_user():
    from app.services.agent_harness.model_driver import (
        build_forced_plan_confirmation_suspend,
        should_force_plan_confirmation,
    )

    report = (
        "## 计划\n\n**Context**：把用户给的 Markdown 转成可演讲的 PPT，"
        "先核对现有材料再写步骤。\n\n**Phase A**：读文件\n\n**验收**：文件可打开"
    )
    assert should_force_plan_confirmation(
        plan_mode=True, already_forced=False, plan_steps=[], answer=report,
    )
    assert should_force_plan_confirmation(
        plan_mode=True,
        already_forced=False,
        plan_steps=[{"title": "调研", "status": "pending"}],
        answer="短句",
    )
    assert not should_force_plan_confirmation(
        plan_mode=False, already_forced=False, plan_steps=[{"title": "x"}], answer=report,
    )
    assert not should_force_plan_confirmation(
        plan_mode=True, already_forced=True, plan_steps=[{"title": "x"}], answer=report,
    )
    assert not should_force_plan_confirmation(
        plan_mode=True, already_forced=False, plan_steps=[], answer="先看一下材料。",
    )

    messages: list = []
    event = build_forced_plan_confirmation_suspend(
        messages=messages, answer_so_far=report, trace=[],
    )
    assert event["type"] == "suspend"
    assert event["name"] == "ask_user_choice"
    assert event["args"]["options"] == [{"label": "开始执行"}]
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["tool_calls"][0]["function"]["name"] == "ask_user_choice"


def test_plan_mode_must_not_close_steps_before_user_agrees():
    src = Path(__file__).resolve().parents[1].joinpath(
        "app/services/agent_harness/model_driver.py"
    ).read_text(encoding="utf-8")
    assert "net_force_plan_confirmation" in src
    assert "should_force_plan_confirmation(" in src
    assert "plan_mode=plan_mode" in src
    # Plan 模式只通过确认挂起离开本轮；不得在确认前用清理 helper 伪造完成。
    assert "_close_incomplete_plan_steps(st.latest_plan_steps" not in src
