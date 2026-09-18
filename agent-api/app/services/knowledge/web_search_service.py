"""联网搜索三段式管线运行时（ADR-037）：搜索 → 抓取 → 重排。

配置来自管理端 `agent_platform_config.web_search`（platform_config_service）。每一段都
优雅降级：某段不可用/未配置/报错时跳过该段，用上一段结果继续。未启用时 search_web
返回 enabled=False，调用方据此不注册 search_web 工具。
"""
import asyncio
import json
import re
import ipaddress
import logging
import socket
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from app.core.model_endpoint import get_model_base_url
from app.core.config import settings
from app.services.platform import platform_config_service as cfg
from app.services.gateway.mcp_client import assert_public_http_url
from .web_engine_health import engine_health
from .public_page_reader import read_public_page

logger = logging.getLogger(__name__)

# fake-ip 模式 VPN（Clash/Surge 等）把所有公网域名都解析进 RFC 2544 基准测试保留段
# 198.18.0.0/15（该段专用于此，真实互联网/内网都不该出现）。命中即说明本机 DNS 被代理
# 接管，「解析后校验实际 IP」的结论不可信。
_FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")

# 公网锚点：正常网络下必解析到真实公网 IP。fake-ip 模式 VPN 会把它们也解析进 198.18.x。
# 据此把「DNS 被 fake-ip 接管」判定为**环境级事实**，而非某个域名被攻击者自建 DNS 构造成
# 全解析 198.18.x（后者可绕过 SSRF 预检——本次加固要堵的正是这条）。探测结果缓存 30s。
_FAKE_IP_ANCHORS = ("www.baidu.com", "www.qq.com", "example.com")
_FAKE_IP_HIJACK_TTL = 30.0
_fake_ip_hijack_cache: Dict[str, float] = {"ts": -1e18, "val": 0.0}
# 单次 DNS 解析超时：agent-api 单进程单事件循环，任何一次同步阻塞的 getaddrinfo 都会冻结
# 同进程所有并发 SSE 流；改走 loop.getaddrinfo（原生异步）之上再加超时，防坏域名/DNS 无响应
# 把解析拖到系统默认超时（可达数十秒）（P1-14）。
_DNS_RESOLVE_TIMEOUT = 2.0


async def _dns_hijacked_by_fake_ip() -> bool:
    """探测本机 DNS 是否被 fake-ip 代理接管：任一公网锚点解析进 fake-ip 段即判定为真。"""
    now = time.monotonic()
    if now - _fake_ip_hijack_cache["ts"] < _FAKE_IP_HIJACK_TTL:
        return bool(_fake_ip_hijack_cache["val"])
    hijacked = False
    loop = asyncio.get_running_loop()
    for host in _FAKE_IP_ANCHORS:
        try:
            infos = await asyncio.wait_for(loop.getaddrinfo(host, None), timeout=_DNS_RESOLVE_TIMEOUT)
            ips = {ipaddress.ip_address(i[4][0]) for i in infos}
        except (socket.gaierror, ValueError, OSError, asyncio.TimeoutError):
            continue
        if ips and all(ip.version == 4 and ip in _FAKE_IP_NET for ip in ips):
            hijacked = True
            break
    _fake_ip_hijack_cache["ts"] = now
    _fake_ip_hijack_cache["val"] = 1.0 if hijacked else 0.0
    return hijacked

_TIMEOUT = 12
# Native search includes model execution and upstream page reads before the
# non-streaming response arrives. A SERP timeout aborts otherwise healthy calls.
_NATIVE_SEARCH_TIMEOUT = httpx.Timeout(60.0, connect=5.0, write=12.0, pool=5.0)
_NATIVE_SEARCH_TOTAL_SECONDS = 75.0
_MAX_SCRAPE = 3          # 仅抓取排名靠前的若干条正文
# 单条抓取上限：并发抓取下整段耗时≈最慢一条，超时该条放弃正文改用搜索摘要（优雅降级）。
# 实测页面两极分布（2026-07-09，9 页样本）：抓得动的 2-4s 完成，反爬站（tianqi/CSDN 等）
# 空转到超时也是 0 字，4-7.5s 间无成功案例——故砍到 5s，晚放弃只是白等。
# 注意条数（_MAX_SCRAPE）不影响耗时（并发取 max），减条数只会降低"至少一条成功"的概率。
_SCRAPE_TIMEOUT = 5
_SEARCH_CANDIDATE_CAP = 12  # 搜索段多取候选，质量重排后再截 topK（防好摘要落在第 6+）
_CONTENT_LIMIT = 1500    # 单条正文进入上下文的字符上限
# 自托管 CPU 重排（TEI）对长中文文本很慢：实测 2000字×5≈28s、500字×5≈5s。相关性信号
# 集中在标题+开头，故送入前单条截到 _RERANK_LOCAL_DOC_CHARS，并单独放宽超时（SaaS 的
# jina/cohere 很快，仍用 _TIMEOUT）。超时/失败照旧静默回退不重排。
# platform（平台重排模型）是云端按量计费，同样只送截断后的标题+开头。
_RERANK_LOCAL_DOC_CHARS = 500
_RERANK_LOCAL_TIMEOUT = 20

# Provider 进程级熔断：自建主路失败时转 DeepSeek，但持续故障不要
# 每次都再等一遍超时。合法空结果 error=""，不记故障。
_PROVIDER_FAILURE_THRESHOLD = 3
_PROVIDER_OPEN_SECONDS = 120.0
_provider_health: Dict[str, Dict[str, Any]] = {}
_provider_health_lock = asyncio.Lock()
_RESEARCH_HYBRID_BUDGET_KEY = "deepseek_research_hybrid"
_DEEPSEEK_DIAGNOSTIC_MAX_OUTPUT_ITEMS = 64
_DEEPSEEK_DIAGNOSTIC_MAX_LABELS = 16


def _provider_trust_env(url: str) -> bool:
    """School/LAN provider endpoints must not be forwarded to a public proxy.

    This applies only to configured service endpoints, never to result URLs.
    Public providers keep the deployment's existing proxy behavior.
    """
    host = (urlparse(url).hostname or "").lower()
    if host in {"localhost", "host.docker.internal", "searxng", "firecrawl", "agent-searxng"}:
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


def _provider_error_code(message: str) -> str:
    text = str(message or "")
    match = re.search(r"HTTP\s+(\d{3})", text, re.I)
    if match:
        return f"http_{match.group(1)}"
    if "重定向" in text:
        return "redirect_rejected"
    if (
        "结构化" in text
        or "web_search_tool_result" in text
        or "未执行服务端联网搜索" in text
        or "未返回可引用来源" in text
    ):
        return "missing_structured_result"
    prefix = text.split(":", 1)[0].strip().lower()
    return re.sub(r"[^a-z0-9_-]+", "_", prefix)[:64] or "provider_error"


def failure_code(message: str) -> str:
    """Stable, credential-free categories for receipts and public status copy."""
    text = str(message or "").lower()
    for tokens, code in (
        (("captcha", "验证码"), "engine_captcha"),
        (("冷却", "cooldown", "suspended"), "engine_cooldown"),
        (("结构化", "web_search_tool_result", "未执行服务端联网搜索", "未返回可引用来源"),
         "missing_structured_result"),
        (("429",), "rate_limited"),
        (("timeout", "超时", "408"), "timeout"),
        (("url_not_public", "private", "公网", "公开地址"), "url_not_public"),
        (("403", "401", "access denied"), "access_denied"),
        (("未配置", "未启用", "未分配"), "not_configured"),
        (("empty_body", "unusable_body"), "unusable_body"),
        (("connect", "network", "gaierror"), "network_error"),
    ):
        if any(token in text for token in tokens):
            return code
    return "provider_error"


def _provider_configured(provider: str, c: Dict[str, Any]) -> bool:
    if provider == "deepseek-official":
        return bool(str(c.get("deepseekApiKey") or "").strip())
    if provider == "searxng":
        return bool(str(c.get("searxngUrl") or "").strip())
    if provider == "serper":
        return bool(str(c.get("serperApiKey") or "").strip())
    if provider == "tavily":
        return bool(str(c.get("tavilyApiKey") or "").strip())
    return False


def _provider_health_key(provider: str, scope: str = "") -> str:
    # DeepSeek 使用用户已分配的 NewAPI Key：某个用户的 Key 异常不能熔断其他用户。
    return f"{provider}:{scope}" if provider == "deepseek-official" and scope else provider


async def _provider_available(provider: str, scope: str = "", *, reserve: bool = True) -> bool:
    health_key = _provider_health_key(provider, scope)
    async with _provider_health_lock:
        state = _provider_health.get(health_key)
        if not state or not state.get("opened_at"):
            return True
        if time.monotonic() - float(state["opened_at"]) < _PROVIDER_OPEN_SECONDS:
            return False
        if state.get("half_open"):
            return False
        if reserve:
            state["half_open"] = True
            state["probe_owner"] = asyncio.current_task()
        return True


async def _release_provider_probe(provider: str, scope: str = "") -> None:
    async with _provider_health_lock:
        state = _provider_health.get(_provider_health_key(provider, scope))
        if state and state.get("probe_owner") is asyncio.current_task():
            state["half_open"] = False
            state.pop("probe_owner", None)


async def _record_provider_health(provider: str, *, success: bool, scope: str = "") -> None:
    health_key = _provider_health_key(provider, scope)
    async with _provider_health_lock:
        state = _provider_health.setdefault(health_key, {"failures": 0, "opened_at": 0.0, "half_open": False})
        state.pop("probe_owner", None)
        if success:
            state.update({"failures": 0, "opened_at": 0.0, "half_open": False})
            return
        state["failures"] = int(state.get("failures") or 0) + 1
        state["half_open"] = False
        if state["failures"] >= _PROVIDER_FAILURE_THRESHOLD:
            state["opened_at"] = time.monotonic()


async def _ordered_search_providers(c: Dict[str, Any]) -> List[str]:
    rows = c.get("providerPool") if isinstance(c.get("providerPool"), list) else []
    if not rows:
        rows = [{"id": str(c.get("searchProvider") or "searxng"), "enabled": True}]
    primary: List[str] = []
    fallback: List[str] = []
    for row in rows:
        if not isinstance(row, dict) or not bool(row.get("enabled", True)):
            continue
        provider = str(row.get("id") or "").strip().lower()
        health_scope = str(c.get("_callerUserId") or "") if provider == "deepseek-official" else ""
        if not _provider_configured(provider, c) or not await _provider_available(provider, health_scope, reserve=False):
            continue
        target = fallback if bool(row.get("fallbackOnly")) else primary
        if provider not in target:
            target.append(provider)
    return primary + fallback


def _canonical_result_key(item: Dict[str, Any]) -> str:
    """Normalize common tracking variants before merging provider results."""
    raw = str(item.get("url") or "").strip()
    try:
        parsed = urlparse(raw)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            query = urlencode([
                (key, value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if not key.lower().startswith(("utm_", "spm"))
                and key.lower() not in {"fbclid", "gclid", "yclid"}
            ], doseq=True)
            return urlunparse((
                parsed.scheme.lower(), parsed.netloc.lower().removeprefix("www."),
                parsed.path.rstrip("/") or "/", "", query, "",
            ))
    except ValueError:
        pass
    title = str(item.get("title") or "").strip().lower()
    content = str(item.get("content") or item.get("snippet") or "").strip().lower()
    return f"text:{title}|{content[:160]}"


def _merge_provider_results(
    outcomes: List[tuple[str, List[Dict[str, Any]]]],
) -> List[Dict[str, Any]]:
    """Interleave independent providers, then merge duplicate URLs with lineage."""
    merged: List[Dict[str, Any]] = []
    positions: Dict[str, int] = {}
    width = max((len(rows) for _provider, rows in outcomes), default=0)
    for offset in range(width):
        for provider, rows in outcomes:
            if offset >= len(rows) or not isinstance(rows[offset], dict):
                continue
            candidate = dict(rows[offset])
            raw_providers = candidate.get("searchProviders")
            providers = [
                str(value) for value in raw_providers if value
            ] if isinstance(raw_providers, list) else []
            if provider not in providers:
                providers.append(provider)
            candidate["searchProvider"] = str(candidate.get("searchProvider") or provider)
            candidate["searchProviders"] = providers
            key = _canonical_result_key(candidate)
            existing_index = positions.get(key)
            if existing_index is None:
                positions[key] = len(merged)
                merged.append(candidate)
                continue
            existing = merged[existing_index]
            raw_existing_providers = existing.get("searchProviders")
            existing_providers = [
                str(value) for value in raw_existing_providers if value
            ] if isinstance(raw_existing_providers, list) else []
            for value in providers:
                if value not in existing_providers:
                    existing_providers.append(value)
            existing["searchProviders"] = existing_providers
            if len(str(candidate.get("content") or "")) > len(str(existing.get("content") or "")):
                existing["content"] = candidate.get("content") or ""
            if not existing.get("title") and candidate.get("title"):
                existing["title"] = candidate["title"]
            if not existing.get("publishedDate") and candidate.get("publishedDate"):
                existing["publishedDate"] = candidate["publishedDate"]
    return merged[:_SEARCH_CANDIDATE_CAP]


def _research_deepseek_query(query: str) -> str:
    """Ask the independent provider for diverse evidence, not a duplicate SERP."""
    value = str(query or "").strip()
    if not value or "site:" in value.lower():
        return value
    return (
        value
        + "\n优先查找一手资料、官方文档、论文、代码仓库及不同域名的独立来源；"
          "补充国内搜索可能遗漏的国际资料和可公开访问的替代页面。"
    )


def _deepseek_responses_search_rows(
    data: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], bool]:
    """Extract citeable URLs from completed DeepSeek Responses web-search calls.

    DeepSeek's Responses API executes search server-side.  It does not support
    OpenAI's ``include=web_search_call.action.sources`` switch, so the durable
    source facts are the URLs from ``open_page``/``find_in_page`` actions plus
    any URL citations or source arrays a compatible gateway preserves.  Plain
    model prose is never treated as proof that a search ran.
    """
    rows: List[Dict[str, Any]] = []
    positions: Dict[str, int] = {}
    search_seen = False

    def add(value: Any, *, fallback_title: str = "", content: str = "") -> None:
        if not isinstance(value, dict):
            return
        nested = value.get("url_citation")
        source = nested if isinstance(nested, dict) else value
        url = str(source.get("url") or "").strip()
        try:
            parsed = urlparse(url)
        except ValueError:
            return
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return
        title = str(source.get("title") or fallback_title or parsed.hostname or url).strip()
        snippet = str(
            source.get("snippet") or source.get("content") or content or ""
        ).strip()
        published = str(
            source.get("published_date") or source.get("publishedDate") or ""
        ).strip()
        key = _canonical_result_key({"url": url})
        existing = positions.get(key)
        if existing is None:
            positions[key] = len(rows)
            rows.append({
                "title": title[:500],
                "url": url[:2000],
                "content": snippet[:1500],
                "publishedDate": published[:40],
            })
            return
        row = rows[existing]
        if (not row.get("title") or row.get("title") == parsed.hostname) and title:
            row["title"] = title[:500]
        if len(snippet) > len(str(row.get("content") or "")):
            row["content"] = snippet[:1500]
        if not row.get("publishedDate") and published:
            row["publishedDate"] = published[:40]

    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "web_search_call" and item.get("status") == "completed":
            search_seen = True
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            sources = action.get("sources")
            if isinstance(sources, list):
                for source in sources:
                    add(source)
            if action.get("type") in {"open_page", "find_in_page"}:
                add(action)
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            for annotation in part.get("annotations") or []:
                add(annotation)
    return rows, search_seen


def _deepseek_responses_structure_diagnostic(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return a bounded response-shape summary without response content or identifiers."""

    def label(value: Any) -> str:
        if value is None or value == "":
            return "missing"
        if not isinstance(value, str):
            return f"invalid_{type(value).__name__}"
        normalized = value.strip().lower()
        if re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,63}", normalized):
            return normalized
        return "other"

    def bump(counts: Dict[str, int], value: Any) -> None:
        key = label(value)
        if key not in counts and len(counts) >= _DEEPSEEK_DIAGNOSTIC_MAX_LABELS:
            key = "other"
        counts[key] = counts.get(key, 0) + 1

    raw_output = data.get("output")
    output = raw_output if isinstance(raw_output, list) else []
    inspected = output[:_DEEPSEEK_DIAGNOSTIC_MAX_OUTPUT_ITEMS]
    item_types: Dict[str, int] = {}
    item_statuses: Dict[str, int] = {}
    action_types: Dict[str, int] = {}
    annotation_count = 0
    source_count = 0
    non_object_count = 0

    for item in inspected:
        if not isinstance(item, dict):
            non_object_count += 1
            continue
        bump(item_types, item.get("type"))
        bump(item_statuses, item.get("status"))
        action = item.get("action")
        if isinstance(action, dict):
            bump(action_types, action.get("type"))
            sources = action.get("sources")
            if isinstance(sources, list):
                source_count += len(sources)
        content = item.get("content")
        for part in content if isinstance(content, list) else []:
            if not isinstance(part, dict):
                continue
            annotations = part.get("annotations")
            if isinstance(annotations, list):
                annotation_count += len(annotations)

    return {
        "response_status": label(data.get("status")),
        "output_count": len(output),
        "inspected_output_count": len(inspected),
        "output_truncated": len(output) > len(inspected),
        "non_object_output_count": non_object_count,
        "output_item_types": item_types,
        "output_item_statuses": item_statuses,
        "action_types": action_types,
        "annotation_count": annotation_count,
        "source_count": source_count,
    }


def _log_deepseek_responses_failure(
    *, reason: str, data: Dict[str, Any], run_id: str = "",
) -> None:
    diagnostic = _deepseek_responses_structure_diagnostic(data)
    logger.warning(
        "deepseek_search_response_rejected reason=%s run_id=%s response_structure=%s",
        reason,
        re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(run_id or ""))[:128],
        json.dumps(diagnostic, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
    )

# 抓取结果可用性闸（2026-08-05）：firecrawl/反爬常把 404/nginx/验证码页当 markdown 吐回。
# 旧逻辑「有字就覆盖 content」，会把搜索摘要（往往还有用）换成垃圾正文，模型只能空口说查不到。
# 宁可保留搜索摘要，也不用错误页顶替。
_USELESS_SCRAPE_RE = re.compile(
    r"(?is)(404\s*not\s*found|not\s*found\s*=+|\bnginx\b|verifycode|验证码|"
    r"访问验证|安全验证|captcha|just a moment|access denied|403\s*forbidden|"
    r"request blocked|cloudflare|机器人验证|请完成.*?验证|\berror\s*404\b)"
)


def _usable_scrape_text(text: str) -> bool:
    """抓取正文是否值得覆盖搜索摘要。"""
    t = str(text or "").strip()
    if len(t) < 80:
        return False
    head = t[:800]
    if re.search(r"(?im)^\s*(?:#{1,6}\s*)?(?:server load too high|503 service unavailable|429 too many requests)\s*$", head):
        return False
    if _USELESS_SCRAPE_RE.search(head):
        return False
    # 几乎只有导航/壳层：有效汉字/字母过少
    signal = re.sub(r"\s+", "", head)
    if len(signal) < 60:
        return False
    return True


def _query_date_tokens(query: str) -> list[str]:
    """从查询里抽日期 token，用于轻量时效加权（非领域词表）。"""
    q = str(query or "")
    tokens: list[str] = []
    for m in re.finditer(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", q):
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        tokens.extend([f"{y}年{mo}月{d}日", f"{y}-{mo:02d}-{d:02d}", f"{y}{mo:02d}{d:02d}", f"{mo}月{d}日"])
    for m in re.finditer(r"(20\d{2})-(\d{1,2})-(\d{1,2})", q):
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        tokens.extend([f"{y}-{mo:02d}-{d:02d}", f"{y}{mo:02d}{d:02d}", f"{y}年{mo}月{d}日"])
    # 去重保序
    seen = set()
    out = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _topic_tokens(query: str, date_tokens: list[str] | None = None) -> list[str]:
    """从 query 抽主题词（去掉日期后做中文 2/3-gram），供时效加权与主题相关性并用。

    整段中文若当作一个 token，天气页标题对不上长问句；仅日期命中又会把当日
    无关新闻顶到前面。n-gram + 停用词是结构信号，不是领域词表。
    """
    q = str(query or "")
    for tok in (date_tokens or []):
        if tok:
            q = q.replace(tok, " ")
    q = re.sub(r"20\d{2}年?|\d{1,2}月|\d{1,2}日|今天|今日|明天|昨日", " ", q)
    stop = {
        "状况", "情况", "如何", "怎么", "什么", "查询", "看看", "帮我",
        "一下", "相关", "信息", "最新", "实时", "给出", "告诉",
        "是否", "可以", "需要", "请问",
    }
    for sw in sorted(stop, key=len, reverse=True):
        q = q.replace(sw, " ")
    out: list[str] = []
    seen: set[str] = set()

    def _add(tok: str) -> None:
        tok = str(tok or "").strip()
        if not tok or tok in stop or tok in seen:
            return
        seen.add(tok)
        out.append(tok)

    for run in re.findall(r"[一-鿿]+", q):
        if len(run) <= 3:
            _add(run)
        else:
            _add(run[:2])
            _add(run[-2:])
            for n in (2, 3):
                for i in range(0, len(run) - n + 1):
                    _add(run[i:i + n])
    for word in re.findall(r"[A-Za-z]{3,}|\d{3,}", q):
        _add(word)
    return out


def _content_concrete_score(item: Dict[str, Any]) -> int:
    """摘要/正文可核验信号（单位数值、区间、足够正文）。结构信号，非领域词表。

    只用 content 计分：黄历/入口页常把地名+日期写进标题，但正文是导航壳；
    真正带 ℃/%/区间 的摘要才应排到前面。
    """
    content = str(item.get("content") or "").strip()
    if not content:
        return -2
    score = 0
    # [0-9] 避免通道吞反斜杠数字类转义
    if re.search(r"[0-9]+[ ]*(?:℃|°C|%|mm|hPa|km(?:/h)?|m/s|级|万|亿|元|美元|USD|¥)", content):
        score += 4
    if re.search(r"(?<![0-9])[0-9]{1,3}[ ]*[~/～-][ ]*[0-9]{1,3}(?![0-9])", content):
        score += 2
    if len(content) >= 120 and re.search(r"[0-9]{2,}", content):
        score += 1
    if len(content) < 40:
        score -= 1
    if (not re.search(r"[0-9]", content)) and len(content) < 120:
        score -= 2
    return score


def _snippets_already_concrete(results: List[Dict[str, Any]], *, min_hits: int = 1) -> bool:
    """搜索摘要里已有可核验细节时，可跳过抓取以省墙钟（天气站反爬常白等 5s）。"""
    hits = sum(1 for r in (results or []) if isinstance(r, dict) and _content_concrete_score(r) >= 3)
    return hits >= max(1, int(min_hits))


def _freshness_boost_key(
    item: Dict[str, Any],
    date_tokens: list[str],
    topic_tokens: list[str] | None = None,
) -> tuple:
    """Rank: concrete content > topic hits > (topic and date) > publishedDate.

    Pure date hits do not boost. Concrete numeric evidence outranks title-only shells.
    """
    blob = f"{item.get('title') or ''} {item.get('url') or ''} {item.get('content') or ''}"
    topics = topic_tokens or []
    topic_hits = sum(1 for tok in topics if tok and tok in blob)
    date_hit = any(tok and tok in blob for tok in date_tokens)
    date_boost = 1 if date_hit and topic_hits > 0 else 0
    pub = str(item.get("publishedDate") or "").strip()
    pub_key = pub[:10] if pub else ""
    concrete = _content_concrete_score(item)
    return (concrete, topic_hits, date_boost, pub_key)


async def _resolves_into_fake_ip_net(url: str) -> bool:
    """域名 URL 的全部解析结果都落在 fake-ip 保留段 → 判定 DNS 被代理接管。

    仅对域名放行（IP 字面量写 198.18.x 仍按保留段拒绝）；此时本地预校验失去意义，
    SSRF 防护交给抓取端（自托管 Firecrawl，服务器侧真实 DNS）自行兜底。
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host or parsed.scheme not in ("http", "https"):
            return False
        ipaddress.ip_address(host)
        return False  # IP 字面量：不存在“被 DNS 劫持”一说，维持拒绝
    except ValueError:
        pass  # host 是域名，继续解析判断
    try:
        loop = asyncio.get_running_loop()
        infos = await asyncio.wait_for(loop.getaddrinfo(host, None), timeout=_DNS_RESOLVE_TIMEOUT)
        ips = {ipaddress.ip_address(info[4][0]) for info in infos}
        all_fake = bool(ips) and all(ip.version == 4 and ip in _FAKE_IP_NET for ip in ips)
    except (socket.gaierror, ValueError, OSError, asyncio.TimeoutError):
        return False
    if not all_fake:
        return False
    # 收窄（SSRF 加固）：域名全解析进 198.18.x 是攻击者自建 DNS 就能构造的单域名属性，
    # 单看它会被绕过。只有本机公网锚点也落 fake-ip 段（环境级 DNS 劫持确证）时才放行；
    # 否则这个"全解析 198.18.x"的域名就是可疑构造，按内网/保留地址拒绝。
    if not await _dns_hijacked_by_fake_ip():
        logger.warning(
            "SSRF：域名 %s 全解析进 fake-ip 段，但本机 DNS 未被接管（公网锚点正常）→ 判为可疑构造，拒绝",
            host,
        )
        return False
    logger.warning("SSRF 预检放行：本机 DNS 被 fake-ip 接管，域名 %s 交抓取端（自托管 Firecrawl）兜底", host)
    return True


async def _is_public_url(url: str) -> bool:
    """SSRF 防护：抓取段仅允许公网 http/https 目标，拒绝 localhost/私网/链路本地/
    保留段/云元数据地址（DNS 解析后校验实际 IP）。复用与 HTTP 工具/MCP 同一守卫。

    fake-ip 模式 VPN 会让所有公网域名都解析进 198.18.0.0/15、令预校验全数误拦
    （历史故障指纹：搜索段 200 有结果、管线却回「未返回结果」）——识别到该特征时
    自动放行域名 URL，不再依赖手动逃生阀。

    异步化（P1-14）：agent-api 单进程单事件循环，同步 socket.getaddrinfo 会冻结同进程
    所有并发 SSE 流；mcp_client.assert_public_http_url 内部仍是同步实现（不在本簇改动
    范围），丢进线程池执行避免阻塞事件循环，本文件自有的两处解析改走原生异步 API。"""
    if not url:
        return False
    # 手动逃生阀保留（默认关，生产务必保持关闭）：完全跳过本地预校验。
    if settings.WEB_SEARCH_SKIP_URL_SSRF_CHECK:
        return True
    try:
        # 主路径也要限时：搜索结果里慢解析/不可达域名很常见，不限时的话该请求自身
        # 仍会被单个坏域名拖 3s+（事件循环虽不再冻结，但用户还是干等）。超时直接拒——
        # fake-ip 劫持是本机 DNS，解析必然瞬回，不会因限时被误伤，无需再走兜底解析
        await asyncio.wait_for(asyncio.to_thread(assert_public_http_url, url), timeout=_DNS_RESOLVE_TIMEOUT + 1.0)
        return True
    except asyncio.TimeoutError:
        return False
    except Exception:  # noqa: BLE001
        return await _resolves_into_fake_ip_net(url)


async def _filter_by_public_url(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """并发跑 SSRF 预检、丢弃没有 url 的条目（抓取段专用：无 url 本就抓不了）。

    并发而非逐条 await 是顺带收益——DNS 解析改异步后天然可以批量并发，不会比原来的
    同步串行更慢（P1-14）。"""
    candidates = [i for i in items if i.get("url")]
    if not candidates:
        return []
    checks = await asyncio.gather(*(_is_public_url(i["url"]) for i in candidates))
    return [i for i, ok in zip(candidates, checks) if ok]


async def _true() -> bool:
    return True


async def _filter_public_or_no_url(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """并发跑 SSRF 预检；没有 url 的条目本就不会被抓取，直接保留（search_web 结果过滤专用）。"""
    if not items:
        return items
    checks = await asyncio.gather(*(
        _is_public_url(r["url"]) if r.get("url") else _true() for r in items
    ))
    return [r for r, ok in zip(items, checks) if ok]


async def _paid_provider_post(
    *,
    c: Dict[str, Any],
    provider: str,
    model: str,
    transport: str,
    url: str,
    headers: Optional[Dict[str, str]],
    payload: Dict[str, Any],
    provider_api_key: str,
    follow_redirects: bool = False,
    purpose_detail: str = "",
    timeout_seconds: float | httpx.Timeout = _TIMEOUT,
    total_timeout_seconds: float | None = None,
):
    """Issue one paid upstream request and retain its audit handles until validation."""
    from app.services.agent_harness import model_usage_audit

    logical = attempt = None
    run_id = str(c.get("_callerRunId") or "")
    if run_id:
        logical = await model_usage_audit.begin_logical_call(
            run_id=run_id,
            root_run_id=str(c.get("_callerRootRunId") or ""),
            thread_id=str(c.get("_callerThreadId") or ""),
            parent_logical_call_id=str(c.get("_callerParentLogicalCallId") or ""),
            parent_tool_call_id=str(c.get("_callerToolCallId") or ""),
            model=model,
            transport=transport,
            purpose="paid_search",
            purpose_detail=(purpose_detail or f"search_provider:{provider}")[:200],
            scope_key=f"paid_search:{provider}",
            provider_api_key=provider_api_key,
        )
        attempt = await model_usage_audit.begin_attempt(
            logical,
            wire_payload=payload,
            attempt_kind="http_paid_search",
            execution_segment=str(c.get("_callerExecutionSegment") or ""),
            legacy_compatible=False,
        )
    try:
        async with asyncio.timeout(total_timeout_seconds), httpx.AsyncClient(
            timeout=timeout_seconds, follow_redirects=follow_redirects,
            trust_env=_provider_trust_env(url),
        ) as client:
            resp = await client.post(url, headers=headers, json=payload)
        try:
            data = resp.json()
        except Exception:
            if resp.status_code >= 300:
                data = {}
            else:
                raise
        return resp, data, model_usage_audit, logical, attempt
    except asyncio.CancelledError:
        await model_usage_audit.finish_attempt(
            attempt,
            terminal_status="cancelled",
            provider_event_seen=False,
            unknown_provider_charge=True,
            committed=False,
        )
        await model_usage_audit.finish_logical_call(
            logical, terminal_status="cancelled", committed=False,
        )
        raise
    except Exception as exc:
        await model_usage_audit.finish_attempt(
            attempt,
            terminal_status="failed",
            provider_event_seen=False,
            error_code=type(exc).__name__,
            committed=False,
        )
        await model_usage_audit.finish_logical_call(
            logical, terminal_status="failed", committed=False,
        )
        raise


async def _finish_paid_provider(
    audit,
    logical,
    attempt,
    *,
    data: Dict[str, Any],
    http_status: int,
    terminal_status: str,
    committed: bool,
) -> None:
    await audit.finish_attempt(
        attempt,
        terminal_status=terminal_status,
        usage=audit.provider_usage_from_response(data),
        response_id=audit.provider_response_id(data),
        provider_event_seen=True,
        terminal_seen=True,
        http_status=http_status,
        committed=committed,
    )
    await audit.finish_logical_call(
        logical,
        terminal_status=terminal_status,
        selected_attempt_id=(attempt.attempt_id if attempt and committed else ""),
        committed=committed,
    )


async def _stage_search_single(
    query: str, c: Dict[str, Any], provider: str,
) -> tuple[List[Dict[str, Any]], str]:
    """返回 (结果, 失败原因)。失败原因非空 = **搜索后端没跑通**，不是「没搜到」。

    为什么要把这两件事分开（2026-07-28）：SearXNG 挂掉 / 超时 / 429 / 引擎被 CAPTCHA 封
    此前全都被 `except Exception: return []` 吞掉，与「确实没有结果」长成同一个样子。
    模型分辨不出来，于是直接告诉用户「没有查到相关信息」——一个可修的后端故障被说成
    了事实结论，用户既不会重试也不会报障。抓取段/重排段可以静默降级（还有上一段的结果
    垫底），**搜索段不行**：它一失败就什么都没有了。
    """
    top_k = int(c.get("topK") or 5)
    # 多取候选再重排：引擎原始序常把黄历/入口壳顶到前面，真正带数值的摘要在第 6+。
    candidate_k = max(top_k, min(_SEARCH_CANDIDATE_CAP, max(top_k * 2, 10)))
    try:
        if provider == "searxng":
            base = str(c.get("searxngUrl") or "").strip().rstrip("/")
            if not base:
                return [], "未配置 SearXNG 地址（searxngUrl 为空）"
            headers = {}
            if c.get("searxngApiKey"):
                headers["Authorization"] = f"Bearer {c['searxngApiKey']}"
            params: Dict[str, Any] = {"q": query, "format": "json"}
            # 显式指定引擎（境内服务器须用国内引擎，否则默认聚合命中被墙引擎→0 结果）。
            engines = str(c.get("searxngEngines") or "").strip()
            if engines:
                selected = engine_health.select(base, engines)
                if not selected:
                    return [], "搜索引擎冷却中，请等待恢复或使用已配置的备用搜索服务"
                params["engines"] = ",".join(selected)
            else:
                selected = []

            async def _fetch(p: Dict[str, Any]):
                async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=_provider_trust_env(base)) as client:
                    return await client.get(
                        f"{base}/search",
                        params=p,
                        headers=headers,
                    )

            resp = await _fetch(params)
            # 429（限流）/5xx（实例挂了）在这里必须显式识别：SearXNG 这两种响应都是 HTML，
            # 直接 resp.json() 会抛 JSONDecodeError 被下面的 except 收成一句含糊的
            # "JSONDecodeError"，看日志的人根本猜不到是被限流了。
            if resp.status_code >= 400:
                return [], f"搜索后端返回 HTTP {resp.status_code}"
            data = resp.json()
            rows = list(data.get("results") or [])
            # Never remove the configured engine boundary after an empty response.
            # Even a partially successful response can report failing engines.
            engine_health.record(base, selected, data.get("unresponsive_engines") or [])
            if not rows:
                unresp = data.get("unresponsive_engines") or []
                if unresp:
                    # 引擎挂了 ≠ 真没结果：据实上报，别让模型说「没有相关信息」
                    sample = ", ".join(
                        f"{x[0]}:{x[1]}" if isinstance(x, (list, tuple)) and len(x) >= 2 else str(x)
                        for x in unresp[:4]
                    )
                    return [], f"搜索引擎暂不可用（{sample}）"
            # publishedDate：新闻类引擎会带，透传给模型辨别结果新旧（时效性问题关键信号）
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                    "publishedDate": str(r.get("publishedDate") or "")[:19],
                }
                for r in rows[:candidate_k]
            ], ""

        if provider == "deepseek-official":
            c.pop("_deepseekUsage", None)
            key = str(c.get("deepseekApiKey") or "").strip()
            if not key:
                return [], "当前用户未分配模型 API Key"
            # 固定走平台 NewAPI 网关。DeepSeek 的 Responses 原生 web_search 会在
            # 服务端执行；旧 Messages 路径在未开启渠道透传时会被 NewAPI 转换成
            # 客户端 tool_use，只产生费用却没有搜索结果，不能再用。
            base = get_model_base_url().rstrip("/")
            payload = {
                "model": str(c.get("deepseekModel") or "deepseek-v4-flash"),
                "instructions": (
                    "必须使用服务端 web_search 检索真实网页。只围绕用户查询做一次简洁检索，"
                    "优先打开不超过 4 个最相关的一手或独立来源，然后用一句话结束；不要虚构网址。"
                ),
                "input": query,
                "tools": [{"type": "web_search"}],
                # A forced named web_search remains forced during DeepSeek's
                # server-side auto-continuation and can loop until its 10-round cap.
                "tool_choice": "auto",
                "reasoning": {"effort": "none"},
                "max_output_tokens": max(1024, int(c.get("deepseekMaxTokens") or 4096)),
                "stream": False,
            }
            resp, data, audit, logical, attempt = await _paid_provider_post(
                c=c,
                provider=provider,
                model=str(payload["model"]),
                transport="responses",
                url=f"{base}/responses",
                headers={
                    "authorization": f"Bearer {key}",
                    "content-type": "application/json",
                },
                payload=payload,
                provider_api_key=key,
                follow_redirects=False,
                timeout_seconds=_NATIVE_SEARCH_TIMEOUT,
                total_timeout_seconds=_NATIVE_SEARCH_TOTAL_SECONDS,
            )
            if 300 <= resp.status_code < 400:
                await _finish_paid_provider(
                    audit, logical, attempt,
                    data=data, http_status=resp.status_code,
                    terminal_status="failed", committed=False,
                )
                return [], "DeepSeek 搜索网关拒绝跟随重定向"
            if resp.status_code >= 400:
                await _finish_paid_provider(
                    audit, logical, attempt,
                    data=data, http_status=resp.status_code,
                    terminal_status="failed", committed=False,
                )
                return [], f"搜索后端返回 HTTP {resp.status_code}"
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            c["_deepseekUsage"] = {
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
            }
            rows, search_seen = _deepseek_responses_search_rows(data)
            response_status = str(data.get("status") or "").strip().lower()
            if response_status == "failed":
                _log_deepseek_responses_failure(
                    reason="response_failed", data=data,
                    run_id=str(c.get("_callerRunId") or ""),
                )
                await _finish_paid_provider(
                    audit, logical, attempt,
                    data=data, http_status=resp.status_code,
                    terminal_status="failed", committed=False,
                )
                return [], "DeepSeek Responses 服务端搜索执行失败"
            if not search_seen:
                _log_deepseek_responses_failure(
                    reason="search_not_executed", data=data,
                    run_id=str(c.get("_callerRunId") or ""),
                )
                await _finish_paid_provider(
                    audit, logical, attempt,
                    data=data, http_status=resp.status_code,
                    terminal_status="incomplete", committed=False,
                )
                return [], "DeepSeek Responses 未执行服务端联网搜索"
            if not rows:
                _log_deepseek_responses_failure(
                    reason="no_citeable_sources", data=data,
                    run_id=str(c.get("_callerRunId") or ""),
                )
                await _finish_paid_provider(
                    audit, logical, attempt,
                    data=data, http_status=resp.status_code,
                    terminal_status="incomplete", committed=False,
                )
                return [], "DeepSeek 服务端搜索未返回可引用来源"
            terminal_status = "completed" if response_status == "completed" else "incomplete"
            await _finish_paid_provider(
                audit, logical, attempt,
                data=data, http_status=resp.status_code,
                terminal_status=terminal_status, committed=True,
            )
            return rows[:candidate_k], ""

        if provider == "serper":
            key = str(c.get("serperApiKey") or "")
            if not key:
                return [], "未配置 Serper API Key"
            payload = {"q": query, "num": candidate_k}
            resp, data, audit, logical, attempt = await _paid_provider_post(
                c=c,
                provider=provider,
                model="serper-search",
                transport="search_api",
                url="https://google.serper.dev/search",
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
                payload=payload,
                provider_api_key=key,
            )
            if resp.status_code >= 400:
                await _finish_paid_provider(
                    audit, logical, attempt,
                    data=data, http_status=resp.status_code,
                    terminal_status="failed", committed=False,
                )
                return [], f"搜索后端返回 HTTP {resp.status_code}"
            rows = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("link", ""),
                    "content": r.get("snippet", ""),
                    "publishedDate": str(r.get("date") or ""),
                }
                for r in (data.get("organic") or [])[:candidate_k]
            ]
            await _finish_paid_provider(
                audit, logical, attempt,
                data=data, http_status=resp.status_code,
                terminal_status="completed", committed=True,
            )
            return rows, ""

        if provider == "tavily":
            key = str(c.get("tavilyApiKey") or "")
            if not key:
                return [], "未配置 Tavily API Key"
            payload = {"api_key": key, "query": query, "max_results": candidate_k}
            resp, data, audit, logical, attempt = await _paid_provider_post(
                c=c,
                provider=provider,
                model="tavily-search",
                transport="search_api",
                url="https://api.tavily.com/search",
                headers=None,
                payload=payload,
                provider_api_key=key,
            )
            if resp.status_code >= 400:
                await _finish_paid_provider(
                    audit, logical, attempt,
                    data=data, http_status=resp.status_code,
                    terminal_status="failed", committed=False,
                )
                return [], f"搜索后端返回 HTTP {resp.status_code}"
            rows = [
                {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
                for r in (data.get("results") or [])[:candidate_k]
            ]
            await _finish_paid_provider(
                audit, logical, attempt,
                data=data, http_status=resp.status_code,
                terminal_status="completed", committed=True,
            )
            return rows, ""
    except Exception as e:  # noqa: BLE001
        logger.warning("联网搜索[搜索段/%s]失败: %s", provider, e)
        return [], f"{type(e).__name__}: {str(e)[:160]}"
    return [], f"未知的搜索后端 {provider!r}"


async def _stage_search(
    query: str,
    c: Dict[str, Any],
    *,
    providers: Optional[List[str]] = None,
) -> tuple[List[Dict[str, Any]], str]:
    """Try the configured primary once, then fall back on failure or an empty native search."""
    provider_order = list(providers) if providers is not None else await _ordered_search_providers(c)
    c["_searchMode"] = str(c.get("providerMode") or "primary_fallback")
    c["_searchProvidersAttempted"] = []
    c["_searchProvidersUsed"] = []
    c["_searchProviderFailures"] = {}
    if not provider_order:
        rows = c.get("providerPool") if isinstance(c.get("providerPool"), list) else []
        if not rows:
            rows = [{"id": str(c.get("searchProvider") or "searxng"), "enabled": True}]
        enabled = [
            str(row.get("id") or "").strip().lower()
            for row in rows
            if isinstance(row, dict) and bool(row.get("enabled", True))
        ]
        configured = [provider for provider in enabled if _provider_configured(provider, c)]
        if configured:
            # _ordered_search_providers 已检查配置和熔断状态。这里绝不能为了生成错误文案
            # 再调用一次 first_enabled，否则会绕过 open/half-open 屏障并重复烧上游配额。
            reason = "所有已配置的联网搜索 Provider 均处于熔断冷却期"
            if "deepseek-official" in enabled and not _provider_configured("deepseek-official", c):
                reason += "；当前用户未分配模型 API Key，付费兜底不可用"
            return [], reason
        if "deepseek-official" in enabled and not _provider_configured("deepseek-official", c):
            return [], "当前用户未分配模型 API Key，付费兜底不可用"
        return [], "没有已启用且配置完整的联网搜索 Provider（相关地址或 Key 未配置）"
    failures = []
    skipped = []
    healthy_empty = False
    for index, provider in enumerate(provider_order):
        if provider == "deepseek-official" and c.get("_researchDepth"):
            claim = getattr(c.get("_requestScope"), "claim_budget", None)
            limit = max(1, min(12, int(c.get("deepseekResearchMaxQueries") or 3)))
            if not callable(claim) or not await claim(_RESEARCH_HYBRID_BUDGET_KEY, limit=limit):
                # Budget exhaustion is not an upstream failure and must not open its circuit.
                c["_deepseekBudgetExhausted"] = True
                skipped.append("deepseek-official: 本轮搜索额度已用完或缺少共享预算")
                continue
        health_scope = str(c.get("_callerUserId") or "") if provider == "deepseek-official" else ""
        if not await _provider_available(provider, health_scope):
            failures.append(f"{provider}: 搜索服务处于冷却期")
            continue
        c["_searchProvidersAttempted"].append(provider)
        started = time.monotonic()
        provider_query = (_research_deepseek_query(query)
                          if provider == "deepseek-official" and c.get("_researchDepth")
                          and not c.get("_requireRichContent") else query)
        try:
            results, error = await _stage_search_single(provider_query, c, provider)
        except BaseException:
            await _release_provider_probe(provider, health_scope)
            raise
        latency_ms = int((time.monotonic() - started) * 1000)
        health_scope = str(c.get("_callerUserId") or "") if provider == "deepseek-official" else ""
        if not error:
            await _record_provider_health(provider, success=True, scope=health_scope)
            c["_searchProvidersUsed"].append(provider)
            usage = c.get("_deepseekUsage") if provider == "deepseek-official" and isinstance(c.get("_deepseekUsage"), dict) else {}
            logger.info(
                "web_search_provider provider=%s status=succeeded latency_ms=%s sources=%s fallback=%s "
                "user_id=%s run_id=%s input_tokens=%s output_tokens=%s",
                provider, latency_ms, len(results), index > 0,
                health_scope, str(c.get("_callerRunId") or "") if health_scope else "",
                int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0),
            )
            if results:
                return results, ""
            healthy_empty = True
            if index == len(provider_order) - 1:
                return [], "; ".join(failures)[:800]
            if provider != "deepseek-official" and not c.get("_researchFallbackOnEmpty"):
                return [], ""
            continue
        await _record_provider_health(provider, success=False, scope=health_scope)
        c["_searchProviderFailures"][provider] = failure_code(error)
        failures.append(f"{provider}: {error}")
        logger.warning(
            "web_search_provider provider=%s status=failed latency_ms=%s fallback=%s "
            "user_id=%s run_id=%s reason=%s",
            provider, latency_ms, index + 1 < len(provider_order), health_scope,
            str(c.get("_callerRunId") or "") if health_scope else "", error[:160],
        )
    fallback_enabled = any(
        isinstance(row, dict)
        and str(row.get("id") or "").strip().lower() == "deepseek-official"
        and bool(row.get("enabled", True))
        for row in (c.get("providerPool") or [])
    )
    if failures and fallback_enabled and not str(c.get("deepseekApiKey") or "").strip():
        failures.append("deepseek-official: 当前用户未分配模型 API Key，付费兜底不可用")
    return [], "; ".join(failures or ([] if healthy_empty else skipped))[:800]


async def _stage_research_search(
    query: str,
    c: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], str]:
    """Research shares the sequential provider chain and a strict team-wide paid budget."""
    c["_researchDepth"] = True
    c["_researchFallbackOnEmpty"] = True
    return await _stage_search(query, c)


async def _scrape_one_firecrawl(
    url: str, base: str, key: str, c: Dict[str, Any],
) -> str:
    endpoint = (base or "https://api.firecrawl.dev").rstrip("/") + "/v1/scrape"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "url": url,
        "formats": ["markdown"],
        "timeout": _SCRAPE_TIMEOUT * 1000 - 500,
    }
    resp, data, audit, logical, attempt = await _paid_provider_post(
        c=c,
        provider="firecrawl",
        model="firecrawl-scrape",
        transport="scrape_api",
        url=endpoint,
        headers=headers,
        payload=payload,
        provider_api_key=key,
        purpose_detail="scrape_provider:firecrawl",
        timeout_seconds=_SCRAPE_TIMEOUT,
    )
    if resp.status_code >= 400:
        await _finish_paid_provider(
            audit, logical, attempt, data=data, http_status=resp.status_code,
            terminal_status="failed", committed=False,
        )
        resp.raise_for_status()
    inner = data.get("data") or {}
    text_value = str(inner.get("markdown") or inner.get("content") or "")
    await _finish_paid_provider(
        audit, logical, attempt, data=data, http_status=resp.status_code,
        terminal_status="completed" if text_value else "incomplete",
        committed=bool(text_value),
    )
    return text_value


async def _stage_scrape(results: List[Dict[str, Any]], c: Dict[str, Any],
                        progress_cb=None, max_scrape: Optional[int] = None) -> List[Dict[str, Any]]:
    provider = str(c.get("scraperProvider") or "none")
    if provider == "none" or not results:
        return results
    cap = _MAX_SCRAPE if max_scrape is None else max(1, int(max_scrape))

    async def _notify(item: Dict[str, Any]) -> None:
        # 逐源阅读进度上报（可选）：只做展示，任何异常都不影响抓取本身
        if progress_cb is None:
            return
        try:
            await progress_cb(item)
        except Exception:  # noqa: BLE001
            pass

    try:
        if provider == "firecrawl":
            base = str(c.get("firecrawlUrl") or "")
            key = str(c.get("firecrawlApiKey") or "")

            async def _fill_one(item: Dict[str, Any]) -> None:
                await _notify(item)
                async def read():
                    errors = []
                    # Campus evidence also extracts trusted images from page Markdown.
                    readers = ("firecrawl",) if c.get("_requireRichContent") else ("direct", "firecrawl")
                    for reader in readers:
                        try:
                            text = (await read_public_page(item["url"]) if reader == "direct"
                                    else await _scrape_one_firecrawl(item["url"], base, key, c))
                            if _usable_scrape_text(text):
                                return {"ok": True, "content": text, "reader": reader, "error": ""}
                            errors.append(f"{reader}:unusable_body")
                        except Exception as exc:  # noqa: BLE001
                            errors.append(f"{reader}:{failure_code(type(exc).__name__ + ':' + str(exc))}")
                    return {"ok": False, "error": ";".join(errors)}

                scope = c.get("_requestScope")
                read_key = ("page", item["url"], base, bool(c.get("_requireRichContent")))
                outcome = await scope.fetch(read_key, read) if scope is not None else await read()
                item.setdefault("snippet", str(item.get("content") or "")[:1500])
                if outcome["ok"]:
                    item.update(content=outcome["content"], scraped=True, reader=outcome["reader"],
                                content_kind="full_text")
                else:
                    item.update(read_error=outcome["error"], content_kind="search_snippet")
                if outcome.get("cache_hit"):
                    item["read_cache_hit"] = True

            # 并发抓取：整段耗时≈最慢一条（≤_SCRAPE_TIMEOUT），串行逐条累加曾是全管线最大瓶颈
            targets = await _filter_by_public_url(results[:cap])
            if targets:
                await asyncio.gather(*(_fill_one(i) for i in targets))
        elif provider == "tavily":
            key = str(c.get("tavilyApiKey") or "")
            tavily_targets = await _filter_by_public_url(results[:cap])
            for item in tavily_targets:
                await _notify(item)
            urls = [item["url"] for item in tavily_targets]
            if key and urls:
                payload = {"api_key": key, "urls": urls}
                resp, data, audit, logical, attempt = await _paid_provider_post(
                    c=c,
                    provider="tavily-extract",
                    model="tavily-extract",
                    transport="scrape_api",
                    url="https://api.tavily.com/extract",
                    headers=None,
                    payload=payload,
                    provider_api_key=key,
                    purpose_detail="scrape_provider:tavily_extract",
                )
                if resp.status_code >= 400:
                    await _finish_paid_provider(
                        audit, logical, attempt, data=data, http_status=resp.status_code,
                        terminal_status="failed", committed=False,
                    )
                    resp.raise_for_status()
                by_url = {r.get("url"): r.get("raw_content", "") for r in (data.get("results") or [])}
                await _finish_paid_provider(
                    audit, logical, attempt, data=data, http_status=resp.status_code,
                    terminal_status="completed", committed=True,
                )
                for item in results:
                    raw = by_url.get(item.get("url"))
                    if raw and _usable_scrape_text(str(raw)):
                        item["content"] = raw
                        item["scraped"] = True
    except Exception as e:  # noqa: BLE001
        logger.warning("联网搜索[抓取段/%s]失败: %s", provider, e)
    return results


async def _stage_rerank(query: str, results: List[Dict[str, Any]], c: Dict[str, Any]) -> List[Dict[str, Any]]:
    provider = str(c.get("rerankerProvider") or "none")
    if provider == "none" or len(results) < 2:
        return results
    docs = [f"{r.get('title', '')}\n{r.get('content', '')}"[:2000] for r in results]
    try:
        if provider == "jina":
            key = str(c.get("jinaApiKey") or "")
            if not key:
                return results
            payload = {
                "model": "jina-reranker-v2-base-multilingual",
                "query": query,
                "documents": docs,
            }
            resp, data, audit, logical, attempt = await _paid_provider_post(
                c=c,
                provider="jina-rerank",
                model=str(payload["model"]),
                transport="rerank_api",
                url="https://api.jina.ai/v1/rerank",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                payload=payload,
                provider_api_key=key,
                purpose_detail="rerank_provider:jina",
            )
            if resp.status_code >= 400:
                await _finish_paid_provider(
                    audit, logical, attempt, data=data, http_status=resp.status_code,
                    terminal_status="failed", committed=False,
                )
                return results
            order = [r["index"] for r in (data.get("results") or []) if "index" in r]
            await _finish_paid_provider(
                audit, logical, attempt, data=data, http_status=resp.status_code,
                terminal_status="completed" if order else "incomplete",
                committed=bool(order),
            )
            if order:
                return [results[i] for i in order if 0 <= i < len(results)]
        elif provider == "cohere":
            key = str(c.get("cohereApiKey") or "")
            if not key:
                return results
            payload = {
                "model": "rerank-multilingual-v3.0",
                "query": query,
                "documents": docs,
            }
            resp, data, audit, logical, attempt = await _paid_provider_post(
                c=c,
                provider="cohere-rerank",
                model=str(payload["model"]),
                transport="rerank_api",
                url="https://api.cohere.ai/v1/rerank",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                payload=payload,
                provider_api_key=key,
                purpose_detail="rerank_provider:cohere",
            )
            if resp.status_code >= 400:
                await _finish_paid_provider(
                    audit, logical, attempt, data=data, http_status=resp.status_code,
                    terminal_status="failed", committed=False,
                )
                return results
            order = [r["index"] for r in (data.get("results") or []) if "index" in r]
            await _finish_paid_provider(
                audit, logical, attempt, data=data, http_status=resp.status_code,
                terminal_status="completed" if order else "incomplete",
                committed=bool(order),
            )
            if order:
                return [results[i] for i in order if 0 <= i < len(results)]
        elif provider == "local":
            # 自托管重排（TEI 兼容 /rerank）：POST {url}/rerank {query, texts} →
            # 返回按分数降序的 [{index, score}, ...]（TEI 直接返回数组；亦兼容 {results:[...]} 包裹）
            url = str(c.get("localRerankerUrl") or "").strip().rstrip("/")
            if not url:
                return results
            # CPU 版慢，单条截短 + 放宽超时（见常量注释）；仍超时则静默回退。
            local_texts = [d[:_RERANK_LOCAL_DOC_CHARS] for d in docs]
            async with httpx.AsyncClient(timeout=_RERANK_LOCAL_TIMEOUT, trust_env=_provider_trust_env(url)) as client:
                resp = await client.post(
                    f"{url}/rerank",
                    json={"query": query, "texts": local_texts},
                )
            data = resp.json()
            rows = data.get("results") if isinstance(data, dict) else data
            order = [r["index"] for r in (rows or []) if isinstance(r, dict) and "index" in r]
            if order:
                return [results[i] for i in order if 0 <= i < len(results)]
        elif provider == "platform":
            # 平台级重排模型（管理台统一配置，与知识库检索共用）。HTTP 调用、密钥与用量记账
            # 都收在 rerank_service 里，这里不再套 _paid_provider_post——那套审计要拿到线上
            # 报文和响应，隔着服务层记只会得到一条"不知道有没有扣费"的空壳记录。
            # 延迟导入：重排是可选段，服务模块缺失（分支未合并、依赖缺失）应只让这一段降级，
            # 不能让整个联网搜索模块 import 失败。
            try:
                from app.services.knowledge import rerank_service
            except ImportError as e:
                logger.warning("联网搜索[重排段/platform]：重排服务模块缺失，跳过重排: %s", e)
                return results
            # 送入内容与 local 分支同样截到标题+开头（见 _RERANK_LOCAL_DOC_CHARS 注释）：
            # 相关性信号就在前几百字，多送只是多付 token、多等。
            texts = [d[:_RERANK_LOCAL_DOC_CHARS] for d in docs]
            try:
                ranked = await rerank_service.rerank(query, texts, top_n=len(texts))
            except rerank_service.RerankUnavailable:
                # 未配置/未启用不是故障：按 provider=none 处理，但留一条 info，
                # 否则管理员选了「平台重排模型」却看不出它从未生效。
                logger.info("平台重排模型未配置，联网搜索跳过重排")
                return results
            except rerank_service.RerankError as e:
                logger.warning("联网搜索[重排段/platform]失败，退回未重排顺序: %s", e)
                return results
            # 与其它分支一致：重排段只回收顺序，分数不落到结果上（调用处只读 _i）。
            order = [i for i, _score in ranked]
            if order:
                return [results[i] for i in order if 0 <= i < len(results)]
    except Exception as e:  # noqa: BLE001
        logger.warning("联网搜索[重排段/%s]失败: %s", provider, e)
    return results


# 单次搜索回收的图片条数上限（多次搜索由调用方 image_sink 再做全消息上限）
_IMAGE_TOPK = 6


def _image_engines_from(c: Dict[str, Any]) -> str:
    """从通用搜索引擎配置推导图片引擎（searxng 引擎名约定 `<engine> images`）。

    显式配置 searxngImageEngines 优先；否则把 searxngEngines 逐个映射成图片变体
    （如 quark→quark images，实测国内实例 quark/360search 图片引擎可用且相关性好）；
    两者都空则不传 engines（用实例默认的 images 类目引擎）。
    """
    explicit = str(c.get("searxngImageEngines") or "").strip()
    if explicit:
        return explicit
    base = str(c.get("searxngEngines") or "").strip()
    if not base:
        return ""
    return ",".join(f"{e.strip()} images" for e in base.split(",") if e.strip())


def _clean_image(r: Dict[str, Any]) -> Dict[str, Any] | None:
    """标准化一条图片结果：{url, thumbnail, title, source}；非 http(s) 直链丢弃。"""
    url = str(r.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return None
    thumb = str(r.get("thumbnail") or "").strip()
    return {
        "url": url[:500],
        "thumbnail": thumb[:500] if thumb.lower().startswith(("http://", "https://")) else "",
        "title": str(r.get("title") or "")[:120],
        "source": str(r.get("source") or "")[:500],
    }


async def _stage_image_search(query: str, c: Dict[str, Any]) -> List[Dict[str, Any]]:
    """图片搜索段（ChatGPT 式图文混排的素材源）：与文本管线并行跑，失败/为空静默降级。

    仅回收元数据（图片由用户浏览器直连加载，无服务端抓取，SSRF 面不变）；
    直链非 http(s) 的丢弃，按直链去重。
    """
    provider = str(c.get("searchProvider") or "searxng")
    raw: List[Dict[str, Any]] = []
    try:
        if provider == "searxng":
            base = str(c.get("searxngUrl") or "").strip().rstrip("/")
            if not base:
                return []
            headers = {}
            if c.get("searxngApiKey"):
                headers["Authorization"] = f"Bearer {c['searxngApiKey']}"
            params: Dict[str, Any] = {"q": query, "format": "json", "categories": "images"}
            engines = _image_engines_from(c)
            if engines:
                params["engines"] = engines
            async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=_provider_trust_env(base)) as client:
                resp = await client.get(f"{base}/search", params=params, headers=headers)
            data = resp.json()
            # 指定 engines 时个别实例仍会混入类目默认引擎（stock/艺术图库，相关性差）——
            # 按 engine 字段过滤只留点名引擎的结果
            wanted = {e.strip() for e in engines.split(",") if e.strip()} if engines else None
            for r in data.get("results") or []:
                if wanted and str(r.get("engine") or "") not in wanted:
                    continue
                raw.append({
                    "url": r.get("img_src"),
                    "thumbnail": r.get("thumbnail_src"),
                    "title": r.get("title"),
                    "source": r.get("url"),
                })
        elif provider == "serper":
            key = str(c.get("serperApiKey") or "")
            if not key:
                return []
            payload = {"q": query, "num": _IMAGE_TOPK}
            resp, data, audit, logical, attempt = await _paid_provider_post(
                c=c,
                provider="serper-images",
                model="serper-images",
                transport="image_search_api",
                url="https://google.serper.dev/images",
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
                payload=payload,
                provider_api_key=key,
                purpose_detail="image_search_provider:serper",
            )
            if resp.status_code >= 400:
                await _finish_paid_provider(
                    audit, logical, attempt, data=data, http_status=resp.status_code,
                    terminal_status="failed", committed=False,
                )
                return []
            for r in data.get("images") or []:
                raw.append({
                    "url": r.get("imageUrl"),
                    "thumbnail": r.get("thumbnailUrl"),
                    "title": r.get("title"),
                    "source": r.get("link"),
                })
            await _finish_paid_provider(
                audit, logical, attempt, data=data, http_status=resp.status_code,
                terminal_status="completed" if raw else "incomplete",
                committed=bool(raw),
            )
        elif provider == "tavily":
            key = str(c.get("tavilyApiKey") or "")
            if not key:
                return []
            payload = {
                "api_key": key,
                "query": query,
                "max_results": 3,
                "include_images": True,
                "include_image_descriptions": True,
            }
            resp, data, audit, logical, attempt = await _paid_provider_post(
                c=c,
                provider="tavily-images",
                model="tavily-images",
                transport="image_search_api",
                url="https://api.tavily.com/search",
                headers=None,
                payload=payload,
                provider_api_key=key,
                purpose_detail="image_search_provider:tavily",
            )
            if resp.status_code >= 400:
                await _finish_paid_provider(
                    audit, logical, attempt, data=data, http_status=resp.status_code,
                    terminal_status="failed", committed=False,
                )
                return []
            for r in data.get("images") or []:
                if isinstance(r, str):
                    raw.append({"url": r})
                elif isinstance(r, dict):
                    raw.append({"url": r.get("url"), "title": r.get("description")})
            await _finish_paid_provider(
                audit, logical, attempt, data=data, http_status=resp.status_code,
                terminal_status="completed" if raw else "incomplete",
                committed=bool(raw),
            )
    except Exception as e:  # noqa: BLE001
        logger.info("联网搜索[图片段/%s]失败（忽略）: %s", provider, e)
        return []
    images: List[Dict[str, Any]] = []
    seen: set = set()
    for r in raw:
        img = _clean_image(r)
        if not img or img["url"] in seen:
            continue
        seen.add(img["url"])
        images.append(img)
        if len(images) >= _IMAGE_TOPK:
            break
    return images


def _format_for_model(
    results: List[Dict[str, Any]],
    start_index: int = 1,
    content_limit: int = _CONTENT_LIMIT,
) -> str:
    """结果编号从 start_index 起（同轮多次搜索全局递增，仅供模型对照材料）。

    编号留在检索回执里即可；用户可见终答由 turn_finalizer.scrub_inline_source_markers
    硬剥 [1][2]，来源走 citations /「参考了 N 个来源」入口。
    """
    if not results:
        return "（联网搜索未返回结果）"
    limit = max(400, int(content_limit or _CONTENT_LIMIT))
    lines: List[str] = []
    for i, r in enumerate(results, start_index):
        content = str(r.get("content") or "")[:limit]
        # 标题旁标注发布时间（有则带）：模型据此结合系统提示的当前日期辨别结果新旧
        pub = str(r.get("publishedDate") or "").strip()
        title = f"{r.get('title', '')}（发布于 {pub}）" if pub else str(r.get("title", ""))
        quality = "已读取网页正文" if r.get("scraped") else "仅搜索摘要，尚未核验全文"
        lines.append(f"[{i}] {title}\n材料状态: {quality}\n{content}\n来源: {r.get('url', '')}")
    return "\n\n".join(lines)


async def is_enabled() -> bool:
    c = await cfg.get_web_search_config()
    return bool(c.get("enabled"))


async def search_web(query: str, start_index: int = 1, with_images: bool = False,
                     progress_cb=None, image_query: Optional[str] = None,
                     caller_user_id: str = "", caller_run_id: str = "",
                     caller_thread_id: str = "", caller_root_run_id: str = "",
                     caller_tool_call_id: str = "",
                     caller_parent_logical_call_id: str = "",
                     caller_execution_segment: str = "",
                     caller_newapi_key: str = "",
                     research_depth: bool = False,
                     research_page_limit: int = 8,
                     research_source_recovery: bool = False,
                     request_scope=None,
                     allowed_domains: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """跑完整三段式管线。返回 {enabled, results, text, scraped_pages, images, error}。

    start_index：结果编号起点——同一轮对话多次搜索时由调用方递增，保证模型看到的
    引用编号全局唯一（配合正文行内 [编号] 引用角标，编号能反查到正确来源）。
    scraped_pages：实际抓到正文的页面 [{title,url}]（思考时间线「浏览 N 个页面」行）。
    with_images：并行加一路图片搜索（图文混排素材），与抓取/重排同批 gather，不增加墙钟。
    image_query：图片段专用 query；缺省回退到正文 query。多意图场景（天气+附图）
    应把配图主体只放这里，避免污染正文检索。
    progress_cb：可选的逐源阅读回调（async, 接收抓取项 {title,url,...}），
    抓取段每开始读一页调用一次，供执行流展示「正在阅读 X」；异常被吞不影响管线。
    error：**搜索后端没跑通**时的原因（空串=正常）。调用方必须据此把「后端坏了」
    和「确实没有结果」讲成两件事，见 _stage_search 的说明。
    """
    c = await cfg.get_web_search_config()
    if not c.get("enabled"):
        # 未启用同样不是"没搜到"：带上 error，调用方才能对用户说清是功能没开
        return {"enabled": False, "results": [], "text": "", "scraped_pages": [], "images": [],
                "error": "联网搜索未启用（管理端 agent_platform_config.web_search）"}
    if not query or not query.strip():
        return {"enabled": True, "results": [], "text": "（未提供搜索关键词）",
                "scraped_pages": [], "images": [], "error": ""}
    # DeepSeek 搜索严格使用主对话已经解析出的用户专属 NewAPI Key。
    # Key 不进平台配置、不另建凭据表，只在本次后端调用的内存中存在。
    c["deepseekApiKey"] = str(caller_newapi_key or "")
    c["_researchDepth"] = bool(research_depth)
    c["_researchSourceRecovery"] = bool(research_depth and research_source_recovery)
    c["_callerUserId"] = str(caller_user_id or "")
    c["_callerRunId"] = str(caller_run_id or "")
    c["_callerThreadId"] = str(caller_thread_id or "")
    c["_callerRootRunId"] = str(caller_root_run_id or "")
    c["_callerToolCallId"] = str(caller_tool_call_id or "")
    c["_callerParentLogicalCallId"] = str(caller_parent_logical_call_id or "")
    c["_callerExecutionSegment"] = str(caller_execution_segment or "")
    c["_requestScope"] = request_scope
    official_only = allowed_domains is not None
    c["_requireRichContent"] = official_only
    if official_only and not allowed_domains:
        # An empty campus allowlist is a configuration error, not permission to fall back to the
        # ordinary web.  Fail before the provider call so neither text nor image search can pull
        # third-party candidates that later layers might accidentally surface.
        return {
            "enabled": True,
            "results": [],
            "scraped_pages": [],
            "images": [],
            "error": "",
            "official_domain_no_hit": True,
            "text": (
                "（学校官网域名白名单尚未配置，本轮未执行网页或图片搜索。"
                "不要改用第三方页面作为学校官方依据。）"
            ),
        }
    provider_query = query
    if official_only:
        from app.services.chat.builtin_assistants.campus_services.domain_policy import scope_query_to_official_domains
        provider_query = scope_query_to_official_domains(query, allowed_domains)
    if research_depth:
        results, search_error = await _stage_research_search(provider_query, c)
    else:
        results, search_error = await _stage_search(provider_query, c)
    if search_error and not results:
        # 快速失败：搜索段一没跑通，后面抓取/重排/配图都无米下锅，白等一轮超时
        logger.warning("联网搜索[搜索段]不可用，据实上报而不是回空结果: %s", search_error)
        return {
            "enabled": True, "results": [], "scraped_pages": [], "images": [],
            "error": search_error,
            "error_code": failure_code(search_error),
            "search_meta": {
                "mode": str(c.get("_searchMode") or "primary_fallback"),
                "providers": list(c.get("_searchProvidersUsed") or []),
                "attempted": list(c.get("_searchProvidersAttempted") or []),
                "failures": dict(c.get("_searchProviderFailures") or {}),
            },
            "text": (
                f"（联网搜索失败：搜索后端不可用 —— {search_error}。"
                "这**不是**「没有搜到相关信息」，不要据此对用户下结论。）"
            ),
        }
    # SSRF：丢弃指向私网/元数据的结果 URL（防被搜索结果诱导抓取内网）
    results = await _filter_public_or_no_url(results)
    if official_only:
        from app.services.chat.builtin_assistants.campus_services.domain_policy import filter_results_by_official_domains
        before = list(results)
        results = filter_results_by_official_domains(results, allowed_domains)
        if before and not results:
            return {
                "enabled": True, "results": [], "scraped_pages": [], "images": [],
                "error": "",
                "official_domain_no_hit": True,
                "text": (
                    "（官方域名过滤后没有可用结果：搜索后端返回了候选，"
                    "但都不在学校官方域名白名单内。这不是搜索故障，"
                    "不要把非官方页面当作学校依据。）"
                ),
            }
    # v2.41：先按摘要可核验度 + 主题/时效轻量预排，再决定是否抓取。
    # 黄历/入口壳常因标题命中排到前面；真正带单位数值的摘要必须先浮上来。
    date_tokens = _query_date_tokens(query)
    topic_tokens = _topic_tokens(query, date_tokens)
    if results:
        indexed = list(enumerate(results))
        indexed.sort(
            key=lambda pair: (
                _freshness_boost_key(pair[1], date_tokens, topic_tokens),
                -pair[0],
            ),
            reverse=True,
        )
        results = [r for _, r in indexed]

    # Campus official-only search must open the official page even when the snippet already has
    # enough text: page Markdown is also the trusted source of official inline images.
    skip_scrape = False if research_depth or official_only else _snippets_already_concrete(results, min_hits=1)
    # 时效问句：摘要虽有数值但可能是旧闻/无日期预报壳；若尚无「非过期 concrete」，
    # 强制抓取 top 页补正文，避免 skip 后把上周数据当今日实况锁死预算。
    if skip_scrape:
        try:
            from app.services.chat.tools.web_freshness import (
                has_fresh_enough_concrete,
                query_wants_fresh,
            )
            if query_wants_fresh(query) and not has_fresh_enough_concrete(results):
                skip_scrape = False
                logger.info(
                    "联网搜索：时效问句且无新鲜可核验摘要，强制抓取段（query=%s）",
                    (query or "")[:80],
                )
        except Exception:  # noqa: BLE001
            pass
    if skip_scrape:
        logger.info(
            "联网搜索：摘要已有可核验细节，跳过抓取段以省墙钟（query=%s）",
            (query or "")[:80],
        )

    # 抓取与重排并行：重排改用搜索摘要快照（相关性信号集中在标题+开头，本就截 500 字，
    # 见 _RERANK_LOCAL_DOC_CHARS），不等抓取正文；整段耗时从 抓取+重排 相加变为取 max，
    # 模型更早拿到完整工具结果开始吐字。快照带 _i 序号，重排只回收顺序、内容仍用抓取后的。
    if research_depth:
        # A blocked site's many snippets must not consume every reading slot.
        # Keep relevance order within the first page from each distinct host.
        first, repeated, seen_hosts = [], [], set()
        for item in results:
            host = (urlparse(str(item.get("url") or "")).hostname or "").lower().removeprefix("www.")
            if host and host not in seen_hosts:
                seen_hosts.add(host)
                first.append(item)
            else:
                repeated.append(item)
        results = first + repeated
    snap = [{**r, "_i": i} for i, r in enumerate(results)]
    scrape_cap = max(1, min(int(research_page_limit), 8)) if research_depth else _MAX_SCRAPE
    content_limit = 4000 if research_depth else _CONTENT_LIMIT

    async def _images_or_empty() -> List[Dict[str, Any]]:
        if not with_images:
            return []
        iq = str(image_query or query or "").strip() or str(query or "").strip()
        if official_only:
            from app.services.chat.builtin_assistants.campus_services.domain_policy import scope_query_to_official_domains
            iq = scope_query_to_official_domains(iq, allowed_domains)
        return await _stage_image_search(iq, c)

    async def _scrape_or_keep() -> List[Dict[str, Any]]:
        if skip_scrape:
            return results
        return await _stage_scrape(
            results, c, progress_cb=progress_cb, max_scrape=scrape_cap,
        )

    results, ordered, images = await asyncio.gather(
        _scrape_or_keep(),
        _stage_rerank(query, snap, c),
        _images_or_empty(),
    )
    order = [s["_i"] for s in ordered if isinstance(s.get("_i"), int)]
    if order:
        results = [results[i] for i in order if 0 <= i < len(results)]
    # 抓取后可能补强了正文：再按 concrete+topic+date 收口，截 topK。
    if results:
        indexed = list(enumerate(results))
        indexed.sort(
            key=lambda pair: (
                bool(research_depth and pair[1].get("scraped")),
                _freshness_boost_key(pair[1], date_tokens, topic_tokens),
                -pair[0],
            ),
            reverse=True,
        )
        results = [r for _, r in indexed]
    top_k = int(c.get("topK") or 5)
    if research_depth:
        top_k = max(top_k, 8)
    results = results[:top_k]
    scraped_pages = [
        {"title": str(r.get("title") or r.get("url") or "网页"), "url": str(r.get("url") or "")}
        for r in results
        if r.get("scraped") and r.get("url")
    ]
    return {
        "enabled": True,
        "results": results,
        "text": _format_for_model(results, start_index, content_limit=content_limit),
        "scraped_pages": scraped_pages,
        "images": images,
        "search_meta": {
            "mode": str(c.get("_searchMode") or "primary_fallback"),
            "providers": list(c.get("_searchProvidersUsed") or []),
            "attempted": list(c.get("_searchProvidersAttempted") or []),
            "failures": dict(c.get("_searchProviderFailures") or {}),
            "fullTextSources": sum(1 for row in results if row.get("scraped")),
            "snippetOnlySources": sum(1 for row in results if not row.get("scraped")),
        },
        # 走到这里说明搜索段成功（有结果）；抓取/重排失败是可降级的，不算搜索失败
        "error": "",
    }


def research_excerpt(body: str, focus: str = "", *, limit: int = 4000) -> str:
    """Locate exact question keywords in fetched text, never invent a summary."""
    if not focus.strip():
        return body[:limit]
    terms = [term for term in re.split(r"[\s,，;；]+", focus.strip()) if len(term) >= 3][:8]
    lowered = body.lower()
    anchors = set()
    for term in terms[:4]:
        pattern = re.escape(term.lower()).replace("_", r"(?:\\)?_")
        matches = [m.start() for m in re.finditer(pattern, lowered)]
        if matches:
            anchors.update((matches[len(matches) // 2], matches[-1]))
    if not anchors:
        return body[:limit]
    # Include later occurrences: the first can be a table-of-contents entry.
    width = max(200, limit // len(anchors) - 20)
    ranges = []
    for anchor in sorted(anchors):
        start, end = max(0, anchor - width // 3), min(len(body), anchor + width * 2 // 3)
        if ranges and start <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
        else:
            ranges.append((start, end))
    return "\n\n[原文片段]\n".join(body[start:end] for start, end in ranges)[:limit]


async def scrape_url(url: str, progress_cb=None, *, focus: str = "", request_scope=None,
                     caller_user_id: str = "", caller_run_id: str = "", caller_thread_id: str = "",
                     caller_root_run_id: str = "", caller_tool_call_id: str = "") -> Dict[str, Any]:
    """Force-read one public URL for Deep Research. SSRF filter is mandatory."""
    target = str(url or "").strip()
    if not target:
        return {"ok": False, "url": "", "title": "", "text": "（未提供网址）", "error": "empty_url"}
    items = await _filter_by_public_url([{"url": target, "title": target, "content": ""}])
    if not items:
        return {
            "ok": False, "url": target, "title": "",
            "text": "（该网址未通过公开地址预检，已拒绝抓取）",
            "error": "url_not_public",
        }
    c = await cfg.get_web_search_config()
    c.update(_requestScope=request_scope, _callerUserId=caller_user_id, _callerRunId=caller_run_id,
             _callerThreadId=caller_thread_id, _callerRootRunId=caller_root_run_id,
             _callerToolCallId=caller_tool_call_id)
    filled = await _stage_scrape(items, c, progress_cb=progress_cb, max_scrape=1)
    row = filled[0] if filled else items[0]
    text = research_excerpt(str(row.get("content") or ""), focus)
    title = str(row.get("title") or target)
    if not text:
        return {
            "ok": False, "url": target, "title": title,
            "text": "（未能抓取到可用正文）", "error": row.get("read_error") or "empty_body",
            "error_code": failure_code(str(row.get("read_error") or "empty_body")),
        }
    return {
        "ok": True, "url": str(row.get("url") or target), "title": title,
        "text": text, "scraped": bool(row.get("scraped")), "error": "",
    }
