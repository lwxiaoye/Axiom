# -*- coding: utf-8 -*-
"""Deep Research 复用主工具循环与 ask_user_choice 的回归约束。"""
import pytest

from app.services.chat.tools import SubagentNeedsInput, ToolSoftError
from app.services.chat.turn_decision import TurnDecision
from app.services.agent_harness.model_driver import build_ask_user_tool


def _research_decision() -> TurnDecision:
    return TurnDecision(
        intent="execute",
        authority="mutate",
        reason_code="research_profile",
        research_profile=True,
    )


def test_research_prompt_requires_clarification_plan_evidence_and_citations():
    block = _research_decision().prompt_block()
    assert "Deep Research" in block
    assert "questions" in block
    assert "最多 3 题" in block
    assert "update_plan" in block
    assert "Bash" in block
    assert "交叉验证" in block
    assert "引用" in block
    assert "不再要求用户确认计划" in block
    assert "必须使用 Markdown 井号标题" in block
    assert "覆盖普通对话「禁止井号标题」" in block
    assert "deep_read" not in block
    assert "多角度换词检索" in block


def test_research_system_prompt_overrides_hash_heading_ban():
    from app.services.chat.turn_context_builder import _build_system_prompt

    normal = _build_system_prompt(
        agents=[], trusted_skills=[], selected_knowledge=[], knowledge_ids=[], memory_block="",
    )
    research = _build_system_prompt(
        agents=[], trusted_skills=[], selected_knowledge=[], knowledge_ids=[], memory_block="",
        research_profile=True,
    )
    assert "禁止" in normal and "#、##、###" in normal
    assert "Markdown 井号标题" in research
    assert "- **禁止**使用 #、##、###" not in research


def test_research_mode_does_not_leak_into_normal_turns():
    normal = TurnDecision(intent="execute", authority="mutate", reason_code="normal")
    assert normal.research_profile is False
    assert "Deep Research" not in normal.prompt_block()


@pytest.mark.asyncio
async def test_ask_user_choice_can_suspend_with_three_questions_at_once():
    tool = build_ask_user_tool()
    with pytest.raises(SubagentNeedsInput) as raised:
        await tool.execute({
            "questions": [
                {
                    "question": "这次竞品分析主要用来做什么？",
                    "options": [
                        {"label": "做产品/商业分析", "recommended": True},
                        {"label": "辅助个人购买"},
                    ],
                },
                {
                    "question": "重点关注哪个价格区间？",
                    "options": [{"label": "1000元以下"}, {"label": "1000-3000元"}],
                },
                {
                    "question": "覆盖哪些品牌？",
                    "options": [{"label": "国产品牌为主"}, {"label": "国内外都要"}],
                },
            ],
        })

    result = raised.value.result
    assert result["ask_user"] is True
    assert result["interactive"]["type"] == "userQuestions"
    questions = result["interactive"]["params"]["questions"]
    assert len(questions) == 3
    assert questions[0]["key"] == "这次竞品分析主要用来做什么？"
    assert questions[0]["options"][0]["recommended"] is True
    assert all(question["allow_custom"] for question in questions)


@pytest.mark.asyncio
async def test_ask_user_choice_rejects_more_than_three_questions():
    tool = build_ask_user_tool()
    message = "questions 一次最多提 3 题"
    with pytest.raises(ToolSoftError, match=message):
        await tool.execute({
            "questions": [
                {"question": f"问题{i}", "options": [{"label": "A"}, {"label": "B"}]}
                for i in range(4)
            ],
        })
