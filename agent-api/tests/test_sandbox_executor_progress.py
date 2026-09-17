from types import SimpleNamespace

import pytest

from app.services.sandbox import sandbox_executor
from app.services.sse_protocol import HARNESS, SSEChannel


class _FakeSandbox:
    async def create(self):
        return None

    async def delete(self):
        return None

    async def write_files(self, entries):
        self.entries = entries

    async def read_files(self, paths):
        return []

    async def execute(self, command, options):
        if "json.dumps" in command:
            return SimpleNamespace(ok=True, stdout="[]", stderr="", exit_code=0, truncated=False)
        return SimpleNamespace(ok=True, stdout="done", stderr="", exit_code=0, truncated=False)


@pytest.mark.asyncio
async def test_execute_in_sandbox_reports_real_phases_without_fake_percentage(monkeypatch):
    sandbox = _FakeSandbox()
    monkeypatch.setattr(sandbox_executor, "create_configured_sandbox", lambda _name: sandbox)
    monkeypatch.setattr(sandbox_executor.settings, "SKILL_SANDBOX_MAX_CONCURRENT", 0)
    phases = []

    async def progress(stage, label, detail):
        phases.append((stage, label, detail or {}))

    result = await sandbox_executor.execute_in_sandbox("print('ok')", progress_callback=progress)

    assert result.ok is True
    assert [item[0] for item in phases] == [
        "creating", "preparing", "executing", "collecting", "collected",
    ]
    assert all("%" not in item[1] for item in phases)


def test_sse_channel_emits_tool_progress_envelope():
    payload = SSEChannel(HARNESS, "thread-1", "run-1").tool_progress(
        "bash", "executing", "正在执行生成脚本", 3200, heartbeat=True,
    )

    assert '"type": "tool.progress"' in payload
    assert '"elapsed_ms": 3200' in payload
    assert '"heartbeat": true' in payload


class _FakeAddrinfo:
    def __init__(self, ips):
        self.ips = ips

    def __call__(self, host, port, *a, **kw):
        return [(2, 1, 6, "", (ip, 0)) for ip in self.ips]


@pytest.mark.asyncio
async def test_fake_ip_vpn_detection_allows_domains_but_not_ip_literals(monkeypatch):
    """fake-ip VPN（全部域名解析进 198.18.0.0/15）下搜索结果不再被 SSRF 预校验全灭；
    IP 字面量与真实私网解析仍拒绝。历史故障指纹：搜索段 200 有结果、管线回「未返回结果」。
    （_is_public_url 已协程化——P1-14 事件循环阻塞修复——断言相应改为 await）"""
    from app.services.knowledge import web_search_service as ws

    monkeypatch.setattr(ws.socket, "getaddrinfo", _FakeAddrinfo(["198.18.0.126"]))
    assert await ws._is_public_url("https://www.weather.com.cn/weather/101040100.shtml") is True
    # IP 字面量不存在“被 DNS 劫持”一说，仍拒绝
    assert await ws._is_public_url("http://198.18.0.126/") is False
    assert await ws._is_public_url("http://127.0.0.1/") is False

    # 真实私网解析（非 fake-ip 段）仍拒绝
    monkeypatch.setattr(ws.socket, "getaddrinfo", _FakeAddrinfo(["10.0.0.8"]))
    assert await ws._is_public_url("https://intranet.example.com/") is False
