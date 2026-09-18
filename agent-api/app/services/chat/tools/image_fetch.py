# -*- coding: utf-8 -*-
"""配图下载器（2026-07-22 PPT 配图链路）。

沙箱是断网的（§7.1），配图字节必须由平台侧下载后经 execute_in_sandbox 的输入通道注入
/workspace/inputs/。本模块只做一件事：把模型从 search_web 图片目录里挑出的直链，
安全地拉回为 (文件名 -> bytes)。

安全护栏（服务端出网新增面，逐条收紧）：
- 仅 http/https；下载前后都做主机 IP 校验，拒绝私网/环回/链路本地/保留段（防 SSRF 打内网）
- 单张 ≤8MB、总数 ≤6、超时 10s、重定向 ≤5（httpx 默认上限内）
- 魔数嗅探必须是真图片（jpeg/png/webp/gif/bmp），Content-Type 不作数——
  非图片字节一律丢弃，杜绝「借配图通道拉任意内网内容进沙箱」
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import posixpath
import re
import socket
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

MAX_ITEMS = 6
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT_S = 10.0

_MAGIC = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
)

_NAME_SAFE_RE = re.compile(r"[^\w一-鿿.-]+")


def _sniff_ext(data: bytes) -> str | None:
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


# fake-ip 透明代理（Clash/Surge TUN 模式）把公网域名解析进 198.18.0.0/15 基准测试段，
# 该段是代理的出口通道而非真实内网；不放行则代理环境下所有公网图源全被误杀。
# 真实内网段（10/8、172.16/12、192.168/16、127/8、169.254/16）不受影响，照拦。
_FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")


# 单次 DNS 解析超时（2026-07-28 补，与 web_search_service._DNS_RESOLVE_TIMEOUT 同口径）。
# loop.getaddrinfo 是异步的，不会冻结事件循环，但**没有超时**：坏域名 / DNS 不响应时它会
# 一路等到系统默认超时（可达数十秒），而每张图都要解析一次——一条 download_url 就能把
# TOOL_CALL_TIMEOUT_SECONDS=300 占满，用户看到的是"下载图片卡住了"。
_DNS_RESOLVE_TIMEOUT = 2.0


async def _host_is_public(host: str) -> bool:
    """解析主机全部地址，任何一个落在私网/环回/保留段都拒绝。"""
    try:
        infos = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(
                host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM),
            timeout=_DNS_RESOLVE_TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        # 超时按"解析不出来"处理 = 拒绝（fail-closed）：拿不到 IP 就无法判断是不是内网
        return False
    addrs = {info[4][0] for info in infos}
    if not addrs:
        return False
    for raw in addrs:
        try:
            ip = ipaddress.ip_address(raw.split("%")[0])
        except ValueError:
            return False
        if ip.version == 4 and ip in _FAKE_IP_NET:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return True


async def _url_allowed(url: str) -> str | None:
    """返回 None=允许；否则返回拒绝原因。"""
    try:
        p = urlparse(url)
    except ValueError:
        return "URL 无法解析"
    if p.scheme not in ("http", "https"):
        return "仅支持 http/https"
    if not p.hostname:
        return "缺少主机名"
    if not await _host_is_public(p.hostname):
        return "主机不可达或位于内网（已拦截）"
    return None


async def _resolve_pinned_target(url: str) -> tuple[str, str, int]:
    """PinnedPublicTransport 的本地 resolver：语义同 _host_is_public（含 fake-ip 放行），
    额外把「校验时解析到的 IP」钉给实际连接——预检与建连分两次 DNS 解析存在 rebinding
    窗口（深扫收尾 2026-07-26），本 resolver 关掉这个窗口。全部地址必须过检，取首个建连。"""
    from app.services.gateway.mcp_client import McpClientError, _parse_http_target

    _scheme, host, port = _parse_http_target(url)
    try:
        # 同 _host_is_public：解析必须有超时，否则慢 DNS 能把整个工具超时预算占满
        infos = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(
                host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM),
            timeout=_DNS_RESOLVE_TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        raise McpClientError("地址无法解析")
    chosen = ""
    for info in infos or []:
        raw = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            raise McpClientError("地址无法解析")
        if not (ip.version == 4 and ip in _FAKE_IP_NET):
            if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                    or ip.is_multicast or ip.is_unspecified):
                raise McpClientError("主机位于内网/保留段（已拦截）")
        if not chosen:
            chosen = str(ip)
    if not chosen:
        raise McpClientError("地址无法解析")
    return chosen, host, port


_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def _safe_name(name: str, ext: str, used: set) -> str:
    """模型请求的文件名带图片扩展名时**原样尊重**（沙箱侧 PIL 按字节解码不看扩展名，
    改名反而会让模型后续的 data-src 引用扑空）；无扩展名才用嗅探结果补。"""
    base = posixpath.basename(str(name or "").strip()) or "image"
    base = _NAME_SAFE_RE.sub("_", base)[:64].strip("._") or "image"
    lower = base.lower()
    if lower.endswith(_IMG_EXTS):
        stem, dot, tail = base.rpartition(".")
        candidate = base
    else:
        stem, tail = base.rsplit(".", 1)[0], ext.lstrip(".")
        candidate = f"{stem}.{tail}"
    i = 2
    while candidate in used:
        candidate = f"{stem}-{i}.{tail}"
        i += 1
    used.add(candidate)
    return candidate


_VET_PROMPT = (
    "这张图片将用作演示文稿(PPT)的配图。判断标准放宽——**只要画面本身能看清主体、"
    "像一张正经图片，就算合格**。\n"
    "只在以下情况判不合格：①网页/软件应用的界面截图（有工具栏/菜单/按钮/浏览器地址栏）；"
    "②糊到看不清主体、分辨率极低；③表情包/聊天截图/纯文字长图/多图硬拼。\n"
    "以下一律合格（不要因为这些拒绝）：实景照片、场景氛围图、游戏/影视/动漫画面与截图、"
    "**带标题或 logo 的官方海报/封面/宣传原画**、产品图、**带水印的图**（水印不影响判为合格）。\n"
    '只输出 JSON：{"ok": true/false, "reason": "不超过20字的理由"}'
)


def _vet_prompt(semantic_requirement: str = "") -> str:
    requirement = str(semantic_requirement or "").strip()
    if not requirement:
        return _VET_PROMPT
    return (
        "这张图片将用于一份明确要求真实照片的演示文稿。"
        f"用户要求：{requirement[:400]}\n"
        "这是逐张候选图审核，当前只输入一张图是正常的。只判断这张图本身，"
        "不要检查整份 PPT 的图片数量、来源数量、PPTX 文件或页面是否已提供；"
        "不得以‘只有一张照片’或‘未提供 PPTX’为拒绝理由。"
        "必须同时满足：①它是真实摄影照片，不是插画、矢量图、火柴人、海报、封面、"
        "纯文字图、界面截图或 AI 概念图；②主体和场景与用户要求直接相关；③主体清晰，"
        "可以作为页面中的主要视觉证据。只要任一条件不满足就判不合格。"
        '只输出 JSON：{"ok": true/false, "reason": "不超过20字的理由"}'
    )


async def vet_images(
    files: dict,
    newapi_key: str,
    *,
    semantic_requirement: str = "",
) -> tuple[dict, list]:
    """视觉审查（2026-07-22 配图质量闸）：逐张让视觉模型把关，滤掉带大字横幅/界面截图/
    水印图——标题挑图看不出这些。复用 OCR 多模态配置；模型不可用时放行（降级开，
    配图质量问题不该让整条链路瘫痪）。返回 (通过{name:bytes}, 拒绝[(name, reason)])。"""
    import base64 as _b64
    import json as _json

    from app.core.model_endpoint import get_model_base_url
    from app.core.config import settings
    from app.services.platform import platform_config_service as cfg

    try:
        conf = await cfg.get_ocr_config()
    except Exception:  # noqa: BLE001
        return files, []
    model = str(conf.get("model") or "").strip()
    vision_base = str(conf.get("visionBaseUrl") or "").strip().rstrip("/")
    if vision_base:
        base_url, api_key = vision_base, str(conf.get("visionApiKey") or "").strip()
    else:
        base_url, api_key = get_model_base_url().rstrip("/"), newapi_key
    if not model or not api_key:
        return files, []

    ok: dict = {}
    rejected: list = []
    sem = asyncio.Semaphore(3)

    async def one(name: str, data: bytes):
        from app.services.agent_harness import model_usage_audit
        from app.services.chat.tools.base import current_tool_context

        mime = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
        wire_payload = {
            "model": model, "temperature": 0.1, "max_tokens": 100,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": _vet_prompt(semantic_requirement)},
                {"type": "image_url", "image_url": {"url": (
                    f"data:{mime};base64,{_b64.b64encode(data).decode()}")}},
            ]}],
        }
        tool_context = current_tool_context()
        run_id = str(tool_context.run_id or "") if tool_context is not None else ""
        thread_id = str(tool_context.thread_id or "") if tool_context is not None else ""
        logical = None
        if run_id:
            logical = await model_usage_audit.begin_logical_call(
                run_id=run_id,
                thread_id=thread_id,
                model=model,
                transport="chat_completions",
                purpose="tool_internal",
                purpose_detail="image_fetch_vet",
                scope_key="tool_internal:image_fetch_vet",
                provider_api_key=api_key,
            )
        else:
            logger.warning(
                "model_usage_orphan purpose=tool_internal purpose_detail=image_fetch_vet "
                "reason=missing_run_id"
            )
        attempt = (
            await model_usage_audit.begin_attempt(
                logical, wire_payload=wire_payload, attempt_kind="initial",
            )
            if logical is not None else None
        )
        resp = None
        response_payload: dict = {}
        try:
            async with sem:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        f"{base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=wire_payload,
                    )
                response_payload = resp.json()
                text = (response_payload["choices"][0]["message"]["content"] or "").strip()
                m = re.search(r"\{.*\}", text, re.S)
                verdict = _json.loads(m.group(0)) if m else {"ok": True}
                committed = resp.status_code < 400
                await model_usage_audit.finish_attempt(
                    attempt,
                    terminal_status="completed" if committed else "http_error",
                    usage=model_usage_audit.provider_usage_from_response(response_payload),
                    response_id=model_usage_audit.provider_response_id(response_payload),
                    provider_event_seen=True,
                    terminal_seen=True,
                    http_status=resp.status_code,
                    committed=committed,
                )
                await model_usage_audit.finish_logical_call(
                    logical,
                    terminal_status="completed" if committed else "failed",
                    selected_attempt_id=(attempt.attempt_id if attempt is not None and committed else ""),
                    committed=committed,
                )
        except asyncio.CancelledError:
            response_seen = resp is not None
            await model_usage_audit.finish_attempt(
                attempt,
                terminal_status="cancelled",
                usage=model_usage_audit.provider_usage_from_response(response_payload),
                response_id=model_usage_audit.provider_response_id(response_payload),
                provider_event_seen=response_seen,
                terminal_seen=response_seen,
                http_status=getattr(resp, "status_code", None),
                unknown_provider_charge=True,
                committed=False,
            )
            await model_usage_audit.finish_logical_call(
                logical, terminal_status="cancelled", committed=False,
            )
            raise
        except Exception as e:  # noqa: BLE001 审查不可用→放行该图
            response_seen = resp is not None
            await model_usage_audit.finish_attempt(
                attempt,
                terminal_status="invalid_response" if response_seen else "failed",
                usage=model_usage_audit.provider_usage_from_response(response_payload),
                response_id=model_usage_audit.provider_response_id(response_payload),
                provider_event_seen=response_seen,
                terminal_seen=response_seen,
                http_status=getattr(resp, "status_code", None),
                error_code=type(e).__name__,
                committed=False,
            )
            await model_usage_audit.finish_logical_call(
                logical, terminal_status="failed", committed=False,
            )
            logger.info("配图视觉审查降级放行 %s: %s", name, e)
            verdict = {"ok": True}
        if verdict.get("ok", True):
            ok[name] = data
        else:
            rejected.append((name, str(verdict.get("reason") or "不适合作配图")))

    await asyncio.gather(*(one(n, d) for n, d in files.items()))
    return ok, rejected


async def fetch_image_urls(items: list) -> tuple[dict, list]:
    """items: [{"url": str, "name": str?}, ...] -> (files{name: bytes}, errors[str])。"""
    files: dict = {}
    errors: list = []
    used: set = set()
    cleaned = []
    for it in list(items or [])[:MAX_ITEMS]:
        if isinstance(it, str):
            it = {"url": it}
        if not isinstance(it, dict) or not str(it.get("url") or "").strip():
            errors.append("条目缺少 url 字段")
            continue
        cleaned.append({"url": str(it["url"]).strip(), "name": str(it.get("name") or "").strip(),
                        "referer": str(it.get("referer") or "").strip()})
    if len(list(items or [])) > MAX_ITEMS:
        errors.append(f"配图最多 {MAX_ITEMS} 张，超出部分已忽略")

    from app.services.gateway.mcp_client import PinnedPublicTransport

    # 逐跳钉 IP（重定向由 httpx 重新构造请求再进 transport，302 跳内网同样被挡）；
    # 下方流内的 resp.url.host 复检保留作纵深防御
    async with httpx.AsyncClient(
        timeout=TIMEOUT_S, follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (deck-image-fetch)"},
        transport=PinnedPublicTransport(resolver=_resolve_pinned_target),
    ) as client:
        sem = asyncio.Semaphore(3)

        async def one(it: dict):
            url = it["url"]
            short = url if len(url) <= 80 else url[:77] + "…"
            reason = await _url_allowed(url)
            if reason:
                errors.append(f"{short}：{reason}")
                return
            # 防盗链（抖音/小红书等 CDN 校验 Referer 常 403）：带上图片所在页面作为
            # Referer——与浏览器打开该页面加载图片的行为一致
            headers = {}
            referer = str(it.get("referer") or "").strip()
            if referer.startswith(("http://", "https://")):
                headers["Referer"] = referer
            async with sem:
                last_err = ""
                buf = bytearray()
                for attempt in range(2):  # 国内图源超时/瞬断高发：传输类错误重试一次
                    buf = bytearray()
                    try:
                        async with client.stream("GET", url, headers=headers) as resp:
                            if resp.status_code != 200:
                                errors.append(f"{short}：HTTP {resp.status_code}")
                                return
                            final_host = resp.url.host or ""
                            if not await _host_is_public(final_host):
                                errors.append(f"{short}：重定向落到内网地址（已拦截）")
                                return
                            async for chunk in resp.aiter_bytes():
                                buf.extend(chunk)
                                if len(buf) > MAX_BYTES:
                                    errors.append(f"{short}：超过 8MB 上限")
                                    return
                        last_err = ""
                        break
                    except httpx.TransportError as e:
                        last_err = type(e).__name__
                        continue
                    except httpx.HTTPError as e:
                        errors.append(f"{short}：下载失败（{type(e).__name__}）")
                        return
                if last_err:
                    errors.append(f"{short}：下载失败（{last_err}，已重试）")
                    return
            data = bytes(buf)
            ext = _sniff_ext(data)
            if ext is None:
                errors.append(f"{short}：内容不是图片（可能是防盗链页），换一张")
                return
            files[_safe_name(it["name"], ext, used)] = data

        await asyncio.gather(*(one(it) for it in cleaned))
    return files, errors
