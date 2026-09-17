"""OpenSandbox provider backed by the current Alibaba Python SDK.

The adapter deliberately keeps the application-facing ``SandboxAdapter`` contract
small.  OpenSandbox owns remote lifecycle and execution; the application owns
copying generated files into the persistent user-file store before destroy().
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

import httpx

from app.core.config import settings

from .base import (
    ExecuteOptions,
    ExecuteResult,
    FileReadResult,
    FileWriteEntry,
    SandboxAdapter,
    SandboxError,
    SandboxInfo,
    SandboxNotConfigured,
    SandboxUnavailable,
)


def classify_sandbox_exception(exc: BaseException) -> tuple[str, bool, int]:
    """Map provider exceptions to a stable termination_reason.

    Returns (reason, timed_out, exit_code).
    """
    name = type(exc).__name__.lower()
    text = str(exc or "").lower()
    blob = f"{name} {text}"
    if isinstance(exc, TimeoutError) or "timeout" in blob or "timed out" in blob:
        return "timeout", True, 124
    if "cancel" in blob:
        return "cancelled", False, 130
    if any(token in blob for token in ("network", "connect", "dns", "econn", "unreachable")):
        return "network_error", False, 1
    return "provider_error", False, 1


def classify_execution_error(error: object) -> tuple[Optional[str], bool, Optional[int]]:
    if error is None:
        return None, False, None
    name = str(getattr(error, "name", "") or "").lower()
    value = str(getattr(error, "value", error) or "").lower()
    blob = f"{name} {value}"
    if "timeout" in blob or "timed out" in blob:
        return "timeout", True, 124
    if "cancel" in blob:
        return "cancelled", False, 130
    if any(token in blob for token in ("network", "connect", "dns", "econn", "unreachable")):
        return "network_error", False, 1
    return None, False, None

_DEFAULT_IMAGE = "python:3.11"
_DEFAULT_MAX_OUTPUT_BYTES = 131072
_SERVER_PROXY_WRITE_BATCH_SIZE = 10
ENV_PREP_EGRESS_TARGETS = (
    "pypi.org",
    "*.pypi.org",
    "files.pythonhosted.org",
    "pypi.python.org",
    "registry.npmjs.org",
    "mirrors.aliyun.com",
    "*.aliyun.com",
)


def _cap_text(text: str, limit: int) -> tuple[str, bool]:
    if limit and len(text) > limit:
        return text[:limit], True
    return text, False


def _container_lifetime_seconds() -> int:
    configured = int(getattr(settings, "SANDBOX_SESSION_MAX_LIFETIME_S", 3600) or 3600)
    return max(1800, configured + 600)


class OpenSandboxAdapter(SandboxAdapter):
    provider = "opensandbox"

    def __init__(self, connection_config: Optional[dict] = None, create_config: Optional[dict] = None):
        cfg = connection_config or {}
        self._domain = str(cfg.get("domain") or cfg.get("baseUrl") or cfg.get("base_url") or "").rstrip("/")
        self._api_key = cfg.get("api_key") or cfg.get("apiKey")
        self._pool_ref = cfg.get("pool_ref") or cfg.get("poolRef")
        self._use_server_proxy = bool(cfg.get("use_server_proxy", True))
        self._image = cfg.get("image") or _DEFAULT_IMAGE
        self._cpu = str(cfg.get("cpu") or getattr(settings, "SKILL_SANDBOX_OPENSANDBOX_CPU", "1"))
        self._memory = str(
            cfg.get("memory") or getattr(settings, "SKILL_SANDBOX_OPENSANDBOX_MEMORY", "1Gi")
        )
        create = create_config or {}
        self._image = create.get("image") or self._image
        self._entrypoint = create.get("entrypoint") or ["sleep", str(_container_lifetime_seconds())]
        self._workspace = create.get("workspace") or "/workspace"
        self._sandbox = None
        self.network_leaked = False

    def _sdk(self):
        try:
            from opensandbox import Sandbox
            from opensandbox.config import ConnectionConfig
            from opensandbox.models.execd import ExecutionHandlers, RunCommandOpts
            from opensandbox.models.filesystem import WriteEntry
        except Exception as exc:  # noqa: BLE001
            raise SandboxUnavailable(
                "OpenSandbox SDK 未安装或版本不兼容：需要 opensandbox==0.1.15"
            ) from exc
        return Sandbox, ConnectionConfig, ExecutionHandlers, RunCommandOpts, WriteEntry

    def _conn(self, ConnectionConfig):  # noqa: ANN001
        if not self._domain:
            raise SandboxNotConfigured(
                "OpenSandbox provider 需配置 domain（SKILL_SANDBOX_OPENSANDBOX_DOMAIN）"
            )
        return ConnectionConfig(
            domain=self._domain,
            api_key=self._api_key,
            use_server_proxy=self._use_server_proxy,
        )

    async def create(self) -> None:
        if self._sandbox is not None:
            return
        Sandbox, ConnectionConfig, _Handlers, _RunOpts, _WriteEntry = self._sdk()
        kwargs = {
            "entrypoint": self._entrypoint,
            "timeout": timedelta(seconds=_container_lifetime_seconds()),
            "ready_timeout": timedelta(
                seconds=int(getattr(settings, "SKILL_SANDBOX_OPENSANDBOX_READY_TIMEOUT_S", 90) or 90)
            ),
            "resource": {"cpu": self._cpu, "memory": self._memory},
            "connection_config": self._conn(ConnectionConfig),
        }
        if self._pool_ref:
            kwargs["extensions"] = {"poolRef": str(self._pool_ref)}
        try:
            self._sandbox = await Sandbox.create(self._image, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self._sandbox = None
            raise SandboxUnavailable(f"创建 OpenSandbox 沙箱失败: {exc}") from exc

    async def delete(self) -> None:
        if self._sandbox is None:
            return
        sandbox = self._sandbox
        self._sandbox = None
        try:
            destroy = getattr(sandbox, "destroy", None)
            if destroy is not None:
                await destroy()
                return
            # Compatibility for older SDKs: close() alone does not terminate a
            # remote sandbox, so kill it first when destroy() is unavailable.
            kill = getattr(sandbox, "kill", None)
            if kill is not None:
                await kill()
            close = getattr(sandbox, "close", None)
            if close is not None:
                await close()
        except Exception:  # noqa: BLE001
            # Cleanup runs from finally blocks and must not hide the original
            # execution error. The server TTL remains a last-resort safeguard.
            pass

    async def ping(self) -> bool:
        if not self._domain:
            return False
        headers = {"OPEN-SANDBOX-API-KEY": self._api_key} if self._api_key else None
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{self._domain}/health", headers=headers)
            return response.is_success
        except Exception:  # noqa: BLE001
            return False

    async def get_info(self) -> Optional[SandboxInfo]:
        if self._sandbox is None:
            return None
        sid = getattr(self._sandbox, "id", "") or getattr(self._sandbox, "sandbox_id", "")
        state = "Running"
        try:
            info = await self._sandbox.get_info()
            state = str(getattr(getattr(info, "status", None), "state", None) or state)
        except Exception:  # noqa: BLE001
            pass
        return SandboxInfo(sandbox_id=str(sid), provider=self.provider, state=state)

    async def execute(self, command: str, options: Optional[ExecuteOptions] = None) -> ExecuteResult:
        if self._sandbox is None:
            await self.create()
        _Sandbox, _ConnectionConfig, ExecutionHandlers, RunCommandOpts, _WriteEntry = self._sdk()
        max_bytes = (
            (options.max_output_bytes if options and options.max_output_bytes else None)
            or _DEFAULT_MAX_OUTPUT_BYTES
        )
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        truncated = {"stdout": False, "stderr": False}

        async def on_stdout(message) -> None:  # noqa: ANN001
            text = str(getattr(message, "text", message))
            if options and options.on_stdout:
                try:
                    options.on_stdout(text.encode("utf-8", errors="replace"))
                except Exception:  # noqa: BLE001
                    pass
            current = sum(len(part) for part in stdout_parts)
            if current >= max_bytes:
                truncated["stdout"] = True
                return
            remaining = max_bytes - current
            stdout_parts.append(text[:remaining])
            if len(text) > remaining:
                truncated["stdout"] = True

        async def on_stderr(message) -> None:  # noqa: ANN001
            text = str(getattr(message, "text", message))
            current = sum(len(part) for part in stderr_parts)
            if current >= max_bytes:
                truncated["stderr"] = True
                return
            remaining = max_bytes - current
            stderr_parts.append(text[:remaining])
            if len(text) > remaining:
                truncated["stderr"] = True

        timeout_ms = (options.timeout_ms if options and options.timeout_ms else 30000)
        run_options = RunCommandOpts(
            working_directory=(options.working_directory if options else None) or self._workspace,
            timeout=timedelta(milliseconds=max(1, timeout_ms)),
            envs=(options.env if options else None),
        )
        handlers = ExecutionHandlers(
            on_stdout=on_stdout,
            on_stderr=on_stderr,
            skip_accumulation=True,
        )
        prep = str((options.phase if options else None) or "") == "env_prep"
        if prep:
            allowed = await self.set_env_prep_network(True)
            if not allowed:
                return ExecuteResult(
                    stdout="",
                    stderr="沙箱未接到 env_prep 出网策略，不能在断网环境安装依赖",
                    exit_code=1,
                    termination_reason="network_denied",
                )
        run_error: Optional[ExecuteResult] = None
        execution = None
        try:
            try:
                execution = await self._sandbox.commands.run(
                    command,
                    opts=run_options,
                    handlers=handlers,
                )
            except Exception as exc:  # noqa: BLE001
                reason, timed_out, exit_code = classify_sandbox_exception(exc)
                run_error = ExecuteResult(
                    stdout="",
                    stderr=str(exc),
                    exit_code=exit_code,
                    timed_out=timed_out,
                    termination_reason=reason,
                )
        finally:
            if prep:
                restored = await self.set_env_prep_network(False)
                if not restored:
                    await self._abandon_leaked_network()
                    return ExecuteResult(
                        stdout="",
                        stderr="env_prep 结束后未能收回沙箱出网策略，已销毁该沙箱，不能继续处理材料",
                        exit_code=1,
                        termination_reason="network_policy_restore_failed",
                    )

        if run_error is not None:
            return run_error

        exit_code = getattr(execution, "exit_code", None)
        if exit_code is None:
            exit_code = 1 if getattr(execution, "error", None) else 0
        stdout = "".join(stdout_parts)
        stderr = "".join(stderr_parts)
        if not stdout and not stderr:
            logs = getattr(execution, "logs", None)
            stdout = "".join(str(getattr(item, "text", item)) for item in getattr(logs, "stdout", []))
            stderr = "".join(str(getattr(item, "text", item)) for item in getattr(logs, "stderr", []))
        error = getattr(execution, "error", None)
        if error is not None and not stderr:
            stderr = str(getattr(error, "value", error))
        reason, timed_out, error_exit = classify_execution_error(error)
        if timed_out and exit_code in (None, 0):
            exit_code = error_exit or 124
        stdout, stdout_capped = _cap_text(stdout, max_bytes)
        stderr, stderr_capped = _cap_text(stderr, max_bytes)
        return ExecuteResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            truncated=bool(truncated["stdout"] or truncated["stderr"] or stdout_capped or stderr_capped),
            timed_out=timed_out,
            termination_reason=reason,
        )

    async def write_files(self, entries: list[FileWriteEntry]) -> None:
        if self._sandbox is None:
            await self.create()
        _Sandbox, _ConnectionConfig, _Handlers, _RunOpts, WriteEntry = self._sdk()
        try:
            payload = []
            for entry in entries:
                path = entry.path if entry.path.startswith("/") else f"{self._workspace}/{entry.path}"
                # The application uses POSIX bitmasks (0o644 -> decimal 420), while
                # OpenSandbox's wire model expects octal digits encoded as an int
                # (644). Passing 420 makes execd parse the string as octal "420".
                posix_mode = entry.mode if entry.mode is not None else 0o666
                sdk_mode = int(format(posix_mode, "o"))
                payload.append(WriteEntry(path=path, data=entry.data, mode=sdk_mode))
            if payload:
                batch_size = _SERVER_PROXY_WRITE_BATCH_SIZE if self._use_server_proxy else len(payload)
                for start in range(0, len(payload), batch_size):
                    await self._sandbox.files.write_files(payload[start:start + batch_size])
        except Exception as exc:  # noqa: BLE001
            raise SandboxError(f"OpenSandbox 写文件失败: {exc}") from exc

    async def read_files(self, paths: list[str]) -> list[FileReadResult]:
        if self._sandbox is None:
            await self.create()
        results = []
        for path in paths:
            full = path if path.startswith("/") else f"{self._workspace}/{path}"
            try:
                data = await self._sandbox.files.read_bytes(full)
                results.append(FileReadResult(path=path, data=data if isinstance(data, bytes) else bytes(data)))
            except Exception as exc:  # noqa: BLE001
                results.append(FileReadResult(path=path, error=str(exc)))
        return results

    async def set_env_prep_network(self, enabled: bool) -> bool:
        if self._sandbox is None:
            await self.create()
        try:
            from opensandbox.models.sandboxes import NetworkRule

            if enabled:
                rules = [
                    NetworkRule(action="allow", target=target)
                    for target in ENV_PREP_EGRESS_TARGETS
                ]
                await self._sandbox.patch_egress_rules(rules)
            else:
                await self._sandbox.delete_egress_rules(list(ENV_PREP_EGRESS_TARGETS))
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _abandon_leaked_network(self) -> None:
        """Destroy a sandbox whose env_prep egress could not be revoked."""
        self.network_leaked = True
        try:
            await self.delete()
        except Exception:  # noqa: BLE001
            self._sandbox = None

    async def execute_background(self, command: str, options=None):  # noqa: ANN001
        if self._sandbox is None:
            await self.create()
        _Sandbox, _ConnectionConfig, ExecutionHandlers, RunCommandOpts, _WriteEntry = self._sdk()
        run_options = RunCommandOpts(
            working_directory=(options.working_directory if options else None) or self._workspace,
            timeout=timedelta(milliseconds=max(1, (options.timeout_ms if options and options.timeout_ms else 30000))),
            envs=(options.env if options else None),
            background=True,
        )
        handlers = ExecutionHandlers(skip_accumulation=True)
        try:
            execution = await self._sandbox.commands.run(
                command, opts=run_options, handlers=handlers,
            )
        except Exception as exc:  # noqa: BLE001
            reason, timed_out, exit_code = classify_sandbox_exception(exc)
            return ExecuteResult(
                stdout="", stderr=str(exc), exit_code=exit_code,
                timed_out=timed_out, termination_reason=reason,
            )
        job_id = str(getattr(execution, "id", "") or "")
        return ExecuteResult(stdout="", stderr="", exit_code=None, job_id=job_id)

    async def interrupt(self, session_id: str) -> None:
        if self._sandbox is None:
            return
        try:
            await self._sandbox.commands.interrupt(session_id)
        except Exception as exc:  # noqa: BLE001
            raise SandboxError(f"中断沙箱作业失败: {exc}") from exc

    async def get_job_status(self, job_id: str):  # noqa: ANN001
        if self._sandbox is None:
            await self.create()
        return await self._sandbox.commands.get_command_status(job_id)

    async def get_job_logs(self, job_id: str, cursor: Optional[int] = None):  # noqa: ANN001
        if self._sandbox is None:
            await self.create()
        return await self._sandbox.commands.get_background_command_logs(job_id, cursor=cursor)
