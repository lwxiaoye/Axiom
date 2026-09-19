"""对话模型连接配置（OpenAI 兼容端点），密钥 Fernet 密文入库。

两层配置：
- **平台名册**（config_key = ``model_connection``）：管理员在「管理配置 → 对话模型」里
  添加的多条独立模型，每条有自己的名称、OpenAI / Anthropic 地址、模型 ID 和密钥。
  对所有登录用户生效；学生不该需要自带 API Key。
- **个人覆盖**（config_key = ``user_model_connection:<sha256(user_id)[:40]>``）：用户在
  「模型配置」页可选填写自己的一条连接，优先于平台名册。

历史：早期只有一条平台连接（``base_url`` + ``model``）；再早则按 user_id 哈希只对管理员
自己生效。读取时把旧形状收成名册条目，不改库；管理员再保存才写成新形状。
"""
import hashlib
import logging
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.services.connectors.crypto import decrypt_secret, encrypt_secret
from app.services.platform.platform_config_service import _get_raw, _list_raw_by_prefix, _save_raw

logger = logging.getLogger(__name__)

MAX_MODELS = 32
ENTRY_DEFAULTS = {
    "id": "",
    "name": "",
    "base_url": "",
    "anthropic_base_url": "",
    "model": "",
    "api_key_cipher": "",
    "enabled": True,
    "latency_ms": None,
}
ROSTER_DEFAULTS = {"enabled": True, "default_id": "", "entries": []}
# 个人覆盖仍是单条连接。
CONNECTION_DEFAULTS = {"base_url": "", "model": "", "api_key_cipher": "", "enabled": False}

PLATFORM_KEY = "model_connection"
LEGACY_PREFIX = "model_connection:"
USER_PREFIX = "user_model_connection:"

_migration_checked = False


class ConnectionInput(BaseModel):
    """个人覆盖（以及单条探测）用的输入。"""
    base_url: str = Field(default="", max_length=2048)
    anthropic_base_url: str = Field(default="", max_length=2048)
    model: str = Field(default="", max_length=255)
    name: str = Field(default="", max_length=255)
    api_key: str = Field(default="", max_length=8192)
    enabled: bool = True


class EntryInput(BaseModel):
    id: str = Field(default="", max_length=64)
    name: str = Field(default="", max_length=255)
    base_url: str = Field(default="", max_length=2048)
    anthropic_base_url: str = Field(default="", max_length=2048)
    model: str = Field(default="", max_length=255)
    api_key: str = Field(default="", max_length=8192)
    enabled: bool = True


class RosterInput(BaseModel):
    enabled: bool = True
    default_id: str = Field(default="", max_length=64)
    entries: List[EntryInput] = Field(default_factory=list)


def _check_key(api_key: str) -> None:
    if any(ord(c) < 32 or ord(c) == 127 for c in api_key):
        raise HTTPException(422, "API Key 不能包含换行或控制字符")


def normalize_url(raw: str, *, required: bool) -> str:
    base = (raw or "").strip().rstrip("/")
    if not base:
        if required:
            raise HTTPException(422, "请填写 API 地址")
        return ""
    for suffix in ("/chat/completions", "/responses", "/models"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    try:
        url = urlsplit(base)
        valid = url.scheme in {"http", "https"} and url.hostname and url.port != 0
    except ValueError:
        valid = False
        url = None
    if not valid or url.username or url.password or url.query or url.fragment or any(c.isspace() for c in base):
        raise HTTPException(422, "请输入完整的 HTTP(S) API 地址，不含账号、查询参数或片段")
    return base


def _user_hash(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()[:40]


def user_config_key(user_id: str) -> str:
    return USER_PREFIX + _user_hash(user_id)


def legacy_config_key(user_id: str) -> str:
    return LEGACY_PREFIX + _user_hash(user_id)


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _usable_entry(entry: dict) -> bool:
    return bool(entry.get("enabled", True)) and bool(entry.get("api_key_cipher")) and bool(entry.get("model")) and bool(
        entry.get("base_url") or entry.get("anthropic_base_url")
    )


def runtime_base_url(entry: dict) -> str:
    """对话目前走 OpenAI 兼容 Chat Completions；有 OpenAI 地址用它，否则用 Anthropic 地址。"""
    return str(entry.get("base_url") or entry.get("anthropic_base_url") or "").strip()


def public_entry(entry: dict) -> dict:
    return {
        "id": entry.get("id") or "",
        "name": entry.get("name") or entry.get("model") or "",
        "base_url": entry.get("base_url") or "",
        "anthropic_base_url": entry.get("anthropic_base_url") or "",
        "model": entry.get("model") or "",
        "enabled": bool(entry.get("enabled", True)),
        "has_api_key": bool(entry.get("api_key_cipher")),
        "latency_ms": entry.get("latency_ms"),
    }


def public_roster(data: dict) -> dict:
    roster = coerce_roster(data)
    return {
        "enabled": bool(roster.get("enabled", True)),
        "default_id": roster.get("default_id") or "",
        "entries": [public_entry(item) for item in roster.get("entries") or []],
    }


def public(data: dict) -> dict:
    """个人覆盖的对外形状；平台名册请用 public_roster。"""
    return {
        "base_url": data.get("base_url") or "",
        "anthropic_base_url": data.get("anthropic_base_url") or "",
        "model": data.get("model") or "",
        "name": data.get("name") or "",
        "enabled": bool(data.get("enabled")),
        "has_api_key": bool(data.get("api_key_cipher")),
    }


def models_of(data: dict) -> List[str]:
    """平台名册里可用的模型 ID（给摘要/校园下拉用）。"""
    names: List[str] = []
    seen = set()
    for entry in coerce_roster(data).get("entries") or []:
        model = str(entry.get("model") or "").strip()
        if model and model not in seen and _usable_entry(entry):
            seen.add(model)
            names.append(model)
    return names


def coerce_roster(data: dict) -> dict:
    """把库里可能出现的几种旧形状收成 {enabled, default_id, entries}。不写库。"""
    raw_entries = data.get("entries")
    if isinstance(raw_entries, list) and raw_entries:
        entries = []
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            entry = dict(ENTRY_DEFAULTS)
            entry.update({k: item.get(k, entry[k]) for k in ENTRY_DEFAULTS})
            if not entry["id"]:
                entry["id"] = _stable_id(entry.get("base_url") or "", entry.get("model") or "", str(len(entries)))
            entries.append(entry)
        default_id = str(data.get("default_id") or "")
        if default_id not in {item["id"] for item in entries}:
            default_id = entries[0]["id"] if entries else ""
        enabled = data.get("enabled")
        return {
            "enabled": True if enabled is None else bool(enabled),
            "default_id": default_id,
            "entries": entries,
        }

    names: List[str] = []
    seen = set()
    raw_models = data.get("models")
    if isinstance(raw_models, list):
        for item in raw_models:
            name = str(item or "").strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    model = str(data.get("model") or "").strip()
    if model and model not in seen:
        names.insert(0, model)
    entries = []
    cipher = data.get("api_key_cipher") or ""
    base = data.get("base_url") or ""
    anthropic = data.get("anthropic_base_url") or ""
    for index, name in enumerate(names):
        entries.append({
            **ENTRY_DEFAULTS,
            "id": _stable_id(base, name, str(index)),
            "name": name,
            "base_url": base,
            "anthropic_base_url": anthropic,
            "model": name,
            "api_key_cipher": cipher,
            "enabled": bool(data.get("enabled", True)),
        })
    return {
        "enabled": bool(data.get("enabled", True)) if names else bool(data.get("enabled", False)),
        "default_id": entries[0]["id"] if entries else "",
        "entries": entries,
    }


def _entry_runtime(entry: dict) -> Optional[dict]:
    if not _usable_entry(entry):
        return None
    base = runtime_base_url(entry)
    if not base:
        return None
    return {
        "id": entry["id"],
        "name": entry.get("name") or entry["model"],
        "base_url": base,
        "anthropic_base_url": entry.get("anthropic_base_url") or "",
        "model": entry["model"],
        "api_key": decrypt_secret(entry["api_key_cipher"]),
        "latency_ms": entry.get("latency_ms"),
    }


def bind_selected(model_id: str) -> None:
    """按用户选中的模型 ID，把当前请求的连接切到名册里对应的那一条。"""
    from app.core.model_endpoint import bind_model_connection, get_model_connection
    connection = get_model_connection()
    if not connection:
        return
    target = str(model_id or "").strip()
    if not target:
        return
    roster = connection.get("roster") or []
    entry = next((item for item in roster if item.get("model") == target or item.get("id") == target), None)
    if not entry:
        return
    bind_model_connection({
        "base_url": entry["base_url"],
        "model": entry["model"],
        "api_key": entry["api_key"],
        "entry_id": entry.get("id") or "",
        "roster": roster,
    })


def normalize_connection(body: ConnectionInput) -> dict:
    openai_url = normalize_url(body.base_url, required=False)
    anthropic_url = normalize_url(body.anthropic_base_url, required=False)
    if not openai_url and not anthropic_url:
        raise HTTPException(422, "请填写 OpenAI 或 Anthropic API 地址")
    model = body.model.strip()
    if not model:
        raise HTTPException(422, "请填写模型 ID")
    if len(model) > 255 or any(ord(c) < 32 or ord(c) == 127 for c in model):
        raise HTTPException(422, "模型 ID 不合法")
    _check_key(body.api_key)
    name = (body.name or "").strip() or model
    return {
        "base_url": openai_url,
        "anthropic_base_url": anthropic_url,
        "model": model,
        "name": name,
        "enabled": body.enabled,
    }


def normalize_entry(body: EntryInput, *, previous: Optional[dict] = None) -> dict:
    openai_url = normalize_url(body.base_url, required=False)
    anthropic_url = normalize_url(body.anthropic_base_url, required=False)
    if not openai_url and not anthropic_url:
        raise HTTPException(422, "请填写 OpenAI 或 Anthropic API 地址")
    model = body.model.strip()
    if not model:
        raise HTTPException(422, "请填写模型 ID")
    if any(ord(c) < 32 or ord(c) == 127 for c in model):
        raise HTTPException(422, "模型 ID 不能包含换行或控制字符")
    _check_key(body.api_key)
    name = (body.name or "").strip() or model
    entry_id = (body.id or "").strip() or (previous or {}).get("id") or uuid.uuid4().hex[:16]
    key = body.api_key.strip()
    previous = previous or {}
    previous_cipher = previous.get("api_key_cipher") or ""
    previous_openai = previous.get("base_url") or ""
    previous_anthropic = previous.get("anthropic_base_url") or ""
    if not key and previous_cipher and (
        (openai_url and openai_url != previous_openai) or (anthropic_url and anthropic_url != previous_anthropic)
    ):
        raise HTTPException(422, "修改请求地址后，请重新输入 API Key")
    cipher = encrypt_secret(key) if key else previous_cipher
    if not cipher:
        raise HTTPException(422, "请填写 API Key")
    return {
        "id": entry_id,
        "name": name,
        "base_url": openai_url,
        "anthropic_base_url": anthropic_url,
        "model": model,
        "api_key_cipher": cipher,
        "enabled": body.enabled,
        "latency_ms": previous.get("latency_ms"),
    }


# ---- 旧记录迁移（按用户哈希 → 平台级）----

def _usable(data: dict) -> bool:
    if isinstance(data.get("entries"), list):
        return any(_usable_entry(item) for item in data["entries"] if isinstance(item, dict))
    return bool(data.get("enabled")) and bool(data.get("api_key_cipher"))


def pick_legacy_row(rows: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    candidates = sorted(
        (key, data) for key, data in rows.items()
        if key.startswith(LEGACY_PREFIX) and isinstance(data, dict) and _usable(data)
    )
    if not candidates:
        return None
    if len(candidates) > 1:
        logger.warning("对话模型旧配置有 %d 条 enabled 记录，迁移取 %s", len(candidates), candidates[0][0])
    return dict(candidates[0][1])


async def migrate_legacy_to_platform() -> bool:
    global _migration_checked
    rows = await _list_raw_by_prefix(PLATFORM_KEY)
    if PLATFORM_KEY in rows:
        _migration_checked = True
        return False
    legacy = pick_legacy_row(rows)
    if legacy is None:
        _migration_checked = True
        if rows:
            logger.info("对话模型旧配置存在 %d 行但没有 enabled 的，未迁移；请管理员在管理配置里重新保存", len(rows))
        return False
    await _save_raw(PLATFORM_KEY, coerce_roster(legacy))
    _migration_checked = True
    logger.info("对话模型配置已从旧的按用户记录迁移为平台名册（旧行保留）")
    return True


async def ensure_migrated() -> None:
    if _migration_checked:
        return
    try:
        await migrate_legacy_to_platform()
    except Exception:  # noqa: BLE001
        logger.warning("对话模型配置迁移失败，本次跳过", exc_info=True)


async def read_platform() -> dict:
    await ensure_migrated()
    return coerce_roster(await _get_raw(PLATFORM_KEY, ROSTER_DEFAULTS))


async def read_user(user_id: str) -> dict:
    data = await _get_raw(user_config_key(user_id), CONNECTION_DEFAULTS)
    return {
        "base_url": data.get("base_url") or "",
        "anthropic_base_url": data.get("anthropic_base_url") or "",
        "model": data.get("model") or "",
        "name": data.get("name") or "",
        "api_key_cipher": data.get("api_key_cipher") or "",
        "enabled": bool(data.get("enabled")),
    }


def _prepare_connection(previous: dict, body: ConnectionInput) -> dict:
    data = normalize_connection(body)
    key = body.api_key.strip()
    if not key and previous.get("api_key_cipher") and data["base_url"] != (previous.get("base_url") or ""):
        raise HTTPException(422, "修改请求地址后，请重新输入 API Key")
    data["api_key_cipher"] = encrypt_secret(key) if key else previous.get("api_key_cipher") or ""
    if not data["api_key_cipher"]:
        raise HTTPException(422, "请填写 API Key")
    return data


async def prepare_user(user_id: str, body: ConnectionInput) -> dict:
    return _prepare_connection(await read_user(user_id), body)


async def save_user(user_id: str, body: ConnectionInput) -> dict:
    data = await prepare_user(user_id, body)
    await _save_raw(user_config_key(user_id), data)
    return public(data)


async def prepare_entry(body: EntryInput) -> dict:
    roster = await read_platform()
    previous = next((item for item in roster["entries"] if item["id"] == (body.id or "").strip()), None)
    return normalize_entry(body, previous=previous)


async def save_platform(body: RosterInput) -> dict:
    if len(body.entries) > MAX_MODELS:
        raise HTTPException(422, f"最多配置 {MAX_MODELS} 个模型")
    previous = await read_platform()
    previous_by_id = {item["id"]: item for item in previous["entries"]}
    entries: List[dict] = []
    seen_models = set()
    seen_ids = set()
    for item in body.entries:
        prepared = normalize_entry(item, previous=previous_by_id.get((item.id or "").strip()))
        if prepared["id"] in seen_ids:
            prepared["id"] = uuid.uuid4().hex[:16]
        if prepared["model"] in seen_models:
            raise HTTPException(422, f"模型 ID 重复：{prepared['model']}。不同来源请用不同的模型 ID，或先删掉旧的")
        seen_models.add(prepared["model"])
        seen_ids.add(prepared["id"])
        entries.append(prepared)
    default_id = (body.default_id or "").strip()
    if default_id not in seen_ids:
        default_id = entries[0]["id"] if entries else ""
    data = {"enabled": body.enabled, "default_id": default_id, "entries": entries}
    await _save_raw(PLATFORM_KEY, data)
    return public_roster(data)


async def save_latencies(latencies: Dict[str, Optional[int]]) -> dict:
    roster = await read_platform()
    changed = False
    for entry in roster["entries"]:
        if entry["id"] in latencies:
            entry["latency_ms"] = latencies[entry["id"]]
            changed = True
    if changed:
        await _save_raw(PLATFORM_KEY, roster)
    return public_roster(roster)


async def runtime_platform() -> Optional[dict]:
    roster = await read_platform()
    if not roster.get("enabled"):
        return None
    runtimes = []
    for entry in roster["entries"]:
        item = _entry_runtime(entry)
        if item:
            runtimes.append(item)
    if not runtimes:
        return None
    default_id = roster.get("default_id") or ""
    chosen = next((item for item in runtimes if item["id"] == default_id), runtimes[0])
    return {
        "base_url": chosen["base_url"],
        "model": chosen["model"],
        "api_key": chosen["api_key"],
        "entry_id": chosen["id"],
        "roster": runtimes,
    }


async def runtime_user(user_id: str) -> Optional[dict]:
    data = await read_user(user_id)
    if not data.get("enabled") or not data.get("api_key_cipher") or not data.get("model"):
        return None
    base = runtime_base_url(data)
    if not base:
        return None
    runtime = {
        "id": "personal",
        "name": data.get("name") or data["model"],
        "base_url": base,
        "anthropic_base_url": data.get("anthropic_base_url") or "",
        "model": data["model"],
        "api_key": decrypt_secret(data["api_key_cipher"]),
        "latency_ms": None,
    }
    return {
        "base_url": runtime["base_url"],
        "model": runtime["model"],
        "api_key": runtime["api_key"],
        "entry_id": "personal",
        "roster": [runtime],
    }
