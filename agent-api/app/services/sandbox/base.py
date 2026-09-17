"""沙箱适配器抽象层（对齐 FastGPT sdk/sandbox-adapter 的 ISandbox + BaseSandboxAdapter）。

FastGPT 的多 provider 架构：一套 ISandbox 接口 + 可插拔 provider 适配器（E2B/Sealos/OpenSandbox），
createSandbox(provider, config) 工厂按配置选一个。我方 Python 侧照搬：SandboxAdapter 抽象基类 +
E2B/Sealos/OpenSandbox 三个 provider，create_sandbox 工厂选择（管理员配好凭证/基建后启用）。

技能执行只用核心方法：create / write_files / execute / read_files / delete。
可选能力（execute_stream/execute_background/interrupt/move_files/get_metrics 等）默认抛 NotSupported，
与蓝本 BaseSandboxAdapter 对不支持的 provider 能力抛错的行为一致。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Callable, Optional


# ---------- 异常（对齐蓝本 errors.ts：ConnectionError / CommandExecutionError 等） ----------
class SandboxUnavailable(RuntimeError):
    """provider 依赖不可用（Docker socket 缺失 / SDK 未装 / 连接失败）→ 调用方可降级。"""


class SandboxNotConfigured(SandboxUnavailable):
    """provider 被选中但缺必要配置（apiKey/baseUrl/token）→ 管理员需补配置。"""


class SandboxError(RuntimeError):
    """沙箱内操作失败（执行/读写文件）。"""


class SandboxNotSupported(SandboxError):
    """当前 provider 不支持该可选能力（对齐蓝本 BaseSandboxAdapter 的 not-supported 抛错）。"""


# ---------- 数据类型（对齐蓝本 types/execution.ts + filesystem.ts） ----------
@dataclass
class ExecuteOptions:
    working_directory: Optional[str] = None
    timeout_ms: Optional[int] = None
    env: Optional[dict] = None
    max_output_bytes: Optional[int] = None
    # 流式 stdout 回调（2026-07-20 逐页产物直播）：执行期间每收到一块 stdout 就同步调用
    # （可能在任意线程，回调自己负责线程安全）；None=维持原「结束后整体返回」行为。
    # 仅 local 适配器实现；其它 provider 忽略该字段（优雅降级，无逐页直播）。
    on_stdout: Optional[Callable[[bytes], None]] = None
    # authoring（默认，保持隔离）| env_prep（只为装依赖临时放行白名单软件源）
    phase: Optional[str] = None


@dataclass
class ExecuteResult:
    stdout: str
    stderr: str
    exit_code: Optional[int]
    truncated: bool = False
    duration_ms: Optional[int] = None
    # 不再要求上层从 stderr 文案猜执行状态。Provider 能确认超时时显式置位；
    # termination_reason 为稳定机器字段，可逐步扩展为 cancelled/signal 等原因。
    timed_out: bool = False
    termination_reason: Optional[str] = None
    job_id: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass
class FileWriteEntry:
    path: str
    data: bytes  # 统一按字节；str 由调用方 encode
    mode: Optional[int] = None


@dataclass
class FileReadResult:
    path: str
    data: bytes = b""
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class SandboxInfo:
    sandbox_id: str
    provider: str
    state: str = "Running"
    extra: dict = field(default_factory=dict)


class SandboxAdapter(abc.ABC):
    """沙箱 provider 抽象基类（= 蓝本 BaseSandboxAdapter implements ISandbox）。"""

    provider: str = "base"

    # ---- 生命周期（ISandboxLifecycle）----
    @abc.abstractmethod
    async def create(self) -> None:
        """创建/连接沙箱实例。"""

    @abc.abstractmethod
    async def delete(self) -> None:
        """销毁沙箱。"""

    async def ensure_running(self) -> None:
        """确保就绪；默认等价 create（已就绪的 provider 可覆盖为幂等）。"""
        await self.create()

    async def get_info(self) -> Optional[SandboxInfo]:
        return None

    # ---- 命令执行（ICommandExecution）----
    @abc.abstractmethod
    async def execute(self, command: str, options: Optional[ExecuteOptions] = None) -> ExecuteResult:
        """在沙箱内执行 shell 命令。"""

    async def execute_stream(self, command: str, handlers, options=None):  # noqa: ANN001
        raise SandboxNotSupported(f"{self.provider} 不支持流式执行")

    async def execute_background(self, command: str, options=None):  # noqa: ANN001
        raise SandboxNotSupported(f"{self.provider} 不支持后台执行")

    async def interrupt(self, session_id: str) -> None:
        raise SandboxNotSupported(f"{self.provider} 不支持中断")

    async def set_env_prep_network(self, enabled: bool) -> bool:
        """Temporarily allow software-source egress. False = provider cannot."""
        return False

    async def get_job_status(self, job_id: str):  # noqa: ANN001
        raise SandboxNotSupported(f"{self.provider} 不支持查询后台作业")

    async def get_job_logs(self, job_id: str, cursor: Optional[int] = None):  # noqa: ANN001
        raise SandboxNotSupported(f"{self.provider} 不支持读取后台作业日志")

    # ---- 文件系统（IFileSystem）----
    @abc.abstractmethod
    async def write_files(self, entries: list[FileWriteEntry]) -> None:
        """批量写文件（蓝本 writeFiles）。"""

    @abc.abstractmethod
    async def read_files(self, paths: list[str]) -> list[FileReadResult]:
        """批量读文件（蓝本 readFiles）。"""

    async def delete_files(self, paths: list[str]) -> None:
        raise SandboxNotSupported(f"{self.provider} 不支持删除文件")

    async def create_directories(self, paths: list[str]) -> None:
        raise SandboxNotSupported(f"{self.provider} 不支持创建目录")

    # ---- 健康检查（IHealthCheck）----
    @abc.abstractmethod
    async def ping(self) -> bool:
        """探活。"""

    async def get_metrics(self) -> dict:
        raise SandboxNotSupported(f"{self.provider} 不支持指标")

    # ---- 便捷：技能运行时用（写单文件字典/读单文件）----
    async def write_file_map(self, files: dict[str, bytes]) -> None:
        entries = [
            FileWriteEntry(path=p, data=(d if isinstance(d, bytes) else str(d).encode("utf-8")))
            for p, d in files.items()
        ]
        await self.write_files(entries)

    async def read_file(self, path: str) -> bytes:
        results = await self.read_files([path])
        if not results or not results[0].ok:
            raise SandboxError(results[0].error if results else f"读取失败: {path}")
        return results[0].data
