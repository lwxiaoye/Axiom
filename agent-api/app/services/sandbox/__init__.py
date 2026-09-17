"""多 provider 沙箱适配层（对齐 FastGPT sdk/sandbox-adapter）。

管理员经 SKILL_SANDBOX_PROVIDER 选一个：docker（自建默认）/ e2b / sealosdevbox / opensandbox。
"""
from .base import (
    ExecuteOptions,
    ExecuteResult,
    FileReadResult,
    FileWriteEntry,
    SandboxAdapter,
    SandboxError,
    SandboxInfo,
    SandboxNotConfigured,
    SandboxNotSupported,
    SandboxUnavailable,
)
from .factory import create_configured_sandbox, create_sandbox, sandbox_available

__all__ = [
    "SandboxAdapter",
    "ExecuteOptions",
    "ExecuteResult",
    "FileWriteEntry",
    "FileReadResult",
    "SandboxInfo",
    "SandboxError",
    "SandboxUnavailable",
    "SandboxNotConfigured",
    "SandboxNotSupported",
    "create_sandbox",
    "create_configured_sandbox",
    "sandbox_available",
]
