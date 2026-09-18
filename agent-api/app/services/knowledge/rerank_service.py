"""重排模型：平台级配置（密文入库）+ 调用（通用 /rerank 与 DashScope 原生端点）。

知识库检索原本只有向量粗召回。管理员拿到了 qwen3.7-text-rerank，但系统里没有任何
重排的配置路径——没有存储、没有管理页、检索也没接。这个模块同时承担「存」和「用」：
知识库检索与联网搜索都调这里的 rerank()。

配置照 platform/model_connection.py 的模式进 agent_platform_config（key 固定
"rerank_model"，平台级、不按用户），api_key 经 Fernet 密文入库——不学 ai_embedding_model
把密钥明文落库。

端点探测结论（2026-09-18，用线上 DashScope key 实测）：
- compatible-mode `POST {base}/rerank`（Jina/Cohere 风格 {model, query, documents, top_n}）
  → HTTP 404、空响应体：DashScope 的 OpenAI 兼容层不提供重排；
- 原生 `POST https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank`，
  body {model, input:{query, documents}, parameters:{top_n, return_documents:false}}
  → 200，output.results[].{index, relevance_score}，已按分数降序。
所以通用 /rerank 优先（别的 OpenAI/Jina/TEI 兼容服务直接可用），DashScope 域名下通用路径
404/405 时改走原生端点，并记住这个 base_url 之后直接走原生，不再每次白打一趟 404。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.services.connectors.crypto import ConnectorCryptoError, decrypt_secret, encrypt_secret
from app.services.platform.platform_config_service import _get_raw, _save_raw

logger = logging.getLogger(__name__)

CONFIG_KEY = "rerank_model"
DEFAULTS = {"base_url": "", "model": "", "api_key_cipher": "", "enabled": False}

_DASHSCOPE_HOSTS = ("dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com")
_DASHSCOPE_NATIVE_PATH = "/api/v1/services/rerank/text-rerank/text-rerank"
# 已实测不支持通用 /rerank、只能走原生端点的 base_url。进程内记忆即可：
# 换地址就是另一个 key，重启后重新探测一次的代价也只是一个 404。
_native_only: set[str] = set()


class RerankUnavailable(ValueError):
    """未配置或未启用重排模型。"""


class RerankError(RuntimeError):
    """网络、HTTP 状态或响应格式错误。绝不吞成空列表——调用方自己决定是否退化并留痕。"""


@dataclass
class RerankConfig:
    base_url: str
    model: str
    api_key: str
    enabled: bool


# ---- 配置存取 ----

class RerankInput(BaseModel):
    base_url: str = Field(max_length=2048)
    model: str = Field(max_length=255)
    api_key: str = Field(default="", max_length=8192)
    enabled: bool = True


def normalize(body: RerankInput) -> dict:
    base = body.base_url.strip().rstrip("/")
    # 管理员常把完整端点贴进来。只去通用后缀；DashScope 原生完整地址保留原样，_native_url 认得它。
    if base.endswith("/rerank"):
        base = base[: -len("/rerank")]
    try:
        url = urlsplit(base)
        valid = url.scheme in {"http", "https"} and url.hostname and url.port != 0
    except ValueError:
        valid = False
    if not valid or url.username or url.password or url.query or url.fragment or any(c.isspace() for c in base):
        raise HTTPException(422, "请输入完整的 HTTP(S) API 地址，不含账号、查询参数或片段")
    model = body.model.strip()
    if not model:
        raise HTTPException(422, "请填写模型名称")
    if any(ord(c) < 32 or ord(c) == 127 for c in body.api_key):
        raise HTTPException(422, "API Key 不能包含换行或控制字符")
    return {"base_url": base, "model": model, "enabled": body.enabled}


async def read() -> dict:
    return await _get_raw(CONFIG_KEY, DEFAULTS)


def public(data: dict) -> dict:
    return {k: data[k] for k in ("base_url", "model", "enabled")} | {"has_api_key": bool(data["api_key_cipher"])}


async def prepare(body: RerankInput) -> dict:
    data = normalize(body)
    previous = await read()
    key = body.api_key.strip()
    # 换了地址却沿用旧密钥，等于把密钥悄悄发给一台新主机——必须重填。
    if not key and previous["api_key_cipher"] and data["base_url"] != previous["base_url"]:
        raise HTTPException(422, "修改请求地址后，请重新输入 API Key")
    data["api_key_cipher"] = encrypt_secret(key) if key else previous["api_key_cipher"]
    if not data["api_key_cipher"]:
        raise HTTPException(422, "请填写 API Key")
    return data


async def save(body: RerankInput) -> dict:
    data = await prepare(body)
    await _save_raw(CONFIG_KEY, data)
    return public(data)


def to_config(data: dict) -> RerankConfig:
    """存储行（密文）-> 运行时配置（明文，只活在进程内）。"""
    return RerankConfig(
        base_url=data["base_url"],
        model=data["model"],
        api_key=decrypt_secret(data["api_key_cipher"]),
        enabled=bool(data["enabled"]),
    )


async def get_active_rerank_config() -> RerankConfig | None:
    """None = 未配置或未启用。"""
    data = await read()
    if not data["enabled"] or not data["api_key_cipher"] or not data["base_url"] or not data["model"]:
        return None
    try:
        return to_config(data)
    except ConnectorCryptoError as exc:
        # 换过加密密钥：配置形同不存在。但要说出来，否则管理页显示「已配置」检索却不重排。
        logger.warning("重排模型密钥无法解密，视为未配置，请在管理配置中重新填写：%s", exc)
        return None


# ---- 调用 ----

def _is_dashscope(base_url: str) -> bool:
    host = (urlsplit(base_url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _DASHSCOPE_HOSTS)


def _native_url(base_url: str) -> Optional[str]:
    """DashScope 原生端点；非 DashScope 地址返回 None。"""
    if base_url.endswith(_DASHSCOPE_NATIVE_PATH):
        return base_url
    if not _is_dashscope(base_url):
        return None
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}{_DASHSCOPE_NATIVE_PATH}"


def _status_hint(status: int) -> str:
    return {
        401: "API Key 无效",
        403: "没有模型访问权限",
        404: "请检查 API 地址和模型名称",
        429: "请求限流或额度不足",
    }.get(status, "重排服务返回错误")


def _body_excerpt(response: httpx.Response) -> str:
    # 服务商的错误体（如 DashScope 的 code/message）比状态码更能说明问题；响应体里不会有我们的 key。
    text = " ".join(response.text.split())[:160]
    return f"：{text}" if text else ""


def _parse_results(payload: Any, count: int) -> list[tuple[int, float]]:
    """兼容三种外形：通用 {results:[...]}、DashScope {output:{results:[...]}}、Voyage {data:[...]}；
    TEI 直接返回数组。条目字段 index + relevance_score（或 score）。"""
    rows: Any = None
    if isinstance(payload, dict):
        if isinstance(payload.get("output"), dict):
            rows = payload["output"].get("results")
        if rows is None:
            rows = payload.get("results", payload.get("data"))
    elif isinstance(payload, list):
        rows = payload
    if not isinstance(rows, list):
        raise RerankError("重排响应缺少 results 列表")
    scored: list[tuple[int, float]] = []
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RerankError("重排响应条目不是对象")
        index = row.get("index")
        score = row.get("relevance_score", row.get("score"))
        if isinstance(index, bool) or not isinstance(index, int) or not (0 <= index < count):
            raise RerankError(f"重排响应 index 越界或缺失：{index!r}")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise RerankError(f"重排响应缺少分数（index={index}）")
        if index in seen:
            continue
        seen.add(index)
        scored.append((index, float(score)))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


AUDIT_TRANSPORT = "rerank_api"
DEFAULT_AUDIT_PURPOSE_DETAIL = "rerank_provider:platform"


async def _begin_audit(config: RerankConfig, purpose_detail: str):
    """用量记账只能在发请求的地方做——联网搜索隔着服务层拿不到原始请求与响应 usage。

    Run 上下文照 embedding_service 的做法从当前工具上下文取；拿不到（前置检索、管理页
    测试）就不记。model_usage_audit 内部一律 fail-open，记账失败不会拖垮检索。
    """
    from app.services.agent_harness import model_usage_audit as audit

    run_id = thread_id = parent_tool_call_id = ""
    try:
        from app.services.chat.tools.base import current_tool_context

        context = current_tool_context()
        if context is not None:
            run_id = str(context.run_id or "")
            thread_id = str(context.thread_id or "")
            parent_tool_call_id = str(context.call_id or "")
    except Exception:  # noqa: BLE001
        pass
    logical = None
    if run_id:
        logical = await audit.begin_logical_call(
            run_id=run_id,
            thread_id=thread_id,
            parent_tool_call_id=parent_tool_call_id,
            model=config.model,
            transport=AUDIT_TRANSPORT,
            endpoint_family="rerank",
            purpose="tool_internal",
            purpose_detail=purpose_detail,
            scope_key=f"tool_internal:rerank:{purpose_detail}"[:255],
            provider_api_key=config.api_key,
        )
    return audit, logical


def _response_id(audit, payload: Any) -> str:
    # DashScope 原生响应的标识叫 request_id，不叫 id
    request_id = payload.get("request_id") if isinstance(payload, dict) else ""
    return audit.provider_response_id(payload) or str(request_id or "")[:128]


async def _close_audit(audit, logical, attempt, *, status: str, payload: Any, http_status: Optional[int], committed: bool) -> None:
    await audit.finish_attempt(
        attempt,
        terminal_status=status,
        usage=audit.provider_usage_from_response(payload),
        response_id=_response_id(audit, payload),
        provider_event_seen=http_status is not None,
        terminal_seen=http_status is not None,
        http_status=http_status,
        committed=committed,
    )
    await audit.finish_logical_call(
        logical,
        terminal_status=status,
        selected_attempt_id=(attempt.attempt_id if attempt and committed else ""),
        committed=committed,
    )


async def rerank(
    query: str,
    documents: list[str],
    *,
    top_n: int | None = None,
    config: RerankConfig | None = None,
    timeout: float = 20.0,
    audit_purpose_detail: str | None = None,
) -> list[tuple[int, float]]:
    """返回 (原始下标, 相关度分数)，按分数降序。documents 为空返回 []。

    config 为 None 时取 active；未配置时 raise RerankUnavailable；显式传入的 config 不看
    enabled（管理页「测试连接」要在开关关着时也能测）。网络/HTTP/格式错误 raise RerankError，
    绝不吞成空列表——「用不了却无报错」是这个项目过去 RAG 的头号投诉。
    audit_purpose_detail 进用量审计的 purpose_detail（知识库传 knowledge_rerank，联网搜索
    不传则记 rerank_provider:platform）。
    """
    if not documents:
        return []
    if config is None:
        config = await get_active_rerank_config()
        if config is None:
            raise RerankUnavailable("未配置或未启用重排模型")
    base = config.base_url.rstrip("/")
    docs = [str(d) for d in documents]
    n = len(docs) if top_n is None else max(1, min(int(top_n), len(docs)))
    native = _native_url(base)
    generic = None if base.endswith(_DASHSCOPE_NATIVE_PATH) or base in _native_only else base + "/rerank"
    if generic is None and native is None:  # generic 为 None 只会发生在 DashScope 场景，这里只是防御
        raise RerankError("重排服务不支持 /rerank 接口")
    headers = {"Authorization": "Bearer " + config.api_key, "Content-Type": "application/json"}
    audit, logical = await _begin_audit(config, str(audit_purpose_detail or DEFAULT_AUDIT_PURPOSE_DETAIL)[:200])
    attempt = None
    response: Optional[httpx.Response] = None
    try:
        # asyncio.timeout 是整体预算：探测通用路径失败再走原生也不能加倍等待。
        async with asyncio.timeout(timeout), httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)), follow_redirects=False, trust_env=False,
        ) as client:
            if generic:
                body = {"model": config.model, "query": query, "documents": docs, "top_n": n}
                attempt = await audit.begin_attempt(logical, wire_payload=body, attempt_kind="initial", legacy_compatible=False)
                response = await client.post(generic, headers=headers, json=body)
                if native and response.status_code in (404, 405):
                    _native_only.add(base)
                    # 探测失败的那一趟也是一次物理请求，单独记失败，不能和原生那趟混成一条
                    await audit.finish_attempt(
                        attempt, terminal_status="failed", provider_event_seen=True, terminal_seen=True,
                        http_status=response.status_code, committed=False,
                    )
                    attempt, response = None, None
            if response is None:
                body = {
                    "model": config.model,
                    "input": {"query": query, "documents": docs},
                    "parameters": {"top_n": n, "return_documents": False},
                }
                attempt = await audit.begin_attempt(
                    logical, wire_payload=body, attempt_kind="endpoint_fallback" if generic else "initial",
                    legacy_compatible=False,
                )
                response = await client.post(native, headers=headers, json=body)
    except asyncio.CancelledError:
        await audit.finish_attempt(
            attempt, terminal_status="cancelled", provider_event_seen=False, unknown_provider_charge=True, committed=False,
        )
        await audit.finish_logical_call(logical, terminal_status="cancelled", committed=False)
        raise
    except (httpx.TimeoutException, TimeoutError, httpx.HTTPError) as exc:
        await audit.finish_attempt(
            attempt, terminal_status="failed", provider_event_seen=False, error_code=type(exc).__name__, committed=False,
        )
        await audit.finish_logical_call(logical, terminal_status="failed", committed=False)
        if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
            raise RerankError(f"重排请求超时（{timeout:g} 秒）") from exc
        raise RerankError(f"重排请求失败：{type(exc).__name__} {str(exc)[:160]}") from exc

    try:
        payload = response.json()
    except ValueError:
        payload = None
    if response.status_code != 200:
        await _close_audit(audit, logical, attempt, status="failed", payload=payload, http_status=response.status_code, committed=False)
        raise RerankError(f"{_status_hint(response.status_code)}（HTTP {response.status_code}）{_body_excerpt(response)}")
    results: list[tuple[int, float]] = []
    error: Optional[RerankError] = RerankError("重排响应不是 JSON") if payload is None else None
    if error is None:
        try:
            results = _parse_results(payload, len(docs))[:n]
        except RerankError as exc:
            error = exc
    # 服务商已经计费但结果不可用，记 incomplete 而不是 completed：账要对得上
    await _close_audit(
        audit, logical, attempt, status="completed" if error is None else "incomplete",
        payload=payload, http_status=response.status_code, committed=error is None,
    )
    if error is not None:
        raise error
    return results


_PROBE_QUERY = "图书馆几点关门"
_PROBE_DOCS = [
    "图书馆开放时间：周一至周五 8:00-22:00，周末 9:00-21:00，闭馆前 15 分钟停止借阅。",
    "学生社团注册流程：填写申请表、指导老师签字、提交学生处审核。",
]


async def test_rerank(config: RerankConfig) -> dict:
    """{"success": bool, "message": str, "latency_ms": int}，给管理页「测试连接」用。"""
    started = time.monotonic()
    try:
        ranked = await rerank(_PROBE_QUERY, _PROBE_DOCS, config=config, timeout=15.0)
    except (RerankUnavailable, RerankError) as exc:
        return {"success": False, "message": str(exc), "latency_ms": round((time.monotonic() - started) * 1000)}
    latency = round((time.monotonic() - started) * 1000)
    if not ranked:
        return {"success": False, "message": "服务已响应，但没有返回任何重排结果", "latency_ms": latency}
    if ranked[0][0] != 0:
        # 能连上却把社团注册排在图书馆开放时间前面：多半是模型名填成了非重排模型
        return {"success": False, "message": "服务已响应，但排序结果不合理，请确认模型名称是重排模型", "latency_ms": latency}
    return {"success": True, "message": "连接成功，模型已响应", "latency_ms": latency}
