# -*- coding: utf-8 -*-
"""「保存到我的文件」类去向话术不得误判成修订（2026-07-28）。

真机事故（deepseek-v4-pro E2E）：「整理一份选型建议保存到我的文件，文件名：xx.md」——
「建议」触发 feedback、「我的文件」与「xx.md」触发 existing_target，叠加成 revise 且
allow_create=False；修订目标又解析不出 file_id，写工具全部被扣押（tools/__init__.py
的目标未解析防线），模型只能把全文贴在聊天里交差，用户点名要的文件从未落盘。

「我的文件”是产品功能区名、「文件名：xxx.md」是给新文件起名——都是**新建**话术。
"""
from app.services.chat.turn_decision import decide_turn


def _d(msg: str, **kw):
    return decide_turn(msg, has_selected_files=kw.pop("has_selected_files", False),
                       active_run=kw.pop("active_run", False), **kw)


def test_save_to_myfiles_with_filename_is_create_not_revise():
    """事故原文：必须判新建（revision=False, allow_create=True），写工具不得被扣押。"""
    d = _d(
        "帮我联网调研 DeepSeek V4 系列模型的公开信息（发布时间、能力定位、和上一代的差异），"
        "整理成一份简短的选型建议 markdown，保存到我的文件，文件名：deepseek-v4-选型建议.md"
    )
    assert d.revision is False, "「保存到我的文件」是去向话术，不是修改已有文件"
    assert d.allow_create is True
    assert d.authority == "mutate"


def test_save_as_filename_is_create():
    d = _d("把调研结果整理成周报，保存为 weekly-report.md")
    assert d.revision is False
    assert d.allow_create is True


def test_true_revision_of_file_in_myfiles_still_revise():
    """真修订不受影响：「把我的文件里的 X 改成 Y」仍是 revision。"""
    d = _d("把我的文件里的选型建议改成两页")
    assert d.revision is True


def test_plain_filename_reference_still_revise():
    """直呼已有文件名要求修改，仍是 revision（剔除仅限「保存为/文件名：」前缀形态）。"""
    d = _d("把 report.md 的第二段改得简洁一点")
    assert d.revision is True


def test_selected_files_still_force_existing_target():
    """用户在 composer 里勾选了文件：无论话术怎么说都指向既有目标。"""
    d = _d("按上面的要求优化一下并保存", has_selected_files=True)
    assert d.revision is True
