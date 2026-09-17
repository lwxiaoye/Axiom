"""Campus official-domain allowlist: normalize, validate, and filter URLs.

These helpers are pure functions.  They do not fetch pages and must not use
substring host matching.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Iterable
from urllib.parse import urlparse


PUBLIC_SUFFIX_ONLY = frozenset({
    "com", "net", "org", "edu", "gov", "cn", "edu.cn", "gov.cn", "com.cn",
    "net.cn", "org.cn", "ac.cn", "mil.cn", "co", "io", "info", "xyz",
})

LOCAL_HOSTS = frozenset({"localhost", "localhost.localdomain"})


class DomainPolicyError(ValueError):
    """Raised when an administrator-supplied host is not a usable official domain."""


def _idna_host(raw: str) -> str:
    text = str(raw or "").strip().rstrip(".").lower()
    if not text:
        raise DomainPolicyError("域名为空")
    try:
        return text.encode("idna").decode("ascii")
    except Exception as exc:  # noqa: BLE001
        raise DomainPolicyError("域名无法规范化") from exc


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def normalize_official_domain(item: Any) -> dict[str, Any]:
    """Normalize one admin domain rule to `{host, include_subdomains}`."""
    if isinstance(item, str):
        host_raw, include_subdomains = item, True
    elif isinstance(item, dict):
        host_raw = item.get("host") or item.get("domain") or item.get("value") or ""
        flag = item.get("include_subdomains")
        include_subdomains = True if flag is None else bool(flag)
    else:
        raise DomainPolicyError("域名规则格式无效")

    text = str(host_raw or "").strip()
    if not text:
        raise DomainPolicyError("域名为空")
    if any(ch in text for ch in ("/", ":", "@", "?", "#", " ")):
        raise DomainPolicyError("只接受主机名，不能包含协议、路径、端口或凭据")
    host = _idna_host(text)
    if _is_ip_literal(host) or host in LOCAL_HOSTS:
        raise DomainPolicyError("不能使用 IP、localhost 或保留地址")
    labels = [part for part in host.split(".") if part]
    if len(labels) < 2:
        raise DomainPolicyError("域名缺少有效后缀")
    if host in PUBLIC_SUFFIX_ONLY:
        raise DomainPolicyError("不能只配置公共后缀")
    return {"host": host, "include_subdomains": bool(include_subdomains)}


def normalize_official_domains(items: Iterable[Any] | None) -> list[dict[str, Any]]:
    seen: set[tuple[str, bool]] = set()
    out: list[dict[str, Any]] = []
    for item in items or []:
        rule = normalize_official_domain(item)
        key = (rule["host"], bool(rule["include_subdomains"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(rule)
    return out


def host_allowed(host: str, rules: Iterable[dict[str, Any]] | None) -> bool:
    try:
        candidate = _idna_host(host)
    except DomainPolicyError:
        return False
    if _is_ip_literal(candidate) or candidate in LOCAL_HOSTS:
        return False
    for rule in rules or []:
        allowed = str(rule.get("host") or "").strip().lower().rstrip(".")
        if not allowed:
            continue
        try:
            allowed = _idna_host(allowed)
        except DomainPolicyError:
            continue
        if candidate == allowed:
            return True
        if rule.get("include_subdomains") and candidate.endswith("." + allowed):
            return True
    return False


def url_allowed(url: str, rules: Iterable[dict[str, Any]] | None) -> bool:
    raw = str(url or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.hostname or ""
    return host_allowed(host, rules)


def scope_query_to_official_domains(
    query: str,
    rules: Iterable[dict[str, Any]] | None,
) -> str:
    """Bias upstream retrieval to the allowlist; post-filtering remains authoritative."""
    hosts: list[str] = []
    seen: set[str] = set()
    for rule in rules or []:
        try:
            host = _idna_host(str(rule.get("host") or ""))
        except (AttributeError, DomainPolicyError):
            continue
        if host in seen or _is_ip_literal(host) or host in LOCAL_HOSTS:
            continue
        seen.add(host)
        hosts.append(host)
    raw = str(query or "").strip()
    if not hosts:
        return raw
    scope = " OR ".join(f"site:{host}" for host in hosts)
    return f"{raw} ({scope})".strip() if len(hosts) > 1 else f"{raw} {scope}".strip()


def filter_results_by_official_domains(
    results: list[dict[str, Any]] | None,
    rules: Iterable[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for item in results or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if url_allowed(url, rules):
            kept.append(item)
    return kept
