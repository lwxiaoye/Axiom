"""SSRF 预检与实际连接的 DNS rebinding 定向测试（深度扫描 P0）。

历史缺陷：assert_public_http_url 用 socket.getaddrinfo 解析一次做校验，真正发请求的
httpx / mcp streamablehttp_client 会**再解析一次**——短 TTL 域名第一次返回公网 IP、
第二次返回 169.254.169.254（云元数据）即可绕过校验。

修复口径：PinnedPublicTransport 在 transport 层自己解析+校验并把 URL 主机改写成该 IP，
httpcore 拿到 IP 字面量不会再查 DNS，"校验的地址 = 连接的地址"；Host 头与 TLS SNI
保留原域名。本文件用 monkeypatch 的 getaddrinfo 模拟 rebinding。
"""
import socket

import httpx
import pytest

from app.services.gateway import mcp_client
from app.services.gateway.mcp_client import (
    McpClientError,
    PinnedPublicTransport,
    mcp_httpx_client_factory,
    resolve_public_http_target,
)

PUBLIC_IP = "93.184.216.34"
METADATA_IP = "169.254.169.254"


class _Resolver:
    """按调用次序返回不同解析结果（最后一个值粘住），用于模拟 DNS rebinding。"""

    def __init__(self, *sequence):
        self.sequence = list(sequence)
        self.calls = 0

    def __call__(self, host, port, *_a, **_kw):
        ip = self.sequence[min(self.calls, len(self.sequence) - 1)]
        self.calls += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]


class _BaseRecorder:
    """替身 httpx.AsyncHTTPTransport.handle_async_request：记录真正下发给 httpcore 的请求。"""

    def __init__(self):
        self.seen = []

    async def __call__(self, request):
        # 实例对象挂到类属性上不走描述符协议，因此这里没有 transport 自身的 self 形参
        self.seen.append({
            "url": str(request.url),
            "host_header": request.headers.get("host"),
            "sni": request.extensions.get("sni_hostname"),
        })
        return httpx.Response(200, text="ok")


@pytest.fixture
def resolver(monkeypatch):
    def _install(*sequence):
        r = _Resolver(*sequence)
        # loop.getaddrinfo 也是 run_in_executor(socket.getaddrinfo)，patch 模块属性即可覆盖两条路径
        monkeypatch.setattr(socket, "getaddrinfo", r)
        return r
    return _install


@pytest.fixture
def base_transport(monkeypatch):
    rec = _BaseRecorder()
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", rec)
    return rec


# ---------- 解析层 ----------

def test_resolve_returns_validated_ip(resolver):
    resolver(PUBLIC_IP)
    ip, host, port = resolve_public_http_target("https://tool.example.com/ping")
    assert (ip, host, port) == (PUBLIC_IP, "tool.example.com", 443)


@pytest.mark.parametrize("bad_ip", [METADATA_IP, "127.0.0.1", "10.1.2.3", "192.168.0.5"])
def test_resolve_rejects_internal(resolver, bad_ip):
    resolver(bad_ip)
    with pytest.raises(McpClientError):
        resolve_public_http_target("http://tool.example.com/ping")


def test_resolve_rejects_non_http(resolver):
    resolver(PUBLIC_IP)
    with pytest.raises(McpClientError):
        resolve_public_http_target("file:///etc/passwd")


# ---------- transport 层 ----------

@pytest.mark.asyncio
async def test_transport_pins_ip_and_keeps_host_and_sni(resolver, base_transport):
    resolver(PUBLIC_IP)
    transport = PinnedPublicTransport()
    request = httpx.Request("GET", "https://tool.example.com/ping?a=1")
    resp = await transport.handle_async_request(request)

    assert resp.status_code == 200
    seen = base_transport.seen[0]
    # 连接目标已被钉到校验过的 IP，httpcore 不会再做第二次 DNS 解析
    assert seen["url"] == f"https://{PUBLIC_IP}/ping?a=1"
    # 虚拟主机路由与证书校验仍按原域名
    assert seen["host_header"] == "tool.example.com"
    assert seen["sni"] == "tool.example.com"
    # 调用后还原：相对路径重定向要拼在域名 URL 上，否则下一跳 Host/SNI 会变成 IP
    assert str(request.url) == "https://tool.example.com/ping?a=1"
    assert "sni_hostname" not in request.extensions


@pytest.mark.asyncio
async def test_transport_blocks_rebinding_on_each_hop(resolver, base_transport):
    """第一跳公网、第二跳（重定向/复用）被改指元数据地址 → 第二跳照样拦。"""
    r = resolver(PUBLIC_IP, METADATA_IP)
    transport = PinnedPublicTransport()
    await transport.handle_async_request(httpx.Request("GET", "https://tool.example.com/a"))
    with pytest.raises(McpClientError):
        await transport.handle_async_request(httpx.Request("GET", "https://tool.example.com/b"))
    assert r.calls == 2
    assert len(base_transport.seen) == 1


# ---------- MCP 客户端 ----------

def test_mcp_client_factory_uses_pinned_transport():
    client = mcp_httpx_client_factory(headers={"X-A": "b"}, timeout=httpx.Timeout(5.0))
    try:
        assert isinstance(client._transport, PinnedPublicTransport)
        assert client.follow_redirects is True
        assert client.headers["X-A"] == "b"
    finally:
        # 未发起过连接，直接丢弃即可（避免 pytest-asyncio 之外再起事件循环）
        client._transport = None


@pytest.mark.asyncio
async def test_mcp_transports_receive_pinned_client_factory(resolver, monkeypatch):
    """streamable 与 SSE 降级两条路径都必须把钉 IP 的 client factory 注入 mcp SDK。"""
    resolver(PUBLIC_IP)
    import mcp.client.sse as sse_mod
    import mcp.client.streamable_http as sh_mod

    captured: dict = {}

    def _fake(kind):
        class _CM:
            def __init__(self, *_a, **kw):
                captured[kind] = kw

            async def __aenter__(self):
                raise RuntimeError("boom")

            async def __aexit__(self, *_a):
                return False
        return _CM

    monkeypatch.setattr(sh_mod, "streamablehttp_client", _fake("streamable"))
    monkeypatch.setattr(sse_mod, "sse_client", _fake("sse"))

    with pytest.raises(McpClientError):
        await mcp_client.list_mcp_tools("https://mcp.example.com/mcp")

    assert captured["streamable"]["httpx_client_factory"] is mcp_httpx_client_factory
    assert captured["sse"]["httpx_client_factory"] is mcp_httpx_client_factory


@pytest.mark.asyncio
async def test_mcp_precheck_still_rejects_internal_target(resolver):
    resolver(METADATA_IP)
    with pytest.raises(McpClientError):
        await mcp_client.list_mcp_tools("https://mcp.example.com/mcp")
