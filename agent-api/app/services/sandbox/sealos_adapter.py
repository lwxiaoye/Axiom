"""Sealos Devbox provider（云开发环境）。对齐蓝本 SealosDevboxAdapter（DevboxApi REST）。

蓝本 REST 契约（sdk/sandbox-adapter SealosDevboxAdapter/api.ts）：
  POST   /api/v1/devbox                 创建
  GET    /api/v1/devbox/{name}          信息
  POST   /api/v1/devbox/{name}/exec     执行 {command:[], stdin?, timeoutSeconds?, container?}
  DELETE /api/v1/devbox/{name}          删除
  鉴权：Authorization: Bearer <token>；响应信封 {code, message, data}

Sealos devbox 通常是持久开发环境：本 adapter 连接到已配置的 devbox（name），exec 执行命令；
文件读写经 exec + base64（Sealos REST 只暴露 exec，无独立文件端点）。需真实 Sealos 集群 + token。
"""
from __future__ import annotations

import base64
import shlex
from typing import Optional

import httpx

from .base import (
    ExecuteOptions,
    ExecuteResult,
    FileReadResult,
    FileWriteEntry,
    SandboxAdapter,
    SandboxError,
    SandboxInfo,
    SandboxNotConfigured,
)

_WORKSPACE = "/home/devbox/project"
# REST 响应体一次性整段返回 stdout/stderr，这里只能对已返回的字符串做事后截断，
# 与 opensandbox_adapter 同一兜底值对齐（P1-13 同类缺口）。
_DEFAULT_MAX_OUTPUT_BYTES = 131072


def _cap_text(text: str, limit: int) -> tuple[str, bool]:
    if limit and len(text) > limit:
        return text[:limit], True
    return text, False


class SealosDevboxAdapter(SandboxAdapter):
    provider = "sealosdevbox"

    def __init__(self, connection_config: Optional[dict] = None, create_config: Optional[dict] = None):
        cfg = connection_config or {}
        self._base_url = str(cfg.get("baseUrl") or cfg.get("base_url") or "").rstrip("/")
        self._token = cfg.get("token")
        self._name = cfg.get("name") or cfg.get("devboxName")
        self._container = cfg.get("container")
        self._workspace = cfg.get("workspace") or _WORKSPACE
        self._create_cfg = create_config or {}

    def _require_config(self):
        if not self._base_url or not self._token:
            raise SandboxNotConfigured(
                "Sealos Devbox provider 需配置 baseUrl + token（SKILL_SANDBOX_SEALOS_*）"
            )
        if not self._name:
            raise SandboxNotConfigured("Sealos Devbox provider 需配置 devbox name")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, json_body: Optional[dict] = None) -> dict:
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.request(method, url, headers=self._headers(), json=json_body)
        if resp.status_code >= 400:
            raise SandboxError(f"Sealos API {resp.status_code}: {resp.text[:300]}")
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            return {"data": resp.text}
        if isinstance(body, dict) and body.get("code") not in (None, 0, 200):
            raise SandboxError(f"Sealos API 业务错误: {body.get('message')}")
        return body if isinstance(body, dict) else {"data": body}

    # ---------- 生命周期 ----------
    async def create(self) -> None:
        # Sealos devbox 通常持久存在：默认连接已有实例。若给了 createSpec 则创建
        self._require_config()
        if self._create_cfg.get("createSpec"):
            await self._request("POST", "/api/v1/devbox", self._create_cfg["createSpec"])

    async def delete(self) -> None:
        # 持久 devbox 默认不删（避免误删共享环境）；显式 autoDelete 才删
        if self._create_cfg.get("autoDelete") and self._base_url and self._token and self._name:
            try:
                await self._request("DELETE", f"/api/v1/devbox/{self._name}")
            except SandboxError:
                pass

    async def get_info(self) -> Optional[SandboxInfo]:
        try:
            self._require_config()
            body = await self._request("GET", f"/api/v1/devbox/{self._name}")
        except SandboxError:
            return None
        data = body.get("data") or {}
        return SandboxInfo(
            sandbox_id=str(self._name), provider=self.provider, state=str(data.get("phase") or "Running")
        )

    async def ping(self) -> bool:
        try:
            self._require_config()
            return True
        except SandboxNotConfigured:
            return False

    # ---------- 命令执行 ----------
    async def execute(self, command: str, options: Optional[ExecuteOptions] = None) -> ExecuteResult:
        self._require_config()
        workdir = (options.working_directory if options else None) or self._workspace
        timeout_s = (options.timeout_ms // 1000) if (options and options.timeout_ms) else 30
        req = {
            "command": ["bash", "-lc", f"cd {shlex.quote(workdir)} && {command}"],
            "timeoutSeconds": max(1, min(timeout_s, 600)),
        }
        if self._container:
            req["container"] = self._container
        body = await self._request("POST", f"/api/v1/devbox/{self._name}/exec", req)
        data = body.get("data") or {}
        stdout = str(data.get("stdout") or "")
        stderr = str(data.get("stderr") or "")
        # 只在调用方显式传 max_output_bytes 时才截断（P1-13 同类缺口）：本 adapter 的
        # read_files 借用 execute() 的 stdout 传 base64 文件内容（无 options），若默认兜底
        # 截断会在文件超过阈值时把 base64 payload 切断、读回损坏数据——不能像
        # local/opensandbox 那样无条件套默认上限。
        max_bytes = options.max_output_bytes if options else None
        out, t1 = _cap_text(stdout, max_bytes) if max_bytes else (stdout, False)
        err, t2 = _cap_text(stderr, max_bytes) if max_bytes else (stderr, False)
        return ExecuteResult(
            stdout=out, stderr=err,
            exit_code=data.get("exitCode") if data.get("exitCode") is not None else 0,
            truncated=t1 or t2,
        )

    # ---------- 文件系统（经 exec + base64）----------
    async def write_files(self, entries: list[FileWriteEntry]) -> None:
        for entry in entries:
            data = entry.data if isinstance(entry.data, bytes) else str(entry.data).encode("utf-8")
            b64 = base64.b64encode(data).decode("ascii")
            path = entry.path if entry.path.startswith("/") else f"{self._workspace}/{entry.path}"
            cmd = f"mkdir -p {shlex.quote(path.rsplit('/', 1)[0])} && echo {shlex.quote(b64)} | base64 -d > {shlex.quote(path)}"
            if entry.path.endswith(".sh"):
                cmd += f" && chmod +x {shlex.quote(path)}"
            result = await self.execute(cmd)
            if not result.ok:
                raise SandboxError(f"Sealos 写文件失败 {entry.path}: {result.stderr}")

    async def read_files(self, paths: list[str]) -> list[FileReadResult]:
        results = []
        for path in paths:
            full = path if path.startswith("/") else f"{self._workspace}/{path}"
            result = await self.execute(f"base64 {shlex.quote(full)}")
            if not result.ok:
                results.append(FileReadResult(path=path, error=result.stderr or "读取失败"))
                continue
            try:
                results.append(FileReadResult(path=path, data=base64.b64decode(result.stdout.strip())))
            except Exception as exc:  # noqa: BLE001
                results.append(FileReadResult(path=path, error=str(exc)))
        return results
