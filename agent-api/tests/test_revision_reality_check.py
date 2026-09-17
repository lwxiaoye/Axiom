# -*- coding: utf-8 -*-
"""修订误判的第三次真机事故回归（2026-07-29）+ 现实校验治本层。

真机复测（GPT 独立评测轮）：全新对话首轮「请联网调研…遇到网页抓取失败请自行换路；
最后保存为 Markdown 到“我的文件”，文件名 agent-benchmark-live-test.md」被判
revise+allow_create=False——「换路」的「换」触发修订动作、带引号的『我的文件』绕过
剔除正则。写工具全体扣押 11 分钟，模型绕路吐 HTML 并在被人工停止后宣称完成。

三层防线，每层单独可测：
①「换」排除改变做法义；②「我的文件」仅后跟里/中/下才算指向既有内容；
③现实校验纯函数——点名的文件在文件区不存在 ⇒ 修订降级新建（chat_service 挂钩）。
"""
import pytest

from app.services.chat.turn_decision import decide_turn, named_missing_file

BENCHMARK_MSG = (
    "请联网调研截至 2026-07-28 的网页端一线 Agent（ChatGPT Agent 模式、Claude.ai、Manus），"
    "围绕任务达成、规划纠错、工具效率、信息记忆、稳定性整理一份可核验的对照证据清单。"
    "要求：优先引用官方一手来源；每条事实带来源链接；区分官方宣称与可验证限制；"
    "证据不足明确标注，不要编造；遇到网页抓取失败请自行换路；"
    "最后保存为 Markdown 到“我的文件”，文件名 agent-benchmark-live-test.md。完成后告诉我真实保存结果。"
)


def test_benchmark_incident_message_is_execute_not_revise():
    d = decide_turn(BENCHMARK_MSG, has_selected_files=False, active_run=False)
    assert d.revision is False, "「自行换路」+『我的文件』不得再把全新调研判成修订"
    assert d.allow_create is True
    assert d.intent == "execute"


@pytest.mark.parametrize("msg", [
    "遇到抓取失败请自行换路，换个思路继续",
    "这个方案不行就换种方式，换一批关键词再搜",
])
def test_strategy_switching_words_are_not_revision(msg):
    assert decide_turn(msg + "，最后写份总结", has_selected_files=False,
                       active_run=False).revision is False


@pytest.mark.parametrize("msg,expect", [
    ("把这份文档的标题换成蓝色", True),
    ("把我的文件里的选型建议改成两页", True),
    ("帮我把报告里的旧数据换掉", True),
])
def test_true_revision_verbs_still_work(msg, expect):
    assert decide_turn(msg, has_selected_files=False, active_run=False).revision is expect


def test_quoted_myfiles_destination_is_not_existing_target():
    d = decide_turn("整理一份周报，保存为 Markdown 到“我的文件”", has_selected_files=False,
                    active_run=False)
    assert d.revision is False and d.allow_create is True


def test_unresolved_target_guidance_gives_boundary_and_exit():
    """2026-08-05 harness：目标未解析时写工具物理未开放；须 ask_user_choice 确认卡。"""
    from app.services.chat.turn_decision import REVISION_TARGET_UNRESOLVED_GUIDANCE as g
    assert "未能唯一确定" in g or "目标文件" in g
    assert "ask_user_choice" in g
    assert "未开放" in g or "收起" in g
    assert "list_files" not in g, "list_files 已退休，应引导 glob/read_file"
    assert "glob" in g or "read_file" in g
    assert "不要另起新文件" in g or "另起新文件" in g
    assert "贴在聊天" in g or "交差" in g
    assert "选择卡" in g or "ask_user_choice" in g



def test_named_missing_file_reality_check():
    assert named_missing_file(BENCHMARK_MSG, {"别的.md", "hello.txt"}) == "agent-benchmark-live-test.md"
    assert named_missing_file(BENCHMARK_MSG, {"agent-benchmark-live-test.md"}) == ""
    assert named_missing_file(BENCHMARK_MSG, {"AGENT-BENCHMARK-LIVE-TEST.MD"}) == "", "大小写不敏感"
    assert named_missing_file("把刚才的报告改一下", {"任意.md"}) == "", "无点名文件名=不降级，等用户选目标"
