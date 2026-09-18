"""知识库检索:核心管线 + search_knowledge 工具(结构手术 Phase 1b:自 model_driver.build_tools 原样搬迁,行为零变化)。

retrieve_knowledge(显式区分失败/无命中,防「用不了却无报错」)、
kb_search_query(短问指代折叠)、build_kb_pre_context(前置强制检索,弱模型
不发工具调用也能基于知识库回答)是主对话与工具共用的检索核心;
search_knowledge 工具供模型多轮按需补检。harness_orchestrator 经 main_agent re-export 调用。
"""
import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from sqlalchemy import text

from app.core.config import settings
from app.core.database import async_session

from .base import MainTool, ToolSoftError, ToolValue, _query_param

logger = logging.getLogger(__name__)


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
                text("select tenant_id from agent_knowledge_base where id = :id limit 1"),
                {"id": str(knowledge_ids[0])},
            )).first()
            return str(row[0]) if row and row[0] is not None else None
    except Exception:  # noqa: BLE001
        return None


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
    - 无权访问 / 向量模型未配置 / 检索异常 → ok=False + error（调用方决定是否透出）。
    - ok=True 且 chunks 为空 → 真·无命中。
    命中片段按内容前缀去重（多查询/重叠切片场景），并回填 citation_sink。

    retrieval_mode / semantic_weight / keyword_weight / rerank_enabled 是工作流
    知识检索节点传下来的检索配置，原样透传给 search_chunks：None 表示取知识库自己保存的
    设置（主对话走这条路），拼写归一化与不认识取值的降级都在 search_chunks 入口做。
    """
    q = (query or "").strip()
    if not q:
        return {"ok": False, "chunks": [], "error": "query 为空"}
    if not knowledge_ids:
        return {"ok": False, "chunks": [], "error": "未选择知识库"}
    # 检索改为进程内直连（原先 POST 到 Java 的 /ai/knowledge/retrieval/*）。
    # Java 下线后 auth-api 只剩一个统一返回 503 的桩：知识库页面能检索、对话里
    # 却恒报「知识库检索暂不可用」——同一套知识库两条链路，只有这条断着。
    from app.core import auth as core_auth
    from app.services.knowledge import knowledge_base_service as kb

    # 可访问性校验原本在 Java 侧。这里必须自己做：直接把调用方传来的 id 丢给
    # 向量库，等于任何人都能检索别人的知识库。
    acting_user_id = str(agent_user_id or telemetry_user_id or "").strip()
    is_admin_user = False
    if not acting_user_id:
        user = await core_auth.user_from_token(token)
        if user is None:
            return {"ok": False, "chunks": [], "error": "检索身份校验失败，请重新登录"}
        acting_user_id = str(user.user_id)
        is_admin_user = core_auth.is_admin(user)

    allowed_ids = await kb.accessible_ids(
        knowledge_ids, user_id=acting_user_id, is_admin=is_admin_user,
    )
    if not allowed_ids:
        return {"ok": False, "chunks": [], "error": "没有可检索的知识库（无权访问或已停用）"}

    k = int(top_k or settings.KNOWLEDGE_TOP_K)
    th = settings.KNOWLEDGE_THRESHOLD if threshold is None else threshold
    search_started = time.monotonic()
    search_telemetry: Dict[str, Any] = {}
    try:
        hits = await kb.search_chunks(
            knowledge_ids=allowed_ids, query=q[:512], top_k=k, score_threshold=th,
            rerank=rerank_enabled,
            retrieval_mode=retrieval_mode,
            semantic_weight=semantic_weight,
            keyword_weight=keyword_weight,
            telemetry=search_telemetry,
        )
    except ValueError as exc:
        # 向量模型未配置等可读原因，原样透出而不是假装没找到
        return {"ok": False, "chunks": [], "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("知识库检索失败: %s", exc, exc_info=True)
        return {"ok": False, "chunks": [], "error": f"检索失败: {exc}"}
    # 运营统计的数据源：每个实际参与检索的库各记一行（无命中也记，无命中率靠它算）。
    # 只记成功执行的检索——上面抛出的异常是「检索没跑成」，不是「跑了没命中」，混进去会
    # 虚高无命中率。写入失败在 record_retrieval 里 warning 吞掉，不影响本轮回答。
    from app.services.knowledge import retrieval_log_service
    await retrieval_log_service.record_retrieval(
        knowledge_ids=allowed_ids, hits=hits, query=q,
        user_id=telemetry_user_id or acting_user_id,
        source=source or "CHAT",
        latency_ms=(time.monotonic() - search_started) * 1000,
        retrieval_mode=search_telemetry.get("retrieval_mode"),
        reranked=bool(search_telemetry.get("reranked")),
        turn_id=turn_id,
    )

    # 归一到下面那段解析逻辑认得的字段名，复用它的去重、配图与引用回填。
    raw = [{
        "content": hit.get("content"),
        "documentName": hit.get("source"),
        "score": hit.get("score"),
        "vectorScore": hit.get("vectorScore"),
        "keywordScore": hit.get("keywordScore"),
        "knowledgeId": hit.get("knowledgeId"),
        "documentId": hit.get("documentId"),
    } for hit in hits]
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
            # 两路召回分各自保留（没走那一路的为 None），排障时能看出一条是靠什么召回的
            "vectorScore": c.get("vectorScore"),
            "keywordScore": c.get("keywordScore"),
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
