# -*- coding: utf-8 -*-
"""「禁止新建」必须有指代证据（2026-07-29 深扫治本层）。

背景：判定词表被击穿四次（"建议" / "我的文件" / "换路" / 以及本次批量确认的
"整改|改造|改版" 类名词），每次都是给词表加负向排除，补不完。根因是
_EXISTING_TARGET_RE 收录了「文档|报告|表格|ppt|代码」这些**新交付物的类名**，
于是"是否在改已有东西"退化成"句子里有没有一个动作词"——实测 12/15 条纯新建
话术被判成修订、写工具全体扣押（=11 分钟真机事故的同一形态）。

判据换向：扣押写工具这个高代价动作必须有**指代既有产物的证据**——
①用户勾了文件 ②明确回指（"刚才那份"/"原文件"/"第 3 页"）③点名了真实存在的文件。
类名词不算证据。三者都没有时按新建放行。
"""
import pytest

from app.services.chat.turn_decision import (
    anaphoric_target_reference,
    decide_turn,
    named_existing_file,
    named_files,
    named_missing_file,
    revision_lock_justified,
)


# ---------- 无证据：一律不许扣押 ----------

@pytest.mark.parametrize("msg", [
    "帮我写一份整改报告",
    "帮我做一份数据库改造方案文档",
    "生成一份改版说明文档",
    "统计这个月的 bug 修复情况，做成表格",
    "帮我做个单位换算表格",
    "帮我写一份视频编辑教程文档",
    "分析一下这次组织调整的影响，写份报告",
    "帮我整理一份关于机房选型的建议文档",
    "调研三家供应商，输出一份带建议的报告",
    "收集用户的想法整理成一份文档",
    "我觉得需要一份竞品分析报告，帮我做一份",
])
def test_neutral_new_artifact_requests_never_lock_writes(msg):
    assert revision_lock_justified(msg, has_selected_files=False, existing_names=set()) is False, (
        f"「{msg}」是纯新建请求，扣押写工具=11 分钟事故重演"
    )


def test_new_ppt_page_spec_is_not_misrouted_as_existing_file_revision():
    msg = (
        "请用当前最新 ppt-studio 制作并实际发布一份 6 页中文 PPTX；"
        "第2页必须是纯排版数据页，第3页时间轴，第5页多图拼贴。"
    )
    decision = decide_turn(msg, has_selected_files=False, active_run=False)

    assert decision.allow_create is True
    assert decision.revision is False or decision.intent == "execute"


# ---------- 有证据：保持扣押（确认卡链路该在的场景） ----------

@pytest.mark.parametrize("msg", [
    "把刚才那份 PPT 的封面改成蓝白色调",
    "把原来的方案优化一下",
    "这个文件的第 3 页标题改短一点",
    "你之前写的那份报告改简洁些",
])
def test_anaphoric_reference_keeps_lock(msg):
    assert anaphoric_target_reference(msg) is True
    assert revision_lock_justified(msg, has_selected_files=False, existing_names=set()) is True


def test_selected_files_keeps_lock():
    assert revision_lock_justified("按上面的要求优化并保存", has_selected_files=True,
                                   existing_names=set()) is True


def test_named_existing_file_keeps_lock():
    assert revision_lock_justified("把 report.md 改短一点", has_selected_files=False,
                                   existing_names={"report.md"}) is True


def test_named_missing_file_releases_lock():
    assert revision_lock_justified("把 不存在的.md 改短一点", has_selected_files=False,
                                   existing_names={"别的.md"}) is False


# ---------- 文件名解析的三处边界（原实现的死角） ----------

def test_all_named_files_are_considered_not_just_first():
    assert named_files("参考 report.md 更新 新周报.md") == ["report.md", "新周报.md"]
    # 首名存在即算证据（原实现 search 只看第一个，反序时整个安全网失效）
    assert named_existing_file("把 新草稿.md 更新到 report.md 里", {"report.md"}) == "report.md"
    assert named_missing_file("参考 a.md 和 b.md", {"b.md"}) == "", "有一个存在就不算「点名的都不存在」"


def test_fullwidth_dot_is_normalized():
    assert named_files("把 报告．md 改一下") == ["报告.md"]
    assert named_existing_file("把 报告．md 改一下", {"报告.md"}) == "报告.md"


@pytest.mark.parametrize("name", ["config.json", "deploy.yaml", "首页.png", "图标.svg"])
def test_extension_family_aligned_with_target_regex(name):
    """json/yaml/png/svg 能触发 revise，安全网必须也认得它们（原先是死代码）。"""
    assert named_files(f"帮我更新 {name} 里的内容") == [name]


# ---------- 控制词短路（我们亲手做出来的陷阱） ----------

def test_continue_without_active_run_keeps_write_authority():
    """扣押轮的引导语教用户回复「继续」；若这轮 authority=none，出口就是陷阱。"""
    d = decide_turn("继续", has_selected_files=False, active_run=False)
    assert d.authority == "mutate", "无进行中 Run 时「继续」意为接着把活干完，需要写能力"


def test_continue_with_active_run_is_still_run_control():
    d = decide_turn("继续", has_selected_files=False, active_run=True)
    assert (d.intent, d.authority) == ("continue", "none"), "有进行中 Run 时它仍是 Run 控制词"


# ---------- 指示代词 + 类名词（2026-07-29 真机复测补的缺口） ----------
#
# 判据的核心张力就在这里：**类名词单独出现不算证据**（"帮我写一份整改报告"是新建），
# 但**指示代词 + 类名词**是明确回指（"那个报告"指向先前那个东西）。原先只收了
# 「那份」「这个文件」这类固定搭配，真机实测「把那个报告改简洁一点」漏判成新建——
# 模型拿到写工具后只是自己 glob 了一下、在正文里追问，**换个更自信的模型就可能直接
# 覆写错文件**。这两组用例必须一起看：任何一边失守，判据就退化成"有动作词就算"或
# "类名词就算"，那正是被击穿四次的老形态。

@pytest.mark.parametrize("msg", [
    "把那个报告改简洁一点",
    "这个报告再精简些",
    "那份文档改一下",
    "把上述方案优化一下",
    "这个 PPT 换个配色",
    "把那个表格改宽点",
    "该文件删掉一段",
])
def test_demonstrative_plus_class_noun_is_anaphoric(msg):
    assert anaphoric_target_reference(msg) is True, (
        f"「{msg}」是明确回指（指示代词+类名词），漏判会让模型在真歧义下拿到写工具"
    )
    assert revision_lock_justified(msg, has_selected_files=False, existing_names=set()) is True


@pytest.mark.parametrize("msg", [
    "帮我写一份整改报告",
    "做个数据分析表格",
    "生成一份说明文档",
    "帮我做一份数据库改造方案文档",
    "整理一份关于机房选型的建议文档",
    "帮我做个单位换算表格",
])
def test_class_noun_alone_is_still_not_evidence(msg):
    """阴性对照：上面那条放宽绝不能把类名词本身变成证据（那就退回被击穿的老形态）。"""
    assert anaphoric_target_reference(msg) is False, f"「{msg}」只有类名词，不是回指"
    assert revision_lock_justified(msg, has_selected_files=False, existing_names=set()) is False


# ==================== 对抗审计查出的三条（2026-07-29 第二轮） ====================
#
# 这三条都出在**当天早些时候刚写的"治本"代码**里——说明"换了判据方向"本身不保证正确，
# 新判据同样需要对抗性检验。三条的共同点：我堵住一个入口，又用新代码开了另一个。

def test_save_destination_is_stripped_before_anaphoric_probe():
    """「保存到我的文件里」是**去向**话术，不是回指证据。

    `我的文件(?=里|中|下)` 那条前瞻本意收「我的文件里的 X」，但方向搞反了——
    「保存到我的文件里」才是最常见的说法。_EXISTING_TARGET_RE 那侧早就先用
    _SAVE_DESTINATION_RE 剔过，新写的治本判据漏了，于是纯新建请求被扣押、
    甚至被导向覆盖上一份产物。
    """
    for msg in ("整理一份竞品分析报告，优化一下措辞，保存到我的文件里",
                "写份周报保存到我的文件中"):
        assert anaphoric_target_reference(msg) is False, f"去向话术被当成回指：{msg}"
        assert revision_lock_justified(msg, has_selected_files=False,
                                       existing_names=set()) is False

    # 阳性对照：真回指必须仍然成立，否则上面那条剔除就是把判据剔瞎了
    assert anaphoric_target_reference("我的文件里的选型建议改成两页") is True


@pytest.mark.parametrize("msg,expected", [
    # 「该」在中文里更常是"应该"：这两句是纯新建请求
    ("帮我写一份方案文档，该说明的地方说明清楚，该调整的就调整", False),
    ("该计划需要几个人力", False),
    # 固定搭配仍是回指
    ("该文件删掉一段", True),
    ("该报告的第二部分改简洁", True),
])
def test_gai_only_matches_fixed_collocations(msg, expected):
    assert anaphoric_target_reference(msg) is expected


@pytest.mark.parametrize("msg", [
    "把这份 PPT 的配色换个暖色调",
    "这份 PPT 标题换一下",
    "把报告里的旧数据换掉",
])
def test_huan_ge_is_still_a_revision_verb(msg):
    """「换个/换一下」是最常用的修订说法，不能被"改变做法"的负向表连带吃掉。

    误判成非修订的下游后果是实的：tool_scope 从 apply_presentation_patch 掉回
    build_presentation → 模型另起一份新 PPT，而「禁止另起副本」的提示词同时消失。
    """
    from app.services.chat.turn_decision import _REVISION_ACTION_RE
    assert _REVISION_ACTION_RE.search(msg), f"「{msg}」的修订动词没被认出来"


@pytest.mark.parametrize("msg", [
    "遇到抓取失败请自行换路",
    "这个方案不行就换种方式",
    "换个思路继续",
    "换一种说法再问",
])
def test_strategy_switch_still_excluded(msg):
    """阴性对照：上面那条放宽绝不能把"改变做法"义放回来（那是第三次击穿的形态）。"""
    from app.services.chat.turn_decision import _REVISION_ACTION_RE
    assert not _REVISION_ACTION_RE.search(msg), f"「{msg}」是改变做法，不是修订产物"
