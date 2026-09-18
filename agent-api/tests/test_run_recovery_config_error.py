"""配置性错误不进 waiting_system 自动恢复（2026-09-18）。

背景：演示文稿助手因为技能目录里没有 ppt-studio 抛 RuntimeError，`_pump_background_run`
把它当瞬时故障 → recover_run_after_error → waiting_system → worker 一秒后重排 → 再撞同一个
错……用户端只看到「等待任务恢复...」，永远看不到原因。

判定规则（与 public_errors.ConfigurationRunError 的 docstring 一致）：
- 终态与否**按异常类型**判，不按消息正则猜：TerminalRunError（含 ConfigurationRunError）终态，
  其余异常继续走恢复；
- ConfigurationRunError 的 public_message 就是面向用户的原因，pump 会以 run.failed 事件发给前端，
  并写进 RunState.terminal_reason，公共快照边界的前缀白名单必须放行它。

本文件不入库、不起事件循环之外的东西：只锁 pump 的分类判据与快照边界的可见性。
"""
from __future__ import annotations

from app.services.agent_harness.public_errors import (
    ConfigurationRunError,
    GENERIC_RUN_FAILURE,
    TerminalRunError,
    public_run_error,
    public_terminal_reason,
)


def _pump_classification(error: BaseException) -> str:
    """复刻 run_hub._pump_background_run 的分类判据（那里是 isinstance(e, TerminalRunError)）。"""
    return "terminal" if isinstance(error, TerminalRunError) else "recover"


def test_configuration_error_is_terminal_not_recoverable():
    error = ConfigurationRunError("演示文稿助手暂不可用：技能目录里没有已启用的 ppt-studio。")
    assert _pump_classification(error) == "terminal"
    # 仍是 RuntimeError：presentation/prepare.py 里 `except RuntimeError: raise` 直通不吞
    assert isinstance(error, RuntimeError)


def test_plain_runtime_error_still_recovers():
    """瞬时错误（DB 抖动、租约丢失、进程重启）仍走既有的 waiting_system 恢复路径。"""
    assert _pump_classification(RuntimeError("演示文稿助手暂不可用：ppt-studio 权威校验超时。")) == "recover"
    assert _pump_classification(ConnectionError("mysql gone away")) == "recover"


def test_configuration_error_reason_is_user_visible():
    reason = "演示文稿助手暂不可用：技能目录里没有已启用的 ppt-studio。请管理员确认内置技能已注册后重试。"
    error = ConfigurationRunError(reason)
    # pump 用 e.public_message 发 run.failed（前端 onError 标红）并写 terminal_reason
    assert error.public_message == reason
    assert public_run_error(error) == reason
    # 历史快照边界（public_terminal_reason）按前缀白名单放行，不能被折叠成通用文案
    assert public_terminal_reason(reason, phase="failed") == reason
    assert public_terminal_reason(reason, phase="failed") != GENERIC_RUN_FAILURE


def test_blank_reason_falls_back_to_generic_message():
    error = ConfigurationRunError("   ")
    assert error.public_message == GENERIC_RUN_FAILURE
