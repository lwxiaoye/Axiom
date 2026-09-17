# -*- coding: utf-8 -*-
"""PPT 语境的多轮延续（2026-07-27 真机事故回归）。

事故复盘：
    18:21 用户：我想做一份ppt                    → 命中，但这轮助手只是反问，没干活
    18:21 助手：请问主题是什么？多少页？受众是谁？
    18:22 用户：主题是关于kimi k3的模型能力宣讲，竞品分析报告，需要10页，受众是ai开发重度使用者
                ↑ 真正干活的那轮，**文字里没有"PPT"二字** → 判定 False → 技能不追加
    18:26 产出：模型自己用 python-pptx 手搓的深色 10 页，**没有 .slides.json**（编辑器进不去）

「做 PPT 前先问清主题/页数/受众」是我们自己要求的行为，判定却只看当轮，
等于自己把自己的技能挡在门外。
"""
import pytest

from app.services.skills import ppt_policy as p

PPT_SKILL = {
    "skillId": "s-ppt", "id": "s-ppt", "name": "ppt studio", "enabled": 1,
    "description": "用 HTML 设计系统逐页生成演示文稿，一键编译成文字可编辑的原生 PPTX。",
}

# 真机原话，一个字没改
TURN1 = "我想做一份ppt"
TURN2 = "主题是关于kimi k3的模型能力宣讲，竞品分析报告，需要10页，受众是ai开发重度使用者"


def test_the_actual_incident_message_alone_does_not_look_like_ppt():
    """先钉住事故的前提：干活那轮单看确实不像 PPT 请求——这不是判据写错，是它只看了一轮。"""
    assert p.is_ppt_artifact_request(TURN1) is True
    assert p.is_ppt_artifact_request(TURN2) is False


def test_context_carries_the_intent_forward():
    assert p.ppt_intent_in_context(TURN2, None, [TURN1]) is True


def test_skill_is_not_appended_by_preparation_on_the_turn_that_actually_works():
    """历史语境可以作为事实，但 preparation 不替模型静默选择生成 Skill。"""
    got = p.effective_ppt_skill_ids(TURN2, [], [PPT_SKILL], None, recent_user_messages=[TURN1])
    assert got == []


def test_without_context_it_still_fails_closed():
    """不传历史时行为与修复前一致——调用方没接上历史就不该悄悄改变语义。"""
    assert p.effective_ppt_skill_ids(TURN2, [], [PPT_SKILL]) == []


def test_lookback_window_is_bounded():
    """只看最近 3 条用户消息：早上做过 PPT、下午问别的，不该一直带着技能。"""
    old = [TURN1, "帮我查个资料", "今天天气怎么样", "解释一下这段代码"]
    assert p.ppt_intent_in_context("这个函数是干嘛的", None, old) is False


def test_recent_ppt_request_within_window_counts():
    assert p.ppt_intent_in_context("那就10页吧", None, ["帮我做个PPT", "嗯"]) is True


def test_explicit_selection_still_wins():
    """用户自己选了 PPT 技能时不追加第二个——2026-07-24 那条口径不能被本次修复破坏。"""
    other = {"skillId": "s-other", "id": "s-other", "name": "PPTX 大师", "enabled": 1, "description": "ppt"}
    got = p.effective_ppt_skill_ids(
        TURN2, ["s-other"], [PPT_SKILL, other], None, recent_user_messages=[TURN1],
    )
    assert got == ["s-other"]


@pytest.mark.parametrize("msg", [
    "帮我写个 word 文档", "把这个表格整理成 excel", "这段代码报错了怎么改",
])
def test_unrelated_requests_do_not_borrow_ppt_context(msg):
    """明确在做别的产物时，不该因为上一轮提过 PPT 就把技能塞进来。

    注意：当前实现是「看回窗口内有 PPT 请求就算」，所以这条在**没有 PPT 历史**时才成立；
    真要在有 PPT 历史时也排除，得加产物类型互斥判定——记在这里，别当它已经做了。
    """
    assert p.ppt_intent_in_context(msg, None, ["帮我查点资料"]) is False
