"""
MCP Client 公共服务：列出工具 / 调用工具。

蓝本对齐（FastGPT packages/service/core/app/mcp.ts）：
- 传输优先 Streamable HTTP，失败降级 SSE；
- 服务端执行 + SSRF 防护（禁止私网/环回/链路本地/保留/多播地址，仅 http/https）。

SSRF 防护的落点是 PinnedPublicTransport（本模块导出，MCP 与 HTTP 工具集共用）：
校验解析与实际建连必须是**同一次解析**，否则短 TTL 域名可以在两次解析之间改指内网
（DNS rebinding）。只调 assert_public_http_url 而用裸 httpx 发请求 = 有洞。
"""
import asyncio
import ipaddress
import json
import logging
import socket
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

LIST_TIMEOUT = 20
CALL_TIMEOUT = 60


class McpClientError(Exception):
    """入参/安全校验失败或 MCP 调用失败，message 可直接展示给用户。"""


def _parse_http_target(url: str) -> tuple[str, str, int]:
    """拆出 (scheme, host, port) 并做 scheme 校验。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise McpClientError("地址仅支持 http/https")
    host = parsed.hostname
    if not host:
        raise McpClientError("地址不合法")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:  # 端口非法（如 http://h:99999/）
        raise McpClientError("地址不合法")
    return parsed.scheme, host, port


# CGNAT 段（100.64.0.0/10）不在 ipaddress 的 is_private 清单里，但 EKS/GKE/ACK 次级
# Pod CIDR、Tailscale 等都落在这段——对 SSRF 而言等同内网，显式拦截（深扫收尾 2026-07-26）
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")

# fake-ip 透明代理（Clash/Surge TUN 模式）把**公网域名**解析进 198.18.0.0/15 基准测试段，
# 该段是代理的出口通道而不是真实内网。不放行的话，代理环境下连 api.githubcopilot.com 都会
# 被自己的 SSRF 闸拦掉——2026-07-28 真机实测：GitHub 连接器连上了却一个工具都挂不上，
# 模型于是退化成用通用下载工具手搓 GitHub REST API，看着像在干活，其实连接器整个是废的。
# （image_fetch 早就为同一原因放行过这一段，这里是把同一决策扩到 MCP 连接。）
#
# 比 image_fetch 更严的一点：**只对域名放行**。工作流的 MCP 工具集允许用户填任意地址，
# 直接写 `http://198.18.0.5/` 这种 IP 字面量一律照拒——那不是代理隧道的正常用法。
# 真实内网段（10/8、172.16/12、192.168/16、127/8、169.254/16、CGNAT）不受影响。
_FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(str(host or "").strip("[]"))
        return True
    except ValueError:
        return False


def _pick_public_ip(infos, host: str = "") -> str:
    """全部解析结果都必须是公网地址；返回第一个用于「钉住」实际连接的 IP。

    host 为域名时，落在 fake-ip 段的解析结果视为代理隧道放行（见 _FAKE_IP_NET 注释）。
    """
    allow_fake_ip = bool(host) and not _is_ip_literal(host)
    chosen = ""
    for info in infos or []:
        raw = str(info[4][0]).split("%", 1)[0]  # 去掉 IPv6 scope id（fe80::1%eth0）
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            raise McpClientError("地址无法解析")
        if allow_fake_ip and ip.version == 4 and ip in _FAKE_IP_NET:
            if not chosen:
                chosen = str(ip)
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise McpClientError("禁止访问内网/保留地址")
        if ip.version == 4 and ip in _CGNAT_NET:
            raise McpClientError("禁止访问内网/保留地址")
        if not chosen:
            chosen = str(ip)
    if not chosen:
        raise McpClientError("地址无法解析")
    return chosen


def resolve_public_http_target(url: str) -> tuple[str, str, int]:
    """SSRF 防护 + 地址钉住：校验通过后返回 (已校验IP, 原始host, port)。

    与只做校验的 assert_public_http_url 的差别在于把「校验时解析到的 IP」交还给调用方，
    由调用方直接对该 IP 建连（见 PinnedPublicTransport）——原实现只校验、真正连接由
    httpx/httpcore 各自再解析一次，短 TTL 域名可以在两次解析之间把结果换成
    169.254.169.254 之类的内网地址（DNS rebinding）从而绕过校验。
    """
    _scheme, host, port = _parse_http_target(url)
    try:
        infos = socket.getaddrinfo(host, port)
    except socket.gaierror:
        raise McpClientError("地址无法解析")
    return _pick_public_ip(infos, host), host, port


async def resolve_public_http_target_async(url: str) -> tuple[str, str, int]:
    """resolve_public_http_target 的协程版（transport 内用，避免冻结事件循环）。"""
    _scheme, host, port = _parse_http_target(url)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port)
    except socket.gaierror:
        raise McpClientError("地址无法解析")
    return _pick_public_ip(infos, host), host, port


def assert_public_http_url(url: str) -> None:
    """SSRF 防护：仅 http/https，解析后的全部 IP 不得为私网/环回/链路本地/保留段（含云元数据地址）。

    被 MCP 连接、HTTP 工具、内置 http_request 与工作流 HTTP 节点共用，文案保持通用。
    注意：单独调用本函数只是「预检」——校验与实际连接是两次独立解析，存在 DNS rebinding
    窗口；真正发请求的路径应改用 PinnedPublicTransport（预检保留只为更早给出清晰错误）。
    """
    resolve_public_http_target(url)


class PinnedPublicTransport(httpx.AsyncHTTPTransport):
    """逐跳「解析→校验→钉住 IP」的 httpx transport（DNS rebinding 防护）。

    - handle_async_request 内自己解析并校验，然后把 URL 主机改写成该 IP：httpcore 拿到的
      是 IP 字面量，不会再触发第二次 DNS 查询，**校验的地址就是连接的地址**；
    - Host 头保持原样、TLS 走 sni_hostname 扩展带回原域名，证书校验与虚拟主机路由不受影响；
    - 重定向由 httpx 在 Client 层重新构造 Request 再次进入本方法，逐跳复检（MCP SDK 的
      httpx client 默认 follow_redirects=True，302 跳内网同样被挡）；
    - 调用结束后把 request.url/extensions 还原，避免相对路径重定向被拼到 IP 形态的 URL 上
      （那会让下一跳的 Host/SNI 变成 IP，HTTPS 证书校验必然失败）；
    - resolver 可注入：默认用本模块的公网校验；调用方策略不同时（如 image_fetch 对
      fake-ip 透明代理段 198.18.0.0/15 有显式放行）传入自己的 async (url)->(ip,host,port)。
    """

    def __init__(self, *args, resolver=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._resolver = resolver or resolve_public_http_target_async

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        ip, host, _port = await self._resolver(str(request.url))
        original_url = request.url
        original_extensions = dict(request.extensions or {})
        if "host" not in request.headers:
            request.headers["Host"] = original_url.netloc.decode("ascii")
        request.url = original_url.copy_with(host=ip)
        request.extensions = {**original_extensions, "sni_hostname": host}
        try:
            return await super().handle_async_request(request)
        finally:
            request.url = original_url
            request.extensions = original_extensions


def mcp_httpx_client_factory(
    headers: Optional[dict] = None,
    timeout: Optional[httpx.Timeout] = None,
    auth: Optional[httpx.Auth] = None,
) -> httpx.AsyncClient:
    """mcp SDK 的 httpx_client_factory：沿用 create_mcp_http_client 的默认，只把 transport
    换成 PinnedPublicTransport，使 MCP 的 HTTP/SSE 连接也钉在预检校验过的 IP 上。"""
    kwargs: dict = {
        "follow_redirects": True,
        "timeout": timeout if timeout is not None else httpx.Timeout(30.0),
        "transport": PinnedPublicTransport(),
    }
    if headers is not None:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)


async def _with_session(url: str, headers: Optional[dict], action):
    """Streamable HTTP 优先、SSE 降级，在初始化完的 ClientSession 上执行 action。"""
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamablehttp_client

    async def _via_streamable():
        async with streamablehttp_client(
            url, headers=headers or None, httpx_client_factory=mcp_httpx_client_factory
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await action(session)

    async def _via_sse():
        async with sse_client(
            url, headers=headers or None, httpx_client_factory=mcp_httpx_client_factory
        ) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await action(session)

    try:
        return await _via_streamable()
    except Exception as streamable_error:
        logger.info("MCP streamable transport failed, fallback to SSE: %s", streamable_error)
        return await _via_sse()


async def list_mcp_tools(url: str, headers: Optional[dict] = None) -> list[dict]:
    """连接 MCP Server 列出工具，返回 [{name, description, inputSchema}]。"""
    assert_public_http_url(url)
    clean_headers = {k: v for k, v in (headers or {}).items() if k and v}

    async def _list(session):
        return await session.list_tools()

    try:
        listed = await asyncio.wait_for(_with_session(url, clean_headers, _list), timeout=LIST_TIMEOUT)
    except McpClientError:
        raise
    except Exception as exc:
        logger.warning("MCP list tools failed: %s", exc)
        raise McpClientError(f"MCP 工具解析失败: {exc}") from exc

    tools = []
    for tool in getattr(listed, "tools", []) or []:
        name = (getattr(tool, "name", "") or "").strip()
        if not name:
            continue
        tools.append(
            {
                "name": name[:128],
                "description": (getattr(tool, "description", "") or "")[:2000],
                "inputSchema": getattr(tool, "inputSchema", None) or {},
            }
        )
    return tools


async def call_mcp_tool(url: str, headers: Optional[dict], name: str, arguments: dict) -> str:
    """调用 MCP 工具并把结果拼为文本（文本内容优先，其余序列化为 JSON）。"""
    assert_public_http_url(url)
    clean_headers = {k: v for k, v in (headers or {}).items() if k and v}

    async def _call(session):
        return await session.call_tool(name, arguments or {})

    try:
        result = await asyncio.wait_for(_with_session(url, clean_headers, _call), timeout=CALL_TIMEOUT)
    except McpClientError:
        raise
    except Exception as exc:
        logger.warning("MCP call tool %s failed: %s", name, exc)
        raise McpClientError(f"MCP 工具调用失败: {exc}") from exc

    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(str(text))
        else:
            try:
                parts.append(json.dumps(getattr(item, "__dict__", {}), ensure_ascii=False, default=str))
            except Exception:
                parts.append(str(item))
    if getattr(result, "isError", False):
        raise McpClientError("MCP 工具返回错误: " + ("\n".join(parts))[:500])
    return "\n".join(parts)


def headers_from_config(config: Any) -> dict:
    """configJson.headers（[{key,value}] 或 dict）-> dict。"""
    if isinstance(config, dict):
        return {str(k): str(v) for k, v in config.items() if k and v}
    result: dict = {}
    for row in config or []:
        if isinstance(row, dict) and row.get("key"):
            result[str(row["key"])] = str(row.get("value") or "")
    return result
