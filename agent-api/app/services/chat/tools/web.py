"""联网搜索工具(search_web)(结构手术 Phase 1b:自 model_driver.build_tools 原样搬迁,行为零变化)。

三段式搜索管线的工具面:结果进模型、引用进 citation_sink(编号全局递增)、
配图进 image_sink（对话附图与产物素材用内部用途标记分流）、结构化统计进 tool_meta_sink。
恒注册(readonly):web_enabled 只是「是否鼓励主动搜索」的行为偏好,不再是门槛。
"""
import re
from typing import List, Optional

from app.services.knowledge import web_search_service
from .web_freshness import (
    compact_search_query as _compact_search_query,
    query_wants_fresh as _query_wants_fresh,
    results_should_not_lock_budget as _results_should_not_lock_budget,
    search_results_structurally_usable as _search_results_structurally_usable,
    search_results_usable as _search_results_usable,
    with_today_date_token as _with_today_date_token,
)

from .base import (MainTool, ToolSoftError, ToolValue, _MAX_IMAGES_PER_MESSAGE, _host_of,
                   _query_param, current_tool_call_id, current_tool_context)

# 搜图意图识别（2026-07-22 PPT 配图断点三修 + 2026-08-05 默认过宽根治）：
# 旧实现：只要 image_sink 有空位就 with_images=True，天气/新闻等纯文本检索也会
# 并行打图片搜索 + 浪费抓取预算，还把文章缩略图塞进配图池把软上限耗光。
# 新口径：仅当查询词明显在找图时才开图片路；PPT 提示词已要求搜图查询带
# 「图片/照片/配图」字样，与这里结构条件对齐。有图意图时硬上限 24；否则不搜图。
_IMAGE_INTENT_RE = re.compile(
    r"图片|截图|壁纸|照片|配图|实景|插图|摄影|海报|photo|image|wallpaper|screenshot", re.I)
_MAX_IMAGES_HARD = 24
_IMAGE_DELIVERY_MODES = {"auto", "chat_inline", "artifact_only", "off"}

# photo-subject merge for image search ()
# Capture text immediately before a photo-noun, then strip structural glue.
_PHOTO_SUBJECT_RE = re.compile(
    r"([一-鿿]{2,16}?)"
    r"(?:的)?"
    r"(?:照片|图片|配图|实景|截图|壁纸|插图|摄影|海报|photos?|images?|pictures?|wallpaper|screenshots?)"
    ,
    re.I,
)
_PHOTO_GLUE = (
    "并在", "对话", "里附上", "附上", "附加", "配上", "放上", "给到",
    "几张", "几幅", "几个", "一些", "一张", "两张", "数张",
    "并", "在", "里", "给", "来",
)

# 图片查询里的产物/操作词不是配图主体。例如「制作 Curry HTML 页面」
# 若原样送入图搜，搜索引擎容易被 HTML/页面/模板带偏，再返回与 Curry 无关的
# 网页配图。这里只剔除跨主题通用的任务噪声，不维护业务领域词表。
_IMAGE_QUERY_TASK_NOISE_RE = re.compile(
    r"(?:请帮我|帮我|请|麻烦|制作|生成|创建|新建|设计|做一个|做一份|做个|"
    r"页面|网页|网站|站点|主页|首页|模板|素材|介绍|展示|内容|高清|精美|"
    r"html?|css|javascript|website|webpage|landing\s+page|page|template|"
    r"create|make|build|design|showcase)",
    re.I,
)
_IMAGE_TOPIC_NOISE = {
    "图片", "照片", "配图", "截图", "壁纸", "插图", "实景", "摄影", "海报", "风景",
    "photo", "photos", "image", "images", "picture", "pictures", "wallpaper", "screenshot",
}


def _image_search_subject(search_query: str, image_query: Optional[str] = None) -> str:
    """从图片查询中只保留可用于证明相关性的主体。

    image_query 比正文 query 更接近配图意图，因此优先使用；清理后为空时
    再回退到正文 query。主体仍为空意味着系统无法证明任何图片相关。
    """
    for raw in (image_query, search_query):
        subject = str(raw or "")
        subject = _IMAGE_INTENT_RE.sub(" ", subject)
        subject = _IMAGE_QUERY_TASK_NOISE_RE.sub(" ", subject)
        subject = re.sub(r"[\s,，.。！!？?；;：:\"'()（）\[\]【】/\\|]+", " ", subject)
        subject = re.sub(r"\s+", " ", subject).strip()
        if subject:
            return subject
    return ""


def _image_subject_tokens(search_query: str, image_query: Optional[str] = None) -> list[str]:
    """抽取图片相关性硬门禁使用的主体 token（去重保序）。"""
    subject = _image_search_subject(search_query, image_query)
    if not subject:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for token in web_search_service._topic_tokens(subject):
        key = str(token or "").strip().lower()
        if len(key) < 2 or key in _IMAGE_TOPIC_NOISE or key in seen:
            continue
        seen.add(key)
        out.append(str(token).strip())
    return out[:12]


def _image_is_on_topic(image: dict, subject_tokens: list[str]) -> bool:
    """只接收能从结果元数据证明与主体相关的图片。

    搜索引擎排名不是相关性证据；标题/来源/URL 都不命中时必须丢弃。
    这会宁可少图，但不会用无关图破坏正文或产物。
    """
    if not isinstance(image, dict) or not subject_tokens:
        return False
    blob = " ".join(
        str(image.get(key) or "") for key in ("title", "source", "url")
    ).lower()
    latin = [t.lower() for t in subject_tokens if re.fullmatch(r"[a-z][a-z0-9-]{2,}", t, re.I)]
    if len(latin) > 1:
        # ``Stephen Curry`` should not accept a page that only happens to mention
        # Stephen (for example, an unrelated Stephen King image).
        return all(token in blob for token in latin)
    cjk_strong = [t for t in subject_tokens if re.search(r"[一-鿿]", t) and len(t) >= 3]
    if cjk_strong:
        return any(token.lower() in blob for token in cjk_strong)
    return any(token.lower() in blob for token in subject_tokens)


def _clean_photo_subject(raw: str) -> list[str]:
    s = str(raw or "").strip()
    if not s:
        return []
    changed = True
    while changed and s:
        changed = False
        for g in sorted(_PHOTO_GLUE, key=len, reverse=True):
            if s.startswith(g):
                s = s[len(g):].lstrip()
                changed = True
                break
    if not s:
        return []
    # 空壳主语：当地/附近/这里… 不能当搜图主体（会变成「当地 图片」）
    if s in {
        "当地", "附近", "这里", "那边", "那边儿", "此处", "本处", "现场", "实景",
        "相关", "相关的", "合适", "合适的", "对应", "对应的", "主题", "主题的",
    }:
        return []
    # Keep compact noun-like tails; prefer 2-4 chars after glue strip.
    if len(s) > 4:
        s = s[-3:]
    return [s] if len(s) >= 2 else []


def _photo_subject_tokens(user_text: str, search_query: str, limit: int = 2) -> list[str]:
    """Extract uncovered photo subjects from the user message for image search merge."""
    text = str(user_text or "").strip()
    query = str(search_query or "").strip()
    if not text or limit <= 0:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def _push(tok: str) -> bool:
        tok = re.sub(r"\s+", " ", str(tok or "").strip())
        if len(tok) < 2 or tok in seen:
            return False
        if tok in query:
            return False
        if _IMAGE_INTENT_RE.search(tok):
            return False
        for qtok in re.findall(r"[一-鿿]{2,}|[A-Za-z]{3,}", query):
            if tok == qtok or tok in qtok:
                return False
            if qtok in tok and len(qtok) >= 2 and len(tok) - len(qtok) <= 1:
                return False
        seen.add(tok)
        out.append(tok)
        return True

    for m in _PHOTO_SUBJECT_RE.finditer(text):
        for cleaned in _clean_photo_subject(m.group(1)):
            if _push(cleaned) and len(out) >= limit:
                return out[:limit]

    for m in re.finditer(r"(?:photos?|images?|pictures?)\s+of\s+([A-Za-z][A-Za-z0-9 \-]{1,40})", text, re.I):
        if _push(m.group(1).strip()) and len(out) >= limit:
            return out[:limit]
    for m in re.finditer(r"([A-Za-z][A-Za-z0-9 \-]{1,40}?)\s+(?:photos?|images?|pictures?|wallpaper|screenshots?)", text, re.I):
        if _push(m.group(1).strip()) and len(out) >= limit:
            return out[:limit]

    return out[:limit]


# 签名/强防盗链 CDN（带 URL 签名，服务端带 Referer 也 403，真机实证 douyinpic）
# + 国内图库素材站（真机实证 photophoto/nipic/redocn：连接失败/超时高发，且图带水印）：
# 目录里排到最后并标注，引导模型优先挑可下载的源；不整个剔除——聊天图文混排场景
# 由用户浏览器加载，部分站点浏览器上下文仍可显示
_HOTLINK_HOSTS = (
    "douyinpic.com", "xhscdn.com", "byteimg.com", "zjcdn.com",
    "photophoto.cn", "nipic.com", "redocn.com", "699pic.com", "58pic.com", "16pic.com",
    # 2026-07-22 实测必挂（502/403/404/ConnectError 复测≥2次稳定复现）
    "ooopic.com", "588ku.com", "tooopen.com", "wotucdn.com", "tukuppt.com",
    # 千图网 CDN：技术上能下，但付费素材站水印/版权不净，按拍板排除
    "qiantucdn.com",
)

# 优选域名（2026-07-22 子代理实测：42 直链下载零失败 + 大型门户/平台自有 CDN，
# 高清、无水印、防盗链友好）：目录里前置排序，引导模型从上往下挑天然命中优质源。
_PREFERRED_HOSTS = (
    "itc.cn", "126.net", "sohucs.com", "sohu.com", "zhimg.com", "sinaimg.cn",
    "gtimg.com", "hdslb.com", "bdstatic.com", "bcebos.com", "huaban.com",
    "duitang.com", "csdnimg.cn", "thepaper.cn", "uc.cn", "pixabay.com", "pexels.com",
)


def _is_preferred(url: str) -> bool:
    return any(h in str(url or "") for h in _PREFERRED_HOSTS)


def _img_rank(img) -> int:
    """图片排序键：优选源(0) < 普通源(1) < 防盗链/素材站(2)。稳定排序保原相对次序。"""
    u = img.get("url") if isinstance(img, dict) else ""
    if _is_hotlink(u):
        return 2
    return 0 if _is_preferred(u) else 1


def _is_hotlink(url: str) -> bool:
    return any(h in str(url or "") for h in _HOTLINK_HOSTS)


def build_web_tools(*, citation_sink: Optional[List[dict]] = None,
                    image_sink: Optional[List[dict]] = None,
                    tool_meta_sink: Optional[dict] = None,
                    tool_progress_queue=None,
                    user_message: Optional[str] = None,
                    user_id: str = "",
                    run_id: str = "",
                    newapi_key: str = "",
                    image_delivery_mode: str = "auto",
                    artifact_asset_tool: str = "download_url",
                    research_profile: bool = False,
                    allowed_domains: Optional[list] = None) -> List[MainTool]:
    tools: List[MainTool] = []
    # 同轮 search_web 次数（闭包）。不能记在 ToolMetaSink 上：那是按 call_id 分桶，
    # 每次调用都会清零，预算永远打不中。build_web_tools 每轮重建一次，正好=轮级预算。
    search_call_count = {"n": 0}
    # 同轮是否已拿到可用结果（供回执质量统计 / 可选提示，**不再硬拦**调用次数）。
    search_quality = {"had_results": False, "had_images": False}
    # 当轮用户原话（可空）：模型 query 常漏写「照片/图片」，但用户明明要附图。
    # 用用户消息补齐 image intent，避免「先搜信息再搜图」白白多一轮墙钟。
    # 纯信息问句（用户未提图）仍不搜图——不回退到旧的「有 sink 就 with_images」。
    user_text = str(user_message or "")
    user_requests_images = bool(user_text and _IMAGE_INTENT_RE.search(user_text))
    official_only = allowed_domains is not None
    requested_mode = str(image_delivery_mode or "auto").strip().lower()
    asset_tool = (
        "fetch_ppt_asset" if str(artifact_asset_tool or "") == "fetch_ppt_asset"
        else "download_url"
    )
    if requested_mode not in _IMAGE_DELIVERY_MODES:
        requested_mode = "auto"
    effective_image_mode = (
        "chat_inline" if requested_mode == "auto" and user_requests_images
        else "off" if requested_mode == "auto"
        else requested_mode
    )
    user_wants_images = user_requests_images and effective_image_mode != "off"
    display_inline_images = effective_image_mode == "chat_inline"
    # search_web 恒注册为核心只读工具（长任务中途查资料是刚需，任务模式设计稿 §7）；
    # web_enabled 从「能否搜索」的门槛降级为「是否鼓励主动搜索」的行为偏好——
    # 由 prompt 层承载，不再决定本工具的可用性（原 if _register_web 死条件已清）。
    async def _web(args: dict) -> ToolValue:
        # 引用编号全局递增：同轮多次搜索接着上次编号，正文 [编号] 角标才能反查到正确来源
        start = 1
        if citation_sink is not None:
            start += sum(1 for c in citation_sink if isinstance(c, dict) and c.get("type") == "web")

        # 逐源阅读进度：抓取段每开始读一个页面就上报一条 tool.progress
        # （前端执行流渲染「正在阅读 <页面标题>」行，Manus 式过程可见）；失败静默不影响搜索
        async def _reading(item: dict) -> None:
            if tool_progress_queue is None:
                return
            title = str(item.get("title") or item.get("url") or "网页")[:80]
            try:
                await tool_progress_queue.put({
                    # call_id：并发只读工具共用一条队列，消费方靠它精确归属（见 base.py）
                    "call_id": current_tool_call_id(),
                    "name": "search_web", "stage": "reading",
                    "label": f"正在阅读 {title}",
                    "detail": {"url": str(item.get("url") or ""), "title": title},
                })
            except Exception:  # noqa: BLE001
                pass

        # progress_cb 只在有进度通道时才传：对旧签名的调用面/测试桩保持向后兼容
        search_kwargs = {"progress_cb": _reading} if tool_progress_queue is not None else {}
        _tool_context = current_tool_context()
        if user_id:
            search_kwargs.update({
                "caller_user_id": str(user_id),
                "caller_run_id": str(run_id or ""),
                "caller_thread_id": str(
                    _tool_context.thread_id if _tool_context is not None else ""
                ),
                "caller_tool_call_id": str(
                    _tool_context.call_id if _tool_context is not None else ""
                ),
                "caller_parent_logical_call_id": str(
                    _tool_context.parent_logical_call_id if _tool_context is not None else ""
                ),
                "caller_execution_segment": str(
                    _tool_context.execution_segment if _tool_context is not None else ""
                ),
            })
        if newapi_key:
            # 用户注册时已由平台分配的 NewAPI Key，仅在后端内存中透传；
            # DeepSeek 搜索兜底因此按发起人计费，无需用户另填凭据。
            search_kwargs["caller_newapi_key"] = str(newapi_key)
        query_text = str(args.get("query") or "")
        # ：search_web **不再按次数硬拒 / 软锁**。
        # 产品口径：信息是否够由模型判断，够了就停手写答案；硬顶会与「多角度调研」打架。
        # 次数仅计数；广撒网时的收敛提示由主循环 SINGLE_TOOL_NUDGE（回执尾软文案）承担，
        # 整轮仍有 TOOL_LOOP 轮次/墙钟/token 熔断，防止真·死循环烧资源。
        used = int(search_call_count["n"])
        search_call_count["n"] = used + 1
        query_image_intent = bool(_IMAGE_INTENT_RE.search(query_text))
        image_intent = (query_image_intent or user_wants_images) and effective_image_mode != "off"
        # 纯信息检索默认不搜图；query 或当轮用户消息带搜图意图时才并行图片段。
        # image_sink 有无只决定「能不能收集」，不再单独决定「要不要搜」。
        image_cap = _MAX_IMAGES_HARD if image_intent else _MAX_IMAGES_PER_MESSAGE
        with_images = (
            image_intent
            and image_sink is not None
            and len(image_sink) < image_cap
        )
        # 附图路径（）：模型 query 常只写信息主题（如「漳州天气」），用户还要
        # 另一主题的图（如「东山岛照片」）。正文 query 保持干净；配图主体只进
        # image_query，避免「东山岛/图片」把天气/新闻正文检索带偏。
        search_query = str(query_text or "").strip()
        # 时效紧迫：不再注入日期（with_today_date_token 为 passthrough）。
        if _query_wants_fresh(query_text, user_text):
            search_query = _with_today_date_token(search_query)
        # ：天气类 query 补「今日 气温 实况」，提高 SERP 命中可核验 ℃ 的概率。
        # 不靠领域词表拦路由，只优化检索词本身。
        if re.search(r"(天气|气温|预报)", search_query) and not re.search(
            r"(气温|实况|℃|°C|温度)", search_query
        ):
            # 不用「今天/今日」：web_search_service 会剥掉日期词；用「实时 气温」更稳。
            search_query = f"{search_query} 实时 气温".strip()
        # ：服务端压紧过长/过礼貌/过时效 query，避免 SERP 被日期/填充词搞脏。
        search_query = _compact_search_query(search_query)
        image_query = None
        if with_images and search_query:
            subjects: list[str] = []
            # Chat-inline requests may name a second visual subject (for example
            # "weather plus Dongshan Island photos"), so the user message remains useful
            # there. Artifact prompts are usually long production briefs; extracting a
            # pseudo-subject from them produced garbage queries and left the PPT gallery
            # with one image. For artifacts, the model's focused search query is the
            # authoritative image subject.
            if user_wants_images and user_text and effective_image_mode == "chat_inline":
                subjects = _photo_subject_tokens(user_text, search_query, limit=2)
            if subjects:
                image_query = f"{' '.join(subjects)} 实景 照片".strip()
            else:
                # 无独立图主语时：先剔除 HTML/制作/网页等产物噪声，避免
                # 「制作 Curry HTML」被搜成 HTML 模板或普通网页配图。
                base = re.sub(
                    r"(天气|气温|预报|实况|新闻|股价|今天|今日|现在)",
                    " ",
                    search_query,
                )
                base = _image_search_subject(base)
                image_query = f"{base} 实景 照片".strip() if base else None
        if official_only:
            search_kwargs["allowed_domains"] = list(allowed_domains)
        res = await web_search_service.search_web(
            search_query, start_index=start,
            with_images=with_images,
            image_query=image_query,
            research_depth=bool(research_profile),
            **search_kwargs,
        )
        results = [r for r in (res.get("results") or []) if isinstance(r, dict)]
        # 后端故障 ≠ 没有结果（2026-07-28）。SearXNG 挂掉/超时/429/引擎被 CAPTCHA 封时，
        # 这里此前和「确实没搜到」返回同一句「（联网搜索无结果）」——模型分辨不出来，
        # 于是直接告诉用户「没有查到相关信息」，把一个可修的服务故障说成了事实结论。
        # 软失败会以 failed 状态回灌模型，措辞明确要求它别下"查不到"的结论。
        search_error = str(res.get("error") or "")
        if search_error and not results:
            raise ToolSoftError(
                f"联网搜索没跑通：搜索后端不可用（{search_error[:160]}）。"
                "**这不等于「没有查到相关信息」**——不要对用户说没搜到、也不要据此下结论。"
                "可以隔几秒重试一次；仍不行就改用 browser_fetch 直接打开你已知的权威页面，"
                "或者如实告诉用户联网搜索服务暂时不可用、这部分信息本次无法核实。"
            )
        canonical_citations = [
            {
                    "type": "web",
                    "title": str(r.get("title") or r.get("url") or "网页"),
                    "url": str(r.get("url") or ""),
                    "snippet": str(r.get("content") or "")[:300],
                    **({"source_label": "官方网页实时检索"} if official_only else {}),
            }
            for r in results[:8]
        ]
        # Compatibility projection for the existing message citation finalizer.  The canonical
        # value below is now the durable source; this sink can be removed once that projector
        # reads observations directly.
        if citation_sink is not None:
            citation_sink.extend(canonical_citations)
        # 搜索附带图片：编号全局递增（跨多次搜索），进 image_sink（下发+持久化）与
        # tool.completed meta（前端实时拿到 [图N]→URL 映射）；目录 + 使用要求随
        # 工具结果给模型（贴近作答的指令服从率高）。
        new_images: List[dict] = []
        # 未开 with_images 时后端 images 本应为空；仍以 with_images 守门，避免旧桩/脏返回污染配图池。
        if with_images and image_sink is not None:
            seen_urls = {i.get("url") for i in image_sink}
            # 三档排序：优选门户 CDN 前置 → 普通源 → 防盗链/素材站沉底；编号靠前=更优质更好下，
            # 模型按序取图自然优先命中高清无水印源（stable sort 保引擎原相对次序）
            ordered = sorted((res.get("images") or []), key=_img_rank)
            host_counts = {}
            # 对话附图：优先多样性，同 host 最多 2 张，避免连出重复风光照
            chat_cap = min(image_cap, max(6, len(image_sink) + 6))
            # 标题/来源/URL 必须命中配图核心主体。搜索引擎原始排名只负责排序，
            # 不能在相关性门禁全灭后反过来当作「兜底」重新放行。
            subject_tokens = _image_subject_tokens(search_query, image_query)

            def _accept(im: dict) -> bool:
                if not isinstance(im, dict) or im.get("url") in seen_urls:
                    return False
                if len(image_sink) >= chat_cap:
                    return False
                if official_only:
                    from app.services.chat.builtin_assistants.campus_services.domain_policy import url_allowed
                    if not (
                        url_allowed(str(im.get("url") or ""), allowed_domains)
                        or url_allowed(str(im.get("source") or ""), allowed_domains)
                    ):
                        return False
                host = _host_of(im.get("source") or im.get("url") or "") or "unknown"
                if int(host_counts.get(host) or 0) >= 2:
                    return False
                seen_urls.add(im.get("url"))
                host_counts[host] = int(host_counts.get(host) or 0) + 1
                scoped = {**im, "display_scope": effective_image_mode}
                image_sink.append(scoped)
                new_images.append({**scoped, "index": len(image_sink)})
                return True

            for img in ordered:
                if not _image_is_on_topic(img, subject_tokens):
                    continue
                if not _accept(img):
                    if len(image_sink) >= chat_cap:
                        break
        if tool_meta_sink is not None:
            tool_meta_sink["search_web"] = {
                "action": {"operation": "search", "target": str(search_query or args.get("query") or "")[:240]},
                "count": len(results),
                "urls": [str(r.get("url") or "") for r in results if r.get("url")],
                "read": res.get("scraped_pages") or [],
                **({"images": new_images} if new_images and display_inline_images else {}),
            }
        # 到这里 error 为空（有故障且无结果的已在上面软失败），所以这句兜底文案
        # 现在真的只对应「搜索跑通了但一条都没有」——它不再会替后端故障顶包。
        text = res.get("text") or "（联网搜索无结果）"
        if official_only and image_sink is not None:
            from .knowledge import attach_chat_images, iter_embedded_images, materialize_chat_images
            extra = []
            for row in results:
                page = str(row.get("url") or "")
                for url, title in iter_embedded_images(str(row.get("content") or "")):
                    extra.append({"url": url, "title": title, "source": page})
            before = len(image_sink)
            attach_chat_images(image_sink, extra, limit=max(6, _MAX_IMAGES_PER_MESSAGE))
            for offset, item in enumerate(image_sink[before:]):
                if not isinstance(item, dict):
                    continue
                scoped = {**item, "index": before + offset + 1}
                new_images.append(scoped)
            text = materialize_chat_images(str(text), image_sink, limit=max(6, _MAX_IMAGES_PER_MESSAGE))
        if new_images:
            def _mark(i: dict) -> str:
                u = str(i.get("url") or "")
                if _is_hotlink(u):
                    return f"（{_host_of(i.get('source') or u)}·下载易失败或带水印，慎选）"
                if _is_preferred(u):
                    return f"（{_host_of(i.get('source') or u)}·高清优选）"
                return f"（{_host_of(i.get('source') or u)}）"
            lines = [
                f"[图{i['index']}] {str(i.get('title') or '图片')[:60]}{_mark(i)}"
                for i in new_images
            ]
            if display_inline_images:
                text += (
                    "\n\n本次搜索附带的相关图片（编号全局唯一）：\n" + "\n".join(lines) +
                    "\n（这是对话附图：从中选出与内容最贴切的 1-3 张，在对应段落之后"
                    "单独起一行写 [图N]；不要 download_url，不要另存文件或 HTML。"
                    "只能引用已列出的编号，禁止编造链接。）"
                )
            else:
                text += (
                    "\n\n本次搜索可用的产物配图素材（编号全局唯一）：\n" + "\n".join(lines) +
                    "\n（这些图片只用于 PPT/Word/PDF/HTML 等产物。需要嵌入时使用 "
                    f"{asset_tool}(url=\"图N\")；搜索结果不是已下载文件，调用后还必须在产物中引用；"
                    "最终对话正文不得输出 [图N] 或 Markdown 图片。）"
                )
        elif effective_image_mode == "artifact_only" and image_sink:
            available = " ".join(f"[图{i}]" for i in range(1, len(image_sink) + 1))
            text += (
                f"\n\n（本次没有新增图片；当前可用图片编号仅为 {available}。"
                "网页来源编号如 [12] 不是图片编号，不得改写为 [图12]。）"
            )
        count = len(results)
        structural = _search_results_structurally_usable(results)
        stale = bool(
            structural
            and _results_should_not_lock_budget(
                results, query=query_text, user_message=user_text,
            )
        )
        usable = _search_results_usable(
            results,
            query=query_text,
            user_message=user_text,
        )
        # 只有「可用内容」才锁预算。入口页/空壳摘要/明显过期时效不算可用。
        if usable:
            search_quality["had_results"] = True
        if new_images or (image_sink is not None and len(image_sink) > 0):
            search_quality["had_images"] = True
        summary = f"搜索到 {count} 个网页" if count else "联网搜索无结果"
        if count > 0 and not usable:
            if stale:
                text = (
                    str(text)
                    + "\n\n（系统提示：本次摘要虽有数值，但发布时间/正文日期相对今天偏旧，"
                    "不能当作实时答案。允许用更短主题词再搜 1 次（如「地名 主题」），禁止加「今天/月日/年月日/实况/预报」；"
                    "不要把过期数据说成今天的实况，也不要空口编造。）"
                )
            else:
                text = (
                    str(text)
                    + "\n\n（系统提示：本次虽有链接，但摘要偏薄（多为入口/导航/过时壳层，"
                    "缺少可核验的具体数据）。允许换更具体的短关键词再搜 1 次；"
                    "不要空口编造数值。仍无细节时再如实说明局限。）"
                )
        elif usable and not (
            user_wants_images and image_sink is not None and len(image_sink) < 1
        ):
            image_note = ""
            if effective_image_mode == "artifact_only" and image_sink is not None:
                if new_images:
                    image_note = "与本次新增配图素材"
            if research_profile:
                text = (
                    str(text)
                    + "\n\n（研究提示：本次已返回可用正文。先把它与当前研究问题和已有来源对照："
                    "若核心结论仍缺一手来源、反例、时效证据、可比口径或独立交叉验证，"
                    "继续用更聚焦的短 query 搜索；若这些具体缺口都已补齐，就停止检索并综合。"
                    "搜索次数不是完成条件，也不要为了显得深入而换词空转。）"
                )
            else:
                text = (
                    str(text)
                    + "\n\n（系统提示：本次已返回可用结果"
                    + image_note
                    + "。摘要若已有可核验数值（如 ℃、%、区间、量级），即使未写完整年月日、或同组混有旧闻，也**必须**把带数值的那几条当作当前可用事实直接作答；"
                    "禁止空口说「无法获取/未能取到/没能查到/无法直接采用」；禁止「建议打开 App/以官方为准/仅供参考/可能不准确」把责任推回用户；"
                    "不要因「像缓存/偏旧」整段拒答；不要再调用 search_web；"
                    + (
                        "对话附图只在正文用 [图N] 内联，禁止 download_url 或另写 HTML。"
                        if display_inline_images
                        else f"配图素材只进文件产物，用 {asset_tool} 取得并嵌入，禁止在对话正文写 [图N]。"
                    )
                    + "）"
                )
        if new_images and user_wants_images and display_inline_images:
            text = (
                str(text)
                + "\n\n（系统提示：配图目录已有可用图片。最终回答必须在相关段落后单独起行写 [图1] [图2] 等"
                + f"（至少 1 张，最多 {min(3, len(new_images))} 张），禁止只写文字不附图。）"
            )
        return ToolValue(
            model_content=text,
            ui={
                "summary": summary,
                "detail": text[:500],
                "action": "search",
                "count": count,
                **({"images": list(new_images)} if new_images and display_inline_images else {}),
            },
            citations=canonical_citations,
        )

    tools.append(
        MainTool(
            name="search_web",
            description=(
                "联网搜索公开信息。深度研究按主题多角度换词检索，不要因为已经搜过几次就停，"
                "也不要深读或打开网页代替搜索。query 保持短（2-8 个词）。"
                if research_profile else
                "联网搜索实时/公开信息。需要最新资讯、平台知识库和技能都无法回答时使用。"
                "query 必须短：地名/主题 2-6 个词（如「漳州 天气」）。"
                "禁止堆「今天/月日/年月日/实况/预报」长串——中文引擎会返回黄历/往年/入口壳；优先采信带可核验数值的摘要。"
                "用户同时要「信息 + 配图」时：一次 query 同时写入主题与「图片/照片/配图」字样。"
                f"普通问答按 [图N] 对话展示；文件产物用 {asset_tool} 取得图片并嵌入，不在终答展示。"
                "同一问题优先只搜 1 次；仅当首次无结果/结果明显偏薄（只有入口页壳层、无可核验细节），"
                "或用户要配图而图目录仍空时，才允许第 2 次换更具体的短关键词。"
                "第 3 次起会被拒绝。已有可用细节时不要换词空转；信息仍不足则如实说明局限，禁止编造。"
            ),
            parameters=_query_param(),
            execute=_web,
            public_action="检索外部资料",
            output_model=ToolValue,
            semantic_tags=("web_search", "search"),
            readonly=True,  # 纯检索、无用户数据副作用，网关异常时允许降级直连重跑
            # ：parallel_safe 必须 False。readonly 默认会并发预执行；
            # 同批两次 search_web 会在 count/had_results 更新前同时通过预算检查，
            # 把「最多 2 次 / 有结果禁二次」整段架空（天气 live 真机出现 2× 空转）。
            parallel_safe=False,
        )
    )
    return tools
