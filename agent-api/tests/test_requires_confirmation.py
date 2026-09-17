# -*- coding: utf-8 -*-
"""确认后执行层（V3 §九）的触发准确性（2026-07-28）。

真机事故：调研任务问「发布时间」——裸词正则把名词性用法当成了外部动作，模型无端
收到「执行前必须先向用户确认」的指令。收紧成①名词后缀负向前瞻+②请求形态匹配后，
名词性提及不再触发；真正的外部/不可逆请求（发送/发布/删除/覆盖）照常触发，
并且用户明说「直接执行/不用确认」时豁免（那本身就是确认）。
"""
import pytest

from app.services.chat.turn_decision import decide_turn


def _d(msg: str):
    return decide_turn(msg, has_selected_files=False, active_run=False)


@pytest.mark.parametrize("msg", [
    # 事故原文：名词性「发布时间」出现在调研请求里
    "帮我联网调研 DeepSeek V4 系列模型的公开信息（发布时间、能力定位、和上一代的差异），"
    "整理成一份简短的选型建议 markdown，保存到我的文件，文件名：deepseek-v4-选型建议.md",
    "帮我写一份报告，统计一下测试覆盖率的变化",
    "查一下这个仓库最近的提交记录，整理成周报保存下来",
    "帮我整理发布会的要点，写成一份纪要文件",
])
def test_noun_usage_does_not_require_confirmation(msg):
    d = _d(msg)
    assert d.requires_confirmation is False, (
        f"名词性用法误触确认层：{msg[:30]}…——模型会无端收到「执行前必须确认」指令"
    )


@pytest.mark.parametrize("msg", [
    "帮我把这份报告发送给张三",
    "删除 hello.txt",
    "把整理好的文档发布到官网",
    "清空我的文件区",
])
def test_real_external_action_requires_confirmation(msg):
    d = _d(msg)
    assert d.requires_confirmation is True, f"真实外部/不可逆请求必须要求先确认：{msg}"
    assert "确认" in d.prompt_block(), "确认要求必须真的进入本轮行为约束提示词"


def test_direct_execute_waives_confirmation():
    d = _d("把这份报告发送给张三，直接执行不用确认")
    assert d.requires_confirmation is False, "用户明说不用确认=已授权，不再立确认标记"
    assert d.direct_execute is True


def test_confirmation_prompt_absent_when_not_required():
    d = _d("帮我写一份周报保存为 weekly.md")
    assert d.requires_confirmation is False
    assert "执行前必须先向用户确认" not in d.prompt_block()
