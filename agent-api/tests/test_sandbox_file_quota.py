"""沙箱单文件配额（`ulimit -f`）定向测试（2026-07-27）。

有了 bash，`dd if=/dev/zero` 和解压炸弹是最现实的攻击面。三条路只有这条走通：
- `--storage-opt size=` 被**静默忽略**（`docker run` 退出 0，容器内 df 仍是宿主全盘）；
- `--tmpfs /workspace` 配额真生效，但 `put_archive`/`docker cp` 绕不过挂载，字节落进被遮挡的
  下层，入口脚本直接 exit 127（实测，已回退）；
- `ulimit -f` 落在执行包装层，对 python 与 bash 同时生效（实测：限 8MB 时 dd 500MB 只落 8.0M）。

边界要如实测出来：它限的是**单个文件**，挡不住「写一万个小文件」。
"""
import shlex

import pytest

from app.core.config import settings
from app.services.sandbox.local_adapter import _wrap_command


def _inner(wrapped: str) -> str:
    """从 `timeout Ns sh -c '<inner>'` 里取回 inner。"""
    parts = shlex.split(wrapped)
    return parts[-1]


def test_ulimit_present_and_uses_512_byte_blocks(monkeypatch):
    """ulimit -f 的单位是 512 字节块（POSIX），不是 KB —— 写错单位上限会差 2 倍。"""
    monkeypatch.setattr(settings, "SKILL_SANDBOX_LOCAL_MAX_FILE_MB", 512)
    inner = _inner(_wrap_command("echo hi", "/workspace", 60))
    assert inner.startswith("ulimit -f ")
    blocks = int(inner.split("ulimit -f ", 1)[1].split(";", 1)[0])
    assert blocks == 512 * 1024 * 1024 // 512 == 1048576


def test_quota_disabled_when_zero(monkeypatch):
    monkeypatch.setattr(settings, "SKILL_SANDBOX_LOCAL_MAX_FILE_MB", 0)
    inner = _inner(_wrap_command("echo hi", "/workspace", 60))
    assert "ulimit" not in inner


def test_cd_and_command_survive_the_prefix(monkeypatch):
    """加了 ulimit 前缀不能破坏原有语义：cd 与 && 串联必须还在，timeout 仍包整条 sh -c。"""
    monkeypatch.setattr(settings, "SKILL_SANDBOX_LOCAL_MAX_FILE_MB", 8)
    wrapped = _wrap_command("cd sub && ls", "/workspace", 90)
    assert wrapped.startswith("timeout 90s sh -c ")
    inner = _inner(wrapped)
    assert "cd /workspace && cd sub && ls" in inner


@pytest.mark.parametrize("mb", [1, 8, 512, 4096])
def test_various_limits_are_positive_blocks(monkeypatch, mb):
    monkeypatch.setattr(settings, "SKILL_SANDBOX_LOCAL_MAX_FILE_MB", mb)
    inner = _inner(_wrap_command("true", "/workspace", 30))
    blocks = int(inner.split("ulimit -f ", 1)[1].split(";", 1)[0])
    assert blocks > 0


def test_quote_safety_with_hostile_command(monkeypatch):
    """命令里带引号/分号不能逃出 sh -c 的引用（shlex.quote 负责，这里锁住不被将来改坏）。"""
    monkeypatch.setattr(settings, "SKILL_SANDBOX_LOCAL_MAX_FILE_MB", 8)
    wrapped = _wrap_command("echo 'a'; rm -rf /", "/workspace", 30)
    # 整条 inner 必须是**一个**被引用的参数：拆出来后仍是完整字符串，没有被 shell 提前分词
    assert _inner(wrapped).endswith("echo 'a'; rm -rf /")
    # timeout / 30s / sh / -c / <inner> —— 恰好 5 个 token 才说明 inner 整体只是一个参数，
    # 没有被 shell 提前分词（分词了 rm -rf / 就会变成独立命令）
    assert len(shlex.split(wrapped)) == 5
