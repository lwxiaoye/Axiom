"""平台功能配置（键值 JSON）读写服务。

承载联网搜索（ADR-037）与 OCR（ADR-040）的管理端配置。密钥字段明文存于
`agent_platform_config.config_json`，仅管理员经受保护的路由读写；返回前端时脱敏
（密钥置空 + `secrets_set` 标记「已配置」），保存时空密钥字段保留库中原值。
"""
import json
import logging
from typing import Any, Dict, Iterable

from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session
from app.models import PlatformConfig

logger = logging.getLogger(__name__)

WEB_SEARCH_KEY = "web_search"
OCR_KEY = "ocr"

# 缺省值由 env 播种（settings.WEB_SEARCH_*，见 config.py）：全新部署/新库不进后台即可用；
# 后台「联网搜索配置」页保存过的字段以库中值为准（_get_raw 里 stored 覆盖 defaults）。
# scraper/reranker 的 provider 缺省随 env 地址联动：填了 firecrawl/TEI 地址即默认启用对应段。
WEB_SEARCH_DEFAULTS: Dict[str, Any] = {
    "enabled": settings.WEB_SEARCH_ENABLED,
    # New deployments do not implicitly enable an external model service.
    "providerMode": "primary_fallback",
    "providerPool": [],
    "searchProvider": "searxng",   # 旧配置/图片搜索兼容字段
    "deepseekModel": "deepseek-v4-flash",
    "deepseekMaxTokens": 4096,
    "deepseekSearchProtocol": "responses",
    # Retained for old clients; research now uses the same sequential routing.
    "deepseekResearchHybrid": False,
    "deepseekResearchMaxQueries": 3,
    "deepseekCredentialMode": "assigned-newapi-key",
    "searxngUrl": settings.WEB_SEARCH_SEARXNG_URL,
    # 逗号分隔的 SearXNG 引擎名，非空则显式指定（否则走实例默认聚合）。
    # 境内服务器建议填国内引擎，如 baidu,sogou,360search,quark（境外引擎多被墙会 0 结果）。
    "searxngEngines": settings.WEB_SEARCH_SEARXNG_ENGINES,
    # 图片段专用引擎（空=按 searxngEngines 自动推导 images 变体；显式配置可绕开死引擎）
    "searxngImageEngines": settings.WEB_SEARCH_SEARXNG_IMAGE_ENGINES,
    "searxngApiKey": "",
    "serperApiKey": "",
    "tavilyApiKey": "",
    "scraperProvider": "firecrawl" if settings.WEB_SEARCH_FIRECRAWL_URL else "none",  # none | firecrawl | tavily
    "firecrawlUrl": settings.WEB_SEARCH_FIRECRAWL_URL,
    "firecrawlApiKey": "",
    # none | jina | cohere | local（自托管 TEI /rerank）| platform（平台重排模型，走 rerank_service；
    # 缺省不随它联动——缺省在 import 时算，那时读不到库里有没有重排配置，运行时未配置会退化为 none）
    "rerankerProvider": "local" if settings.WEB_SEARCH_LOCAL_RERANKER_URL else "none",
    "jinaApiKey": "",
    "cohereApiKey": "",
    # 自托管重排服务地址（TEI 等，兼容 /rerank 接口），如 http://<服务器IP>:8087
    "localRerankerUrl": settings.WEB_SEARCH_LOCAL_RERANKER_URL,
    "topK": 5,
}
WEB_SEARCH_SECRETS = {
    "searxngApiKey", "serperApiKey", "tavilyApiKey",
    "firecrawlApiKey", "jinaApiKey", "cohereApiKey",
}

_SEARCH_PROVIDER_IDS = {"deepseek-official", "searxng", "serper", "tavily"}


def _normalize_provider_pool(data: Dict[str, Any]) -> Dict[str, Any]:
    """Honor the deployment's search order and explicit external-service opt-in."""
    normalized = dict(data)
    deepseek_first = normalized.get("providerMode") == "deepseek_first"
    # 兜底固定走平台 NewAPI 的 Responses 原生搜索，并使用发起人的已分配 Key 计费。
    # 历史版本曾暴露可配置的官方 Base URL；归一化时直接丢弃，避免再次进入管理接口。
    normalized.pop("deepseekBaseUrl", None)
    # Anthropic Messages 的 max_uses 不适用于 Responses；清掉旧管理配置，避免
    # 让管理员误以为它还能限制 DeepSeek 的服务端自动搜索轮次。
    normalized.pop("deepseekMaxUses", None)
    normalized["deepseekCredentialMode"] = "assigned-newapi-key"
    normalized["deepseekSearchProtocol"] = "responses"
    raw_rows = normalized.get("providerPool")
    pool: list[Dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(raw_rows, list):
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            provider_id = str(raw.get("id") or "").strip().lower()
            if provider_id not in _SEARCH_PROVIDER_IDS or provider_id in seen:
                continue
            seen.add(provider_id)
            try:
                weight = max(1, min(1000, int(raw.get("weight") or 100)))
            except (TypeError, ValueError):
                weight = 100
            pool.append({
                "id": provider_id,
                "enabled": bool(raw.get("enabled", True)),
                "weight": weight,
                "fallbackOnly": bool(raw.get("fallbackOnly", False)),
            })
    if not pool:
        legacy = str(normalized.get("searchProvider") or "searxng").strip().lower()
        if legacy not in _SEARCH_PROVIDER_IDS or legacy == "deepseek-official":
            legacy = "searxng"
        pool = [
            {"id": "deepseek-official", "enabled": deepseek_first, "weight": 100, "fallbackOnly": not deepseek_first},
            {"id": legacy, "enabled": True, "weight": 100, "fallbackOnly": deepseek_first},
        ]
    deepseek_enabled = any(row["id"] == "deepseek-official" and row["enabled"] for row in pool)
    for row in pool:
        row["fallbackOnly"] = (
            row["id"] != "deepseek-official" if deepseek_first else row["id"] == "deepseek-official"
        ) if deepseek_enabled else False
    pool.sort(key=lambda row: (bool(row["fallbackOnly"]),))
    normalized["providerMode"] = "deepseek_first" if deepseek_first and deepseek_enabled else "primary_fallback"
    normalized["providerPool"] = pool
    # searchProvider also selects image search; DeepSeek web_search has no image-result contract.
    normalized["searchProvider"] = next(
        (row["id"] for row in pool if row["enabled"] and row["id"] != "deepseek-official"), "searxng"
    )
    try:
        normalized["deepseekMaxTokens"] = max(1024, min(32768, int(normalized.get("deepseekMaxTokens") or 4096)))
    except (TypeError, ValueError):
        normalized["deepseekMaxTokens"] = 4096
    normalized["deepseekResearchHybrid"] = False
    try:
        normalized["deepseekResearchMaxQueries"] = max(
            1, min(12, int(normalized.get("deepseekResearchMaxQueries") or 3))
        )
    except (TypeError, ValueError):
        normalized["deepseekResearchMaxQueries"] = 3
    return normalized

OCR_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "strategy": "none",            # none | custom_endpoint | multimodal_model
    # ---- custom_endpoint 策略 ----
    "endpointUrl": "",
    "apiKey": "",
    # ---- multimodal_model 策略（视觉模型看图→回传内容给主对话模型）----
    "model": "",                   # 视觉模型名，如 qwen-vl-max / glm-4v / gpt-4o
    # 独立 OpenAI 兼容端点：三者都填则直连该端点；visionBaseUrl 留空则回退平台 New API 网关。
    "visionBaseUrl": "",           # 如 https://dashscope.aliyuncs.com/compatible-mode/v1
    "visionApiKey": "",
    # 可选：自定义视觉提示词。留空用默认（完整描述图片内容+转录文字，便于主模型据此作答）。
    "visionPrompt": "",
}
OCR_SECRETS = {"apiKey", "visionApiKey"}


async def _get_raw(key: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
    """读取某个功能域的完整配置（含明文密钥），缺省字段以默认值补齐。"""
    async with async_session() as session:
        row = await session.get(PlatformConfig, key)
    stored: Dict[str, Any] = {}
    if row and row.config_json:
        try:
            parsed = json.loads(row.config_json)
            if isinstance(parsed, dict):
                stored = parsed
        except (ValueError, TypeError):
            logger.warning("平台配置 %s 的 JSON 解析失败，回退默认值", key)
    merged = dict(defaults)
    merged.update({k: v for k, v in stored.items() if k in defaults})
    return merged


async def _save_raw(key: str, data: Dict[str, Any]) -> None:
    async with async_session() as session:
        row = await session.get(PlatformConfig, key)
        payload = json.dumps(data, ensure_ascii=False)
        if row:
            row.config_json = payload
        else:
            session.add(PlatformConfig(config_key=key, config_json=payload))
        await session.commit()


def _mask(data: Dict[str, Any], secret_fields: Iterable[str]) -> Dict[str, Any]:
    """脱敏：密钥字段置空，附带 `secrets_set` 标记每个密钥是否已配置。"""
    masked = dict(data)
    secrets_set: Dict[str, bool] = {}
    for field in secret_fields:
        secrets_set[field] = bool(str(data.get(field) or "").strip())
        masked[field] = ""
    masked["secrets_set"] = secrets_set
    return masked


def _merge_incoming(
    current: Dict[str, Any],
    incoming: Dict[str, Any],
    defaults: Dict[str, Any],
    secret_fields: Iterable[str],
) -> Dict[str, Any]:
    """合并提交值：仅覆盖已知字段；空密钥字段保留库中原值。"""
    secrets = set(secret_fields)
    result = dict(current)
    for field in defaults:
        if field not in incoming:
            continue
        value = incoming[field]
        if field in secrets:
            if str(value or "").strip():
                result[field] = value
            # 空密钥：保留原值
        else:
            result[field] = value
    return result


# ---- 联网搜索（ADR-037） ----

async def get_web_search_masked() -> Dict[str, Any]:
    data = _normalize_provider_pool(await _get_raw(WEB_SEARCH_KEY, WEB_SEARCH_DEFAULTS))
    return _mask(data, WEB_SEARCH_SECRETS)


async def save_web_search(incoming: Dict[str, Any]) -> Dict[str, Any]:
    current = await _get_raw(WEB_SEARCH_KEY, WEB_SEARCH_DEFAULTS)
    merged = _normalize_provider_pool(
        _merge_incoming(current, incoming, WEB_SEARCH_DEFAULTS, WEB_SEARCH_SECRETS)
    )
    await _save_raw(WEB_SEARCH_KEY, merged)
    return _mask(merged, WEB_SEARCH_SECRETS)


async def get_web_search_config() -> Dict[str, Any]:
    """运行时用：返回含明文密钥的完整配置（供联网搜索工具消费）。"""
    cfg = _normalize_provider_pool(await _get_raw(WEB_SEARCH_KEY, WEB_SEARCH_DEFAULTS))
    # Nonempty deployment overrides retain the original local configuration
    # precedence. The saved admin values are used when an override is empty.
    env_url = str(getattr(settings, "WEB_SEARCH_SEARXNG_URL", "") or "").strip()
    if env_url:
        cfg["searxngUrl"] = env_url
    env_engines = str(getattr(settings, "WEB_SEARCH_SEARXNG_ENGINES", "") or "").strip()
    if env_engines:
        cfg["searxngEngines"] = env_engines
    env_img = str(getattr(settings, "WEB_SEARCH_SEARXNG_IMAGE_ENGINES", "") or "").strip()
    if env_img:
        cfg["searxngImageEngines"] = env_img
    return cfg


# ---- OCR（ADR-040） ----

async def get_ocr_masked() -> Dict[str, Any]:
    data = await _get_raw(OCR_KEY, OCR_DEFAULTS)
    return _mask(data, OCR_SECRETS)


async def save_ocr(incoming: Dict[str, Any]) -> Dict[str, Any]:
    current = await _get_raw(OCR_KEY, OCR_DEFAULTS)
    merged = _merge_incoming(current, incoming, OCR_DEFAULTS, OCR_SECRETS)
    await _save_raw(OCR_KEY, merged)
    return _mask(merged, OCR_SECRETS)


async def get_ocr_config() -> Dict[str, Any]:
    """运行时用：返回含明文密钥的完整 OCR 配置。"""
    return await _get_raw(OCR_KEY, OCR_DEFAULTS)


async def resolve_secret(key: str, field: str, incoming_value: str) -> str:
    """测试连通性时用：提交值非空则用提交值，否则回退库中已存密钥。"""
    if str(incoming_value or "").strip():
        return incoming_value
    defaults = WEB_SEARCH_DEFAULTS if key == WEB_SEARCH_KEY else OCR_DEFAULTS
    stored = await _get_raw(key, defaults)
    return str(stored.get(field) or "")
