"""v3.0 续做触发语扩充：新语料正例/反例 + 【任务快照】marker 剥离。"""

from app.services.chat.turn_context_builder import needs_resume_checkpoint

# 新语料正例（v3.0 追加）
NEW_POSITIVE = [
    "上次做到一半继续",
    "刚才没做完接着来",
    "之前没弄完继续",
    "做到一半就停了，继续",
    "请继续之前那个文档",
    "接着刚才的PPT",
    "从上次进度继续",
    "顺着上次状态接着做",
    "按上次断点继续",
    "刚才断网了继续",
    "上次卡住了接着来",
    "刚才中断了，重新来",
    "刚才没信号了接着来",
]

# 反例：普通新任务 / 顺带询问
NEGATIVE = [
    "帮我查一下今天漳州天气",
    "顺带帮我查天气",
    "今天有什么安排",
    "介绍一下你自己",
    "写一份新方案",
]

# 已有语料回归（v2.81/v2.85 锁定）
LEGACY_POSITIVE = [
    "继续",
    "接着做",
    "恢复",
    "继续完成刚才的文档",
    "在原来基础上继续",
    "接着刚才的弄",
    "网络断了，继续",
    "继续，别从零开始",
    "继续，不要重做",
    "请直接交付",
    "现在导出给我",
    "直接交付ppt",
    "直接交付PPT",
    "直接发布到我的文件",
    "不用审查直接交付",
]


def test_new_phrases_match():
    for phrase in NEW_POSITIVE:
        assert needs_resume_checkpoint(phrase) is True, f"应命中续做：{phrase}"


def test_negative_phrases_do_not_match():
    for phrase in NEGATIVE:
        assert needs_resume_checkpoint(phrase) is False, f"不应命中续做：{phrase}"


def test_legacy_phrases_still_match():
    for phrase in LEGACY_POSITIVE:
        assert needs_resume_checkpoint(phrase) is True, f"回归失败：{phrase}"


def test_snapshot_marker_stripped():
    """注入过快照块的文本剥离后仍能识别裸「继续」（与断点现场/锚点/工具进度同闸）。
    用户原句在前、平台注入块在后——剥离点之后的快照块不影响判定。"""
    text = (
        "继续\n\n【任务快照（平台注入，上一轮断点现场，强制沿用）】\n"
        "任务目标：做一份教师节 PPT\n已加载技能：1 个"
    )
    assert needs_resume_checkpoint(text) is True


def test_snapshot_marker_in_strip_set():
    src = open("app/services/chat/turn_context_builder.py", encoding="utf-8").read()
    assert "'【任务快照'" in src
    assert "【断点现场" in src
    # model_driver 的 marker 剥离集合同步扩充
    main_src = open("app/services/agent_harness/model_driver.py", encoding="utf-8").read()
    assert '"【任务快照"' in main_src
