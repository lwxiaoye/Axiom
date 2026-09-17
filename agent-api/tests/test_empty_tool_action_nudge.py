# -*- coding: utf-8 -*-
"""空口假交付推回：弱模型把 bash 写在正文 / 工作区交付 0 工具就停手。"""
from app.services.agent_harness.model_driver import (
    LoopState,
    _looks_like_fake_shell_delivery,
    _should_nudge_empty_tool_action,
    _REQUIRES_TOOL_DELIVERY_RE,
)


def test_fake_shell_delivery_detection():
    assert _looks_like_fake_shell_delivery(
        'bash echo "424242" > /workspace/files/multi-smoke-glm-4-flash.txt'
    )
    assert _looks_like_fake_shell_delivery(
        "```bash\necho 1 > /workspace/files/a.txt\n```"
    )
    assert not _looks_like_fake_shell_delivery("已写入 multi-smoke-glm-4-flash.txt")


def test_requires_tool_delivery_not_essay():
    assert not _REQUIRES_TOOL_DELIVERY_RE.search("写一篇关于春天的散文")
    assert _REQUIRES_TOOL_DELIVERY_RE.search(
        "用 bash 跑：把数字 424242 写入 multi-smoke-glm-4-flash.txt"
    )
    assert _REQUIRES_TOOL_DELIVERY_RE.search("请新建文件 a.md，内容 multi-ok")


def test_nudge_conditions():
    common = dict(
        already_nudged=False,
        forced_final=False,
        has_executors=True,
        tools_succeeded=False,
    )
    assert _should_nudge_empty_tool_action(
        user_message="用 bash 跑：写入 x.txt",
        round_content='bash echo "1" > /workspace/files/x.txt',
        **common,
    )
    assert _should_nudge_empty_tool_action(
        user_message="请新建文件 multi-smoke.md，内容 multi-ok",
        round_content="好的，文件已准备好。",
        **common,
    )
    # 纯作文不推回
    assert not _should_nudge_empty_tool_action(
        user_message="写一篇关于春天的散文",
        round_content="春天来了，万物复苏。",
        **common,
    )
    # 工具已成功不再推
    assert not _should_nudge_empty_tool_action(
        user_message="用 bash 跑：写入 x.txt",
        round_content="ok",
        already_nudged=False,
        forced_final=False,
        has_executors=True,
        tools_succeeded=True,
    )
    # 整轮一次
    assert not _should_nudge_empty_tool_action(
        user_message="用 bash 跑：写入 x.txt",
        round_content="ok",
        already_nudged=True,
        forced_final=False,
        has_executors=True,
        tools_succeeded=False,
    )


def test_state_flag_default():
    assert LoopState().empty_tool_action_nudged is False


def test_stream_loop_hosts_the_net():
    import inspect
    from app.services.agent_harness import model_driver
    src = inspect.getsource(model_driver.drive_model)
    # An ordinary no-tool answer is a model decision.  CompletionVerifier and
    # ToolSpec facts remain the only lifecycle/permission authorities.
    assert "net_empty_tool_action" not in src
    assert "CompletionVerifier" in src
    assert 'payload["tool_choice"] = "auto"' in src
