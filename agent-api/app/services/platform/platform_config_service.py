"""平台功能配置（键值 JSON）读写服务。

承载联网搜索（ADR-037）与 OCR（ADR-040）的管理端配置，存于 `agent_platform_config.config_json`，
仅管理员经受保护的路由读写；返回前端时脱敏（密钥置空 + `secrets_set` 标记「已配置」），
保存时空密钥字段保留库中原值。

密钥字段（WEB_SEARCH_SECRETS / OCR_SECRETS）以 Fernet 密文入库（connectors.crypto），与对话
模型、重排模型、embedding 的密钥同一做法：此前这些字段是明文躺在 config_json 里，一条
SELECT 或一份库备份就能带走 Serper/Tavily/Firecrawl 等付费 key。读取处解密，运行时消费方
（get_web_search_config / get_ocr_config）拿到的仍是明文；存量明文由启动迁移
（main._encrypt_platform_config_secrets → migrate_plaintext_secrets）原地加密。
"""
import json
import logging
from typing import Any, Dict, Iterable, Optional, Tuple

from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session
from app.models import PlatformConfig
from app.services.connectors.crypto import (
    CIPHER_PREFIX,
    decrypt_secret,
    encrypt_secret,
    is_cipher_text,
)

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
    # 2026-09-19 用户拍板「OCR 不需要，我们用的是多模态模型」：缺省即启用 multimodal_model，
    # 视觉模型自动取平台对话模型连接（document_parse_service._resolve_vision_runtime），
    # 管理页的「图片识别」tab 已去掉；从未保存过 ocr 行的部署不需要任何配置就能解析扫描件。
    # 显式关闭（enabled=False / strategy=none）与自建端点（custom_endpoint）仍可经 API 配。
    "enabled": True,
    "strategy": "multimodal_model",  # none | custom_endpoint | multimodal_model
    # ---- custom_endpoint 策略 ----
    "endpointUrl": "",
    "apiKey": "",
    # ---- multimodal_model 策略（视觉模型看图→回传内容给主对话模型）----
    # 三者都填才直连该独立端点；有任一缺省则整体回落到平台对话模型（不做半套拼接）。
    "model": "",                   # 视觉模型名，如 qwen-vl-max / glm-4v / gpt-4o
    "visionBaseUrl": "",           # 如 https://dashscope.aliyuncs.com/compatible-mode/v1
    "visionApiKey": "",
    # 可选：自定义视觉提示词。留空用默认（完整描述图片内容+转录文字，便于主模型据此作答）。
    "visionPrompt": "",
}
OCR_SECRETS = {"apiKey", "visionApiKey"}


async def _load_stored(key: str) -> Optional[Dict[str, Any]]:
    """读某一行的 config_json 原样解析成 dict：行不存在 / JSON 坏 / 不是对象 → None。

    与 _get_raw 的区别是不补默认值、不裁未知字段：启动迁移要把密钥字段加密后**整行原样**
    写回，多补一个默认字段或丢一个未知字段都是迁移不该有的副作用。
    """
    async with async_session() as session:
        row = await session.get(PlatformConfig, key)
    if not row or not row.config_json:
        return None
    try:
        parsed = json.loads(row.config_json)
    except (ValueError, TypeError):
        logger.warning("平台配置 %s 的 JSON 解析失败，回退默认值", key)
        return None
    return parsed if isinstance(parsed, dict) else None


async def _get_raw(key: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
    """读取某个功能域的完整配置，缺省字段以默认值补齐。

    密钥字段按库中原样返回（本模块两个功能域为 Fernet 密文，须再经 _decrypt_secrets；
    rerank_service / model_connection 复用本函数读各自的 api_key_cipher 字段，同样自行解密）。
    """
    stored = await _load_stored(key) or {}
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


async def _list_raw_by_prefix(prefix: str) -> Dict[str, Dict[str, Any]]:
    """按 config_key 前缀列出配置行（原样、不补默认值）。

    给对话模型配置的「旧按用户记录 → 平台默认」迁移用：迁移要知道平台级那行**存不存在**
    （_get_raw 会用默认值补齐，分不清「没有」和「空」），还要枚举旧的哈希后缀行。
    解析失败的行跳过并告警，不让一行坏 JSON 挡住整个迁移。
    """
    async with async_session() as session:
        rows = (
            await session.execute(
                select(PlatformConfig).where(PlatformConfig.config_key.like(prefix + "%"))
            )
        ).scalars().all()
    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        try:
            parsed = json.loads(row.config_json or "")
        except (ValueError, TypeError):
            logger.warning("平台配置 %s 的 JSON 解析失败，按前缀枚举时跳过", row.config_key)
            continue
        if isinstance(parsed, dict):
            result[row.config_key] = parsed
    return result


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


# ---- 密钥字段的密文存取 ----
#
# 「这个值是不是密文」的判据在 connectors.crypto.is_cipher_text（gAAAA 前缀 **且** 当前密钥能
# 解开）。前缀对但解不开的（换过 CONNECTOR_SECRET_KEY / 被篡改）单独归为「解不开」：读取当作
# 未配置——把 Fernet token 当 key 送出去只会换来一个让人误以为 key 错了的 401，不如页面显示
# 无 key 让管理员重填覆盖；迁移时跳过不覆盖——再加密一层或写空都会让管理员连「原来填过」的
# 线索都没了。明文（迁移尚未跑过）读取原样放行，保证迁移前后联网搜索/OCR 不中断。

SECRET_EMPTY = "empty"
SECRET_PLAIN = "plain"                  # 明文（尚未迁移）
SECRET_CIPHER = "cipher"                # 当前密钥能解开的 Fernet 密文
SECRET_UNDECRYPTABLE = "undecryptable"  # 长得像密文但解不开

# 「明文尚未迁移」「密文解不开」的告警按 (类别, 配置键, 字段) 只报一次：每次检索都刷会淹没日志
_secret_warned: set = set()


def _classify_secret(value: Any) -> str:
    text = str(value or "")
    if not text:
        return SECRET_EMPTY
    if is_cipher_text(text):
        return SECRET_CIPHER
    if text.startswith(CIPHER_PREFIX):
        return SECRET_UNDECRYPTABLE
    return SECRET_PLAIN


def _warn_once(kind: str, key: str, field: str, message: str, *args) -> None:
    marker = (kind, key, field)
    if marker in _secret_warned:
        return
    _secret_warned.add(marker)
    logger.warning(message, *args)


def encrypt_plaintext_secrets(
    stored: Dict[str, Any], secret_fields: Iterable[str], *, key: str = "",
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """把 stored 里仍是明文的密钥字段改写成 Fernet 密文，返回 (新 dict, 各类计数)。

    纯函数、不碰库：既是 _save_raw 前的加密步骤，也是启动迁移的核心（便于拿内存 dict 测）。
    幂等：已是密文的原样保留，第二次跑 migrated=0；空的仍是空（"" 表示未配置，不加密）；
    解不开的伪密文只告警不覆盖。加密失败（密钥不可用）直接抛 ConnectorCryptoError——保存路径
    宁可 500 也不能悄悄把明文写回库里，迁移路径由调用方兜住。
    """
    stats = {"migrated": 0, "already": 0, "skipped": 0, "empty": 0}
    result = dict(stored)
    for field in secret_fields:
        value = str(stored.get(field) or "")
        kind = _classify_secret(value)
        if kind == SECRET_EMPTY:
            stats["empty"] += 1
            continue
        if kind == SECRET_CIPHER:
            stats["already"] += 1
            continue
        if kind == SECRET_UNDECRYPTABLE:
            stats["skipped"] += 1
            _warn_once(
                kind, key, field,
                "平台配置 %s.%s 的密文无法解密（CONNECTOR_SECRET_KEY 可能已更换），迁移跳过不覆盖；"
                "请在管理页重新填写", key, field,
            )
            continue
        result[field] = encrypt_secret(value)
        stats["migrated"] += 1
    return result, stats


def _encrypt_secrets(data: Dict[str, Any], secret_fields: Iterable[str], *, key: str = "") -> Dict[str, Any]:
    """_save_raw 前：把明文密钥字段加密（已是密文的不动）。要在 _merge_incoming 之后调——
    合并「页面传空串 = 保持原密钥」是明文层的语义，先加密再合并会把原密钥比成密文。"""
    return encrypt_plaintext_secrets(data, secret_fields, key=key)[0]


def _decrypt_secrets(data: Dict[str, Any], secret_fields: Iterable[str], *, key: str = "") -> Dict[str, Any]:
    """_get_raw 后：密文 → 明文；明文原样放行并告警一次；解不开的伪密文 → 空串并告警一次。"""
    result = dict(data)
    for field in secret_fields:
        value = str(data.get(field) or "")
        kind = _classify_secret(value)
        if kind == SECRET_EMPTY:
            result[field] = ""
            continue
        if kind == SECRET_CIPHER:
            result[field] = decrypt_secret(value)
            continue
        if kind == SECRET_UNDECRYPTABLE:
            _warn_once(
                kind, key, field,
                "平台配置 %s.%s 的密文无法解密（CONNECTOR_SECRET_KEY 可能已更换），按未配置处理；"
                "请在管理页重新填写", key, field,
            )
            result[field] = ""
            continue
        _warn_once(
            kind, key, field,
            "平台配置 %s.%s 仍是明文（启动迁移尚未执行），本次按明文使用", key, field,
        )
        result[field] = value
    return result


async def _get_plain(key: str, defaults: Dict[str, Any], secret_fields: Iterable[str]) -> Dict[str, Any]:
    """读某功能域的完整配置，密钥字段已解成明文（只活在进程内，不出接口层——出去前必经 _mask）。"""
    return _decrypt_secrets(await _get_raw(key, defaults), secret_fields, key=key)


_SECRET_DOMAINS: Tuple[Tuple[str, frozenset], ...] = (
    (WEB_SEARCH_KEY, frozenset(WEB_SEARCH_SECRETS)),
    (OCR_KEY, frozenset(OCR_SECRETS)),
)


async def migrate_plaintext_secrets() -> Dict[str, int]:
    """启动迁移：把 web_search / ocr 两行里仍是明文的密钥字段原地加密回写（幂等）。

    只碰这两行、只改密钥字段、整行其余内容原样保留；rerank_model / model_connection* 那些
    本来就是 api_key_cipher 的行不在此列。行不存在（从未在后台保存过）就不凭空造一行——
    没有密钥可迁。单行失败只告警，继续下一行，最终由 main 的调用方决定怎么记日志。
    """
    total = {"migrated": 0, "already": 0, "skipped": 0, "empty": 0, "rows": 0}
    for key, secret_fields in _SECRET_DOMAINS:
        try:
            stored = await _load_stored(key)
            if stored is None:
                continue
            total["rows"] += 1
            updated, stats = encrypt_plaintext_secrets(stored, secret_fields, key=key)
            if stats["migrated"]:
                await _save_raw(key, updated)
            for name in ("migrated", "already", "skipped", "empty"):
                total[name] += stats[name]
        except Exception:  # noqa: BLE001
            logger.warning("平台配置 %s 的密钥密文迁移失败，跳过（下次启动重试）", key, exc_info=True)
    return total


# ---- 联网搜索（ADR-037） ----

async def get_web_search_masked() -> Dict[str, Any]:
    data = _normalize_provider_pool(await _get_plain(WEB_SEARCH_KEY, WEB_SEARCH_DEFAULTS, WEB_SEARCH_SECRETS))
    return _mask(data, WEB_SEARCH_SECRETS)


async def save_web_search(incoming: Dict[str, Any]) -> Dict[str, Any]:
    # 合并在明文层：页面传空串表示「保持原密钥」，得先解出原密钥再合并，最后整体加密入库
    current = await _get_plain(WEB_SEARCH_KEY, WEB_SEARCH_DEFAULTS, WEB_SEARCH_SECRETS)
    merged = _normalize_provider_pool(
        _merge_incoming(current, incoming, WEB_SEARCH_DEFAULTS, WEB_SEARCH_SECRETS)
    )
    await _save_raw(WEB_SEARCH_KEY, _encrypt_secrets(merged, WEB_SEARCH_SECRETS, key=WEB_SEARCH_KEY))
    return _mask(merged, WEB_SEARCH_SECRETS)


async def get_web_search_config() -> Dict[str, Any]:
    """运行时用：返回含明文密钥的完整配置（供联网搜索工具消费）。"""
    # env 只作为缺省值（已在 WEB_SEARCH_DEFAULTS 里播种），管理页保存过的值必须生效。
    # 此前这里让非空的 env 反过来覆盖库里的值：compose 给了 ENGINES=bing，管理员在页面上
    # 改成别的引擎、保存成功、检索却仍然打 bing——「配置改了不生效」正是要杜绝的交互。
    return _normalize_provider_pool(await _get_plain(WEB_SEARCH_KEY, WEB_SEARCH_DEFAULTS, WEB_SEARCH_SECRETS))


# ---- OCR（ADR-040） ----

async def get_ocr_masked() -> Dict[str, Any]:
    data = await _get_plain(OCR_KEY, OCR_DEFAULTS, OCR_SECRETS)
    return _mask(data, OCR_SECRETS)


async def save_ocr(incoming: Dict[str, Any]) -> Dict[str, Any]:
    # 同 save_web_search：明文层合并（空串 = 保持原密钥），再加密入库
    current = await _get_plain(OCR_KEY, OCR_DEFAULTS, OCR_SECRETS)
    merged = _merge_incoming(current, incoming, OCR_DEFAULTS, OCR_SECRETS)
    await _save_raw(OCR_KEY, _encrypt_secrets(merged, OCR_SECRETS, key=OCR_KEY))
    return _mask(merged, OCR_SECRETS)


async def get_ocr_config() -> Dict[str, Any]:
    """运行时用：返回含明文密钥的完整 OCR 配置。"""
    return await _get_plain(OCR_KEY, OCR_DEFAULTS, OCR_SECRETS)


async def resolve_secret(key: str, field: str, incoming_value: str) -> str:
    """测试连通性时用：提交值非空则用提交值，否则回退库中已存密钥。"""
    if str(incoming_value or "").strip():
        return incoming_value
    if key == WEB_SEARCH_KEY:
        stored = await _get_plain(key, WEB_SEARCH_DEFAULTS, WEB_SEARCH_SECRETS)
    else:
        stored = await _get_plain(key, OCR_DEFAULTS, OCR_SECRETS)
    return str(stored.get(field) or "")
