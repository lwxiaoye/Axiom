"""v2.97: 跨主题禁止串文件/串配图。"""

from app.services.agent_harness.model_driver import (
    _checkpoint_topic_mismatch,
    _topic_tokens,
)


def test_topic_tokens_keeps_game_names():
    toks = _topic_tokens("重构整个ppt，关于只狼的宣传，要穿插游戏截图")
    assert "只狼" in toks


def test_checkpoint_mismatch_elden_vs_sekiro():
    user = "重构整个ppt，做只狼的宣传，里面要穿插游戏图片"
    blob = (
        "【断点现场（平台注入，强制遵守）】\n"
        "- elden-02.jpg\n"
        "- elden-03.jpg\n"
        "- 艾尔登法环宣传.pptx\n"
        "已执行工具 download_url write_file"
    )
    assert _checkpoint_topic_mismatch(user, blob) is True


def test_checkpoint_same_topic_no_mismatch():
    user = "继续优化只狼的ppt配色"
    blob = (
        "【断点现场】\n"
        "- sekiro-cover.jpg\n"
        "- 只狼宣传.pptx\n"
    )
    assert _checkpoint_topic_mismatch(user, blob) is False


def test_empty_checkpoint_no_mismatch():
    assert _checkpoint_topic_mismatch("做只狼ppt", "") is False
