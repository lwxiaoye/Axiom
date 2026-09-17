"""知识库检索:核心管线 + search_knowledge 工具(结构手术 Phase 1b:自 model_driver.build_tools 原样搬迁,行为零变化)。

retrieve_knowledge(显式区分失败/无命中,防「用不了却无报错」)、
kb_search_query(短问指代折叠)、build_kb_pre_context(前置强制检索,弱模型
不发工具调用也能基于知识库回答)是主对话与工具共用的检索核心;
search_knowledge 工具供模型多轮按需补检。harness_orchestrator 经 main_agent re-export 调用。
"""
import asyncio
import hashlib
import hmac
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

import httpx
from sqlalchemy import bindparam, text

from app.core.config import settings
from app.core.database import async_session

from .base import MainTool, ToolSoftError, ToolValue, _query_param

logger = logging.getLogger(__name__)


def _internal_retrieval_canonical(timestamp: str, user_id: str, payload: Dict[str, Any]) -> bytes:
    """Match Java's length-prefixed internal-retrieval HMAC payload exactly."""
    values = [
        timestamp,
        user_id,
        payload.get("agentId"),
        payload.get("query"),
        payload.get("topK"),
        payload.get("scoreThreshold"),
        payload.get("semanticWeight"),
        payload.get("keywordWeight"),
        payload.get("retrievalMode"),
        payload.get("rerankEnabled"),
        payload.get("rerankTopN"),
        str(len(payload.get("knowledgeIds") or [])),
        *(payload.get("knowledgeIds") or []),
    ]
    parts: list[bytes] = []
    for value in values:
        if value is None:
            text_value = ""
        elif isinstance(value, bool):
            text_value = "true" if value else "false"
        else:
            text_value = str(value)
        encoded = text_value.encode("utf-8")
        parts.append(f"{len(encoded)}:".encode("utf-8") + encoded)
    return b"".join(parts)


def _internal_retrieval_headers(user_id: str, payload: Dict[str, Any]) -> Dict[str, str]:
    secret = str(settings.INTERNAL_SYNC_SECRET or "")
    if not secret or secret in {"CHANGE_ME", "change-me"}:
        raise ValueError("知识库内部检索密钥未配置")
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode("utf-8"),
        _internal_retrieval_canonical(timestamp, user_id, payload),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Internal-Timestamp": timestamp,
        "X-Internal-User-Id": user_id,
        "X-Internal-Signature": signature,
    }


def _analytics_retrieval_headers(
    user_id: str,
    payload: Dict[str, Any],
    *,
    execution_id: str,
    turn_id: str,
    source: str,
) -> Dict[str, str]:
    """Build Java-verifiable analytics attribution headers without exposing query content."""
    secret = str(settings.INTERNAL_SYNC_SECRET or "")
    if not secret or secret in {"CHANGE_ME", "change-me"}:
        raise ValueError("知识库内部检索密钥未配置")
    timestamp = str(int(time.time()))
    retrieval_signature = hmac.new(
        secret.encode("utf-8"),
        _internal_retrieval_canonical(timestamp, user_id, payload),
        hashlib.sha256,
    ).hexdigest()
    values = [timestamp, user_id, execution_id, turn_id, source, retrieval_signature]
    canonical = b"".join(
        f"{len(value.encode('utf-8'))}:".encode("utf-8") + value.encode("utf-8")
        for value in values
    )
    signature = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    return {
        "X-Knowledge-Analytics-Timestamp": timestamp,
        "X-Knowledge-Analytics-User-Id": user_id,
        "X-Knowledge-Analytics-Execution-Id": execution_id,
        "X-Knowledge-Analytics-Turn-Id": turn_id,
        "X-Knowledge-Analytics-Source": source,
        "X-Knowledge-Analytics-Signature": signature,
    }


def _strip_markdown_images(value: Any) -> str:
    """Remove Markdown image links from plain-text snippets/citations."""
    return re.sub(r"!\[[^\]\n]*\]\((?:<[^>\n]+>|[^)\n]+)\)", "", str(value or "")).strip()


_MD_IMAGE_RE = re.compile(r"!\[([^\]\n]*)\]\(\s*(?:<([^>\n]+)>|([^)\n]+))\s*\)")
_HTML_IMG_RE = re.compile(r"""<img\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.I)
_CHAT_IMAGE_SINK_LIMIT = 8


def normalize_chat_image_url(raw: str) -> str:
    """Keep http(s) and same-origin knowledge/static paths; drop data URIs."""
    url = str(raw or "").strip()
    if url.startswith("<") and url.endswith(">"):
        url = url[1:-1].strip()
    if not url or url.lower().startswith("data:"):
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("/api/") or url.startswith("/upload/"):
        return url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return ""


def iter_embedded_images(text: str) -> list[tuple[str, str]]:
    """Return unique (url, title) pairs in document order."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for match in _MD_IMAGE_RE.finditer(text or ""):
        url = normalize_chat_image_url(match.group(2) or match.group(3) or "")
        title = (match.group(1) or "").strip() or "相关图片"
        if url and url not in seen:
            seen.add(url)
            out.append((url, title))
    for match in _HTML_IMG_RE.finditer(text or ""):
        url = normalize_chat_image_url(match.group(1) or "")
        if url and url not in seen:
            seen.add(url)
            out.append((url, "相关图片"))
    return out


def attach_chat_images(
    image_sink: Optional[list],
    images: list[dict],
    *,
    source: str = "",
    display_scope: str = "chat_inline",
    limit: int = _CHAT_IMAGE_SINK_LIMIT,
) -> dict[str, int]:
    """Append unique images to the turn sink. Return url -> 1-based [图N] index."""
    mapping: dict[str, int] = {}
    if image_sink is None:
        return mapping
    existing = {
        str(item.get("url") or "").strip(): index
        for index, item in enumerate(image_sink, 1)
        if isinstance(item, dict) and item.get("url")
    }
    for image in images:
        if not isinstance(image, dict):
            continue
        url = normalize_chat_image_url(str(image.get("url") or ""))
        if not url:
            continue
        if url in existing:
            mapping[url] = existing[url]
            continue
        if len(image_sink) >= limit:
            break
        image_sink.append({
            "url": url,
            "title": str(image.get("title") or "相关图片")[:80],
            "source": str(image.get("source") or source or ""),
            "thumbnail": "",
            "display_scope": display_scope,
        })
        index = len(image_sink)
        existing[url] = index
        mapping[url] = index
    return mapping


def materialize_chat_images(
    text: str,
    image_sink: Optional[list],
    *,
    extra_urls: Optional[list] = None,
    source: str = "",
    limit: int = _CHAT_IMAGE_SINK_LIMIT,
) -> str:
    """Move embedded images into the [图N] catalog and rewrite the model-visible text."""
    images = [
        {"url": url, "title": title, "source": source}
        for url, title in iter_embedded_images(text)
    ]
    for raw in extra_urls or []:
        url = normalize_chat_image_url(str(raw or ""))
        if url:
            images.append({"url": url, "title": "相关图片", "source": source})
    mapping = attach_chat_images(image_sink, images, source=source, limit=limit)
    if not mapping:
        return text

    def _replace_md(match: re.Match[str]) -> str:
        url = normalize_chat_image_url(match.group(2) or match.group(3) or "")
        index = mapping.get(url)
        return f"[图{index}]" if index else match.group(0)

    def _replace_html(match: re.Match[str]) -> str:
        url = normalize_chat_image_url(match.group(1) or "")
        index = mapping.get(url)
        return f"[图{index}]" if index else match.group(0)

    rewritten = _MD_IMAGE_RE.sub(_replace_md, text or "")
    return _HTML_IMG_RE.sub(_replace_html, rewritten)


@dataclass
class KnowledgePreContextResult:
    status: Literal["hit", "no_hit", "failed", "timeout"]
    prompt_block: str = ""
    citations: list = field(default_factory=list)
    error_code: str | None = None


async def resolve_kb_tenant(knowledge_ids: List[str]) -> Optional[str]:
    """从所选知识库的 tenant_id 解析租户，供检索带 X-Tenant-Id（Java 可访问性校验必需）。

    知识库是租户隔离的，取所选库的 tenant_id 即正确租户；Java 仍按 owner/ACL 二次鉴权，安全。
    查不到返回 None。
    """
    if not knowledge_ids:
        return None
    try:
        async with async_session() as s:
            row = (await s.execute(
                text("select tenant_id from ai_knowledge_base where id = :id limit 1"),
                {"id": str(knowledge_ids[0])},
            )).first()
            return str(row[0]) if row and row[0] is not None else None
    except Exception:  # noqa: BLE001
        return None


async def _group_knowledge_ids_by_tenant(knowledge_ids: List[str]) -> Dict[str, List[str]]:
    """按知识库自身租户拆分检索请求。

    Java 检索接口一次只接收一个 X-Tenant-Id；把跨租户 id 混在同一请求里会让除第一个
    租户以外的库被静默过滤。查库失败时返回空，由调用方保留原有单请求降级行为。
    """
    ids = [str(k) for k in knowledge_ids if k]
    if not ids:
        return {}
    try:
        async with async_session() as s:
            stmt = text("select id, tenant_id from ai_knowledge_base where id in :ids") \
                .bindparams(bindparam("ids", expanding=True))
            rows = (await s.execute(stmt, {"ids": ids})).all()
    except Exception:  # noqa: BLE001
        return {}
    groups: Dict[str, List[str]] = {}
    for kid, tenant in rows:
        key = str(tenant) if tenant is not None else ""
        groups.setdefault(key, []).append(str(kid))
    return groups


async def retrieve_knowledge(
    token: str,
    knowledge_ids: List[str],
    query: str,
    *,
    top_k: Optional[int] = None,
    threshold: Optional[float] = None,
    tenant_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    agent_user_id: Optional[str] = None,
    telemetry_user_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    source: Optional[str] = None,
    execution_id: Optional[str] = None,
    retrieval_mode: Optional[str] = None,
    semantic_weight: Optional[float] = None,
    keyword_weight: Optional[float] = None,
    rerank_enabled: Optional[bool] = None,
    citation_sink: Optional[List[dict]] = None,
    image_sink: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """知识库检索核心（供 search_knowledge 工具与前置强制检索共用）。

    显式区分三种结果，不再把失败吞成「没找到」（这是此前 RAG「用不了却无报错」的根因）：
    返回 {"ok": bool, "chunks": [{content, source, score}], "error": str|None}。
    - HTTP 非 200 / 连接异常 / Java success:false → ok=False + error（调用方决定是否透出）。
    - ok=True 且 chunks 为空 → 真·无命中。
    命中片段按内容前缀去重（多查询/重叠切片场景），并回填 citation_sink。
    """
    q = (query or "").strip()
    if not q:
        return {"ok": False, "chunks": [], "error": "query 为空"}
    if not knowledge_ids:
        return {"ok": False, "chunks": [], "error": "未选择知识库"}
    execution_id = str(execution_id or uuid.uuid4().hex)
    tenant_groups = await _group_knowledge_ids_by_tenant(knowledge_ids)
    if len(tenant_groups) > 1:
        # 同一用户显式选择多个租户的共享库时，按租户分别请求 Java 再合并；不能把首库
        # tenant 当作所有库的 tenant。子调用不直接写 citation，避免重复来源。
        grouped = await asyncio.gather(*[
            retrieve_knowledge(
                token, ids, q, top_k=top_k, threshold=threshold,
                tenant_id=tenant or None, agent_id=agent_id, agent_user_id=agent_user_id, citation_sink=None,
                telemetry_user_id=telemetry_user_id, turn_id=turn_id, source=source,
                execution_id=f"{execution_id}-{index}", retrieval_mode=retrieval_mode,
                semantic_weight=semantic_weight, keyword_weight=keyword_weight, rerank_enabled=rerank_enabled,
                image_sink=image_sink,
            )
            for index, (tenant, ids) in enumerate(tenant_groups.items(), 1)
        ])
        merged: Dict[str, dict] = {}
        for result in grouped:
            for chunk in result.get("chunks") or []:
                key = str(chunk.get("textContent") or chunk.get("content") or "")[:120]
                if key and key not in merged:
                    merged[key] = chunk
        chunks = sorted(merged.values(), key=lambda item: item.get("score") or 0, reverse=True)
        chunks = chunks[: int(top_k or settings.KNOWLEDGE_TOP_K)]
        if citation_sink is not None:
            for chunk in chunks:
                citation_sink.append({
                    "type": "knowledge",
                    "title": chunk.get("source") or "知识库片段",
                    "source": chunk.get("source") or "",
                    "snippet": _strip_markdown_images(chunk.get("textContent") or chunk.get("content"))[:300],
                })
        failures = [str(result.get("error") or "") for result in grouped if not result.get("ok")]
        return {
            "ok": any(bool(result.get("ok")) for result in grouped),
            "chunks": chunks,
            "error": "；".join(error for error in failures if error) or None,
        }
    # 租户兜底：调用方没给就从知识库自身 tenant_id 解析（Java 可访问性校验必需）——
    # 覆盖工作流子智能体等未显式传租户的路径。
    if not tenant_id:
        tenant_id = await resolve_kb_tenant(knowledge_ids)
    agent_bound = bool(agent_id)
    if agent_bound and not agent_user_id:
        return {"ok": False, "chunks": [], "error": "智能体检索缺少当前用户标识"}
    endpoint = "internal" if agent_bound else "test"
    url = f"{settings.JAVA_INTERNAL_BASE}/ai/knowledge/retrieval/{endpoint}"
    k = int(top_k or settings.KNOWLEDGE_TOP_K)
    th = settings.KNOWLEDGE_THRESHOLD if threshold is None else threshold
    payload = {
        "knowledgeIds": [str(knowledge_id) for knowledge_id in knowledge_ids],
        "query": q[:512],
        "topK": k,
        "scoreThreshold": th,
    }
    if agent_bound:
        payload["agentId"] = str(agent_id)
    if retrieval_mode:
        payload["retrievalMode"] = str(retrieval_mode)
    if semantic_weight is not None:
        payload["semanticWeight"] = semantic_weight
    if keyword_weight is not None:
        payload["keywordWeight"] = keyword_weight
    if rerank_enabled is not None:
        payload["rerankEnabled"] = rerank_enabled
    # Java 检索的可访问性校验依赖租户上下文：只带 X-Access-Token 会被判「无可访问知识库」→ 空。
    # 前端 defHttp 自动带 X-Tenant-Id，agent-api 必须补上（否则 RAG 恒空——实测根因）。
    headers = {"X-Access-Token": token or ""}
    if tenant_id:
        headers["X-Tenant-Id"] = str(tenant_id)
    if agent_bound:
        try:
            headers.update(_internal_retrieval_headers(str(agent_user_id), payload))
        except ValueError as exc:
            return {"ok": False, "chunks": [], "error": str(exc)}
    attribution_user_id = str(telemetry_user_id or agent_user_id or "").strip()
    if attribution_user_id and turn_id and source in {"CHAT", "AGENT", "WORKFLOW"}:
        try:
            headers.update(_analytics_retrieval_headers(
                attribution_user_id, payload, execution_id=execution_id,
                turn_id=str(turn_id), source=str(source),
            ))
        except ValueError as exc:
            logger.warning("知识库运营统计来源未签名: %s", exc)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except Exception as e:  # noqa: BLE001
        logger.warning("知识库检索连接异常: %s", e)
        return {"ok": False, "chunks": [], "error": f"检索服务连接失败: {e}"}
    if resp.status_code != 200:
        body = (resp.text or "")[:200]
        logger.warning("知识库检索 HTTP %s: %s", resp.status_code, body)
        return {"ok": False, "chunks": [], "error": f"检索服务返回 HTTP {resp.status_code}"}
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return {"ok": False, "chunks": [], "error": "检索响应非 JSON"}
    if isinstance(data, dict) and data.get("success") is False:
        # Java 业务失败（如 Token 非法、参数错误）——过去被当成 result=null → 空 chunks → 假装没找到
        return {"ok": False, "chunks": [], "error": str(data.get("message") or "检索失败")}
    result = data.get("result") if isinstance(data, dict) else None
    result = result if isinstance(result, dict) else (data if isinstance(data, dict) else {})
    # Java /ai/knowledge/retrieval/test 返回 RetrievalResponse{query,latencyMs,items:[...]}——
    # 命中片段在 `items`（前端 knowledge.types.ts 契约）。此前只读 chunks/records/list，
    # 字段名不匹配 → 即使检索有结果也被解析成空（RAG「用不了」的直接元凶之一）。items 优先。
    raw = (
        result.get("items")
        or result.get("chunks")
        or result.get("records")
        or result.get("list")
        or []
    )
    chunks: List[dict] = []
    seen: set = set()
    for c in raw:
        if not isinstance(c, dict):
            continue
        text_content = str(c.get("content") or c.get("text") or "").strip()
        content_with_images = str(c.get("contentWithImages") or "").strip()
        content = content_with_images or text_content
        if not content:
            continue
        key = (text_content or _strip_markdown_images(content))[:120]
        if key in seen:
            continue
        seen.add(key)
        source = str(c.get("documentName") or c.get("docName") or "")
        raw_image_urls = c.get("imageUrls")
        image_urls = [
            url.strip()
            for url in (raw_image_urls if isinstance(raw_image_urls, list) else [])
            if isinstance(url, str) and url.strip()
        ]
        if image_sink is not None:
            content = materialize_chat_images(
                content, image_sink, extra_urls=image_urls, source=source,
            )
        chunks.append({
            "content": content,
            "textContent": text_content,
            "source": source,
            "score": c.get("score") or c.get("similarity"),
            "imageUrls": image_urls,
            "chunkId": c.get("chunkId") or c.get("id"),
            "knowledgeId": c.get("knowledgeId") or c.get("knowledge_id"),
            "documentId": c.get("documentId") or c.get("document_id"),
        })
        if citation_sink is not None:
            citation_sink.append({
                "type": "knowledge",
                "title": source or "知识库片段",
                "source": source,
                "snippet": (text_content or _strip_markdown_images(content))[:300],
            })
    return {"ok": True, "chunks": chunks, "error": None}


def kb_search_query(message: str, history: Optional[List[dict]] = None) -> str:
    """构造检索查询：短问题 / 指代性追问时折叠上一条用户消息补全上下文，提升召回。

    纯启发式（零额外 LLM 调用/零延迟）：只在明显是「续问」时生效，避免污染独立完整的提问。
    """
    q = (message or "").strip()
    if not q:
        return q
    is_followup = len(q) <= 12 or re.match(
        r"^(它|他|她|它们|这个|那个|这些|那些|上面|上述|继续|然后|接着|再|还有|另外|详细|展开|为什么|怎么|如何)",
        q,
    )
    if is_followup:
        prev_user = ""
        for m in reversed(history or []):
            if (m.get("role") if isinstance(m, dict) else None) == "user":
                prev_user = str(m.get("content") or "").strip()
                break
        if prev_user and prev_user != q:
            q = f"{prev_user} {q}"
    return q[:512]


async def build_kb_pre_context_result(
    token: str,
    knowledge_ids: List[str],
    query: str,
    *,
    raw_query: Optional[str] = None,
    tenant_id: Optional[str] = None,
    citation_sink: Optional[List[dict]] = None,
    image_sink: Optional[List[dict]] = None,
    source_label: Optional[str] = None,
    telemetry_user_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    source: str = "CHAT",
) -> KnowledgePreContextResult:
    queries = [query]
    rq = (raw_query or "").strip()[:512]
    if rq and rq != query:
        queries.append(rq)
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*[
                retrieve_knowledge(
                    token, knowledge_ids, q, tenant_id=tenant_id, image_sink=image_sink,
                    telemetry_user_id=telemetry_user_id, turn_id=turn_id, source=source,
                    execution_id=uuid.uuid4().hex,
                ) for q in queries
            ]),
            timeout=max(1, int(settings.KNOWLEDGE_PRE_RETRIEVE_TIMEOUT_SECONDS)),
        )
    except asyncio.TimeoutError:
        logger.warning("知识库前置检索超时，本轮不等待结果")
        return KnowledgePreContextResult(
            status="timeout",
            prompt_block="【知识库检索超时】本轮未能及时取得所选知识库资料；请如实说明这一点，不要假装引用了资料。",
            error_code="timeout",
        )
    if not any(r["ok"] for r in results):
        logger.warning("知识库前置检索失败，本轮不注入资料: %s", results[0].get("error"))
        return KnowledgePreContextResult(
            status="failed",
            error_code=str(results[0].get("error") or "retrieve_failed"),
        )
    merged: Dict[str, dict] = {}
    for r in results:
        if not r["ok"]:
            continue
        for c in r["chunks"]:
            key = c["content"][:120]
            prev = merged.get(key)
            if prev is None or (c.get("score") or 0) > (prev.get("score") or 0):
                merged[key] = c
    chunks = sorted(merged.values(), key=lambda c: c.get("score") or 0, reverse=True)
    chunks = chunks[: max(int(settings.KNOWLEDGE_TOP_K), 8) + 4]
    if not chunks:
        return KnowledgePreContextResult(status="no_hit")
    citations = []
    for c in chunks:
        citations.append({
            "type": "knowledge",
            "title": c["source"] or "知识库片段",
            "source": c["source"],
            "snippet": c["content"][:300],
            **({"source_label": source_label} if source_label else {}),
        })
    if citation_sink is not None:
        for c in chunks:
            citation_sink.append({
                "type": "knowledge",
                "title": c["source"] or "知识库片段",
                "source": c["source"],
                "snippet": _strip_markdown_images(c.get("textContent") or c["content"])[:300],
            })
        citation_sink.extend(citations)
    blocks = []
    for i, c in enumerate(chunks, 1):
        src = f"（来源：{c['source']}）" if c["source"] else ""
        blocks.append(f"[资料{i}]{src}\n{c['content']}")
    body = "\n\n".join(blocks)
    prompt_block = (
        "以下是从用户选中的知识库中检索到的参考资料。请**优先依据这些资料**回答用户的问题；"
        "资料未覆盖到的部分，如实说明知识库中没有相关内容、不要编造。"
        "资料中如包含 [图N] 或 Markdown 图片，请保留图片与相邻文字的原始顺序，"
        "对办事有帮助的官方原图可以在对应段落后单独起行引用 [图N]；"
        "装饰性或无关图片不要输出，也不要编造链接：\n\n"
        f"===== 知识库参考资料 =====\n{body}\n===== 资料结束 ====="
    )
    return KnowledgePreContextResult(status="hit", prompt_block=prompt_block, citations=citations)


async def build_kb_pre_context(
    token: str,
    knowledge_ids: List[str],
    query: str,
    *,
    raw_query: Optional[str] = None,
    tenant_id: Optional[str] = None,
    citation_sink: Optional[List[dict]] = None,
    telemetry_user_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    source: str = "CHAT",
) -> str:
    """选中知识库时的前置强制检索：把命中片段拼成注入 system prompt 的「参考资料」块。

    不依赖模型是否主动调 search_knowledge——弱模型不发工具调用时也能基于知识库回答。
    返回空串表示无可注入（无命中或检索失败）；检索失败仅记日志、不注入报错文本污染回答。

    多查询并发召回（提升召回率）：当折叠后的 query 与原始问题 raw_query 不同（指代/短追问
    被 kb_search_query 补全时），两路并发检索取并集——同时覆盖「折叠更完整」与「原句更贴近
    文档措辞」两种命中，asyncio.gather 并发不增加墙钟时间。跨查询按内容前缀去重、按分数取优，
    统一回填 citation_sink（避免两路重复引用）。独立完整的提问 raw_query==query 时退化为单查询。
    """
    result = await build_kb_pre_context_result(
        token, knowledge_ids, query, raw_query=raw_query,
        tenant_id=tenant_id, citation_sink=citation_sink,
        telemetry_user_id=telemetry_user_id, turn_id=turn_id, source=source,
    )
    return result.prompt_block


async def _run_knowledge_search(
    token: str, knowledge_ids: List[str], query: str, citation_sink: Optional[List[dict]] = None,
    tenant_id: Optional[str] = None,
    image_sink: Optional[List[dict]] = None,
    telemetry_user_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    source: str = "CHAT",
) -> str:
    """search_knowledge 工具执行体：供模型多轮追问时补充检索（前置检索之外的按需检索）。"""
    if not query:
        raise ToolSoftError("query 不能为空")
    res = await retrieve_knowledge(
        token, knowledge_ids, query, tenant_id=tenant_id,
        citation_sink=citation_sink, image_sink=image_sink,
        telemetry_user_id=telemetry_user_id, turn_id=turn_id, source=source,
        execution_id=uuid.uuid4().hex,
    )
    if not res["ok"]:
        # 显式透出失败原因（而非假装没找到），便于定位鉴权/服务问题
        raise ToolSoftError(f"（知识库检索暂不可用：{res.get('error')}）")
    lines = [
        c["content"] + (f"\n来源: {c['source']}" if c["source"] else "")
        for c in res["chunks"]
    ]
    if lines:
        return "\n---\n".join(lines)
    # 无命中兜底：引导模型用「补全指代/更完整」的 query 重试一次，再判定无果
    return (
        "（知识库中未检索到相关内容。若当前 query 依赖上文指代或过于简略，"
        "请结合对话历史把它补全为独立完整的检索词后再调用一次 search_knowledge；"
        "若已是完整 query 仍无命中，则据此如实说明未找到，不要编造。）"
    )


def build_knowledge_tools(*, token: str, knowledge_ids: List[str],
                          citation_sink: Optional[List[dict]] = None,
                          image_sink: Optional[List[dict]] = None,
                          tenant_id: Optional[str] = None,
                          tool_meta_sink: Optional[dict] = None,
                          telemetry_user_id: Optional[str] = None,
                          turn_id: Optional[str] = None,
                          source: str = "CHAT") -> List[MainTool]:
    tools: List[MainTool] = []
    ids = list(knowledge_ids)

    async def _kb(args: dict) -> ToolValue:
        query = str(args.get("query") or "")
        citation_start = len(citation_sink) if citation_sink is not None else 0
        result = await _run_knowledge_search(
            token, ids, query, citation_sink, tenant_id=tenant_id, image_sink=image_sink,
            telemetry_user_id=telemetry_user_id, turn_id=turn_id, source=source,
        )
        hit_count = 0
        emptyish = (not result) or ("未检索到相关内容" in result) or result.startswith("（知识库")
        if not emptyish:
            sep = chr(10) + "---" + chr(10)
            hit_count = result.count(sep) + 1 if sep in result else 1
        if tool_meta_sink is not None:
            tool_meta_sink["search_knowledge"] = {
                "action": {"operation": "search", "target": query[:240]},
                "count": hit_count,
            }
        summary = f"检索到 {hit_count} 条资料" if hit_count else "知识库无相关命中"
        canonical_citations = []
        if citation_sink is not None:
            canonical_citations = [
                dict(item) for item in citation_sink[citation_start:]
                if isinstance(item, dict) and item.get("type") == "knowledge"
            ]
        return ToolValue(
            model_content=result,
            ui={
                "summary": summary,
                "detail": result[:500],
                "action": "search",
                "count": hit_count,
            },
            citations=canonical_citations,
        )

    tools.append(
        MainTool(
            name="search_knowledge",
            description=(
                "检索用户选中的知识库内容。回答与所选知识库主题相关的问题时优先使用。"
                "资料中的 [图N] 是知识库原图：对办事、流程、证件样例或校园环境有帮助时，"
                "可在对应段落后单独起行引用；不要输出无关配图，不要编造链接。"
            ),
            parameters=_query_param(),
            execute=_kb,
            public_action="检索知识库",
            output_model=ToolValue,
            semantic_tags=("search",),
            readonly=True,  # 纯检索、无用户数据副作用，网关异常时允许降级直连重跑
        )
    )
    return tools
