"""E2B provider（商业云沙箱）。对齐蓝本 E2BAdapter（用 @e2b/code-interpreter TS SDK）。

Python 侧用官方 `e2b_code_interpreter` SDK（懒加载：仅 provider=e2b 时才需 pip install e2b-code-interpreter）。
配置：apiKey（必填）、template、timeout、envs。需真实 E2B_API_KEY 才能运行。
"""
from __future__ import annotations

import asyncio
from typing import Optional

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

# SDK 一次性整段返回 stdout/stderr（不像 local_adapter 有分块读的余地），这里只能对
# 已返回的字符串做事后截断，与 opensandbox_adapter 同一兜底值对齐（P1-13 同类缺口）。
_DEFAULT_MAX_OUTPUT_BYTES = 131072


def _cap_text(text: str, limit: int) -> tuple[str, bool]:
    if limit and len(text) > limit:
        return text[:limit], True
    return text, False


class E2BAdapter(SandboxAdapter):
    provider = "e2b"

    def __init__(self, connection_config: Optional[dict] = None, create_config: Optional[dict] = None):
        cfg = connection_config or {}
        self._api_key = cfg.get("apiKey") or cfg.get("api_key")
        self._template = cfg.get("template")
        self._timeout = cfg.get("timeout")
        self._envs = cfg.get("envs")
        self._sandbox = None
        if not self._api_key:
            # 延迟到 create 抛，允许构造但激活时报清晰错误
            self._config_error = "E2B provider 需配置 apiKey（SKILL_SANDBOX_E2B_API_KEY）"
        else:
            self._config_error = None

    def _sdk(self):
        try:
            from e2b_code_interpreter import Sandbox  # 懒加载
        except Exception as exc:  # noqa: BLE001
            raise SandboxUnavailable(
                "E2B SDK 未安装：pip install e2b-code-interpreter（仅 provider=e2b 需要）"
            ) from exc
        return Sandbox

    async def create(self) -> None:
        if self._config_error:
            raise SandboxNotConfigured(self._config_error)
        if self._sandbox is not None:
            return
        Sandbox = self._sdk()

        def _create():
            kwargs = {"api_key": self._api_key}
            if self._template:
                kwargs["template"] = self._template
            if self._timeout:
                kwargs["timeout"] = self._timeout
            if self._envs:
                kwargs["envs"] = self._envs
            return Sandbox.create(**kwargs) if hasattr(Sandbox, "create") else Sandbox(**kwargs)

        try:
            self._sandbox = await asyncio.to_thread(_create)
        except Exception as exc:  # noqa: BLE001
            raise SandboxUnavailable(f"创建 E2B 沙箱失败: {exc}") from exc

    async def delete(self) -> None:
        if self._sandbox is None:
            return
        sb = self._sandbox
        self._sandbox = None
        try:
            await asyncio.to_thread(sb.kill)
        except Exception:  # noqa: BLE001
            pass

    async def ping(self) -> bool:
        return not self._config_error

    async def get_info(self) -> Optional[SandboxInfo]:
        if self._sandbox is None:
            return None
        sid = getattr(self._sandbox, "sandbox_id", "") or getattr(self._sandbox, "id", "")
        return SandboxInfo(sandbox_id=str(sid), provider=self.provider)

    async def execute(self, command: str, options: Optional[ExecuteOptions] = None) -> ExecuteResult:
        if self._sandbox is None:
            await self.create()

        def _run():
            timeout_ms = options.timeout_ms if options else None
            kwargs = {}
            if timeout_ms:
                kwargs["timeout"] = max(1, timeout_ms // 1000)
            # e2b: sandbox.commands.run(cmd) -> CommandResult(stdout, stderr, exit_code)
            return self._sandbox.commands.run(command, **kwargs)

        try:
            res = await asyncio.to_thread(_run)
        except Exception as exc:  # noqa: BLE001
            # e2b CommandExitError 携带非零退出信息
            return ExecuteResult(stdout="", stderr=str(exc), exit_code=1)
        max_bytes = (
            (options.max_output_bytes if options and options.max_output_bytes else None)
            or _DEFAULT_MAX_OUTPUT_BYTES
        )
        out, t1 = _cap_text(getattr(res, "stdout", "") or "", max_bytes)
        err, t2 = _cap_text(getattr(res, "stderr", "") or "", max_bytes)
        return ExecuteResult(
            stdout=out, stderr=err,
            exit_code=getattr(res, "exit_code", 0),
            truncated=t1 or t2,
        )

    async def write_files(self, entries: list[FileWriteEntry]) -> None:
        if self._sandbox is None:
            await self.create()

        def _write():
            for entry in entries:
                data = entry.data
                # e2b files.write(path, data)；bytes 或 str 均可
                self._sandbox.files.write(entry.path, data)

        try:
            await asyncio.to_thread(_write)
        except Exception as exc:  # noqa: BLE001
            raise SandboxError(f"E2B 写文件失败: {exc}") from exc

    async def read_files(self, paths: list[str]) -> list[FileReadResult]:
        if self._sandbox is None:
            await self.create()

        def _read():
            out = []
            for path in paths:
                try:
                    content = self._sandbox.files.read(path)
                    data = content if isinstance(content, bytes) else str(content).encode("utf-8")
                    out.append(FileReadResult(path=path, data=data))
                except Exception as exc:  # noqa: BLE001
                    out.append(FileReadResult(path=path, error=str(exc)))
            return out

        return await asyncio.to_thread(_read)
