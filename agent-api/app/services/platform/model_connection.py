"""对话模型连接配置（OpenAI 兼容端点），密钥 Fernet 密文入库。

两层配置：
- **平台默认**（config_key = ``model_connection``）：管理员在「管理配置 → 对话模型」里
  配的那份，对**所有登录用户**生效。产品要求「管理员和用户登录进去都可以使用智能体」，
  校园学生不该需要自带 API Key——所以这份配置不能再按 user_id 哈希只对管理员自己生效。
- **个人覆盖**（config_key = ``user_model_connection:<sha256(user_id)[:40]>``）：用户在
  「模型配置」页**可选**填写自己的 Key，优先于平台默认；不填就用平台默认。

历史与迁移：早期版本把管理员配的那份存成 ``model_connection:<hash(user_id)>``，实际只
对管理员自己生效（新注册用户进主对话直接 403「未分配 API Key」）。首次读取平台默认时
若还没有平台级记录、而旧的按用户记录存在，就把 enabled 的那条迁成平台默认
（幂等、记日志；旧行**保留不删**，便于回滚到旧版本）。个人覆盖故意换了一个新前缀，
不复用旧前缀：否则管理员自己那条旧记录会变成「管理员的个人覆盖」，之后在管理页改了
平台模型，管理员自己却仍被旧记录钉在老模型上，和学生看到的不一致。
"""
import hashlib
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.services.connectors.crypto import decrypt_secret, encrypt_secret
from app.services.platform.platform_config_service import _get_raw, _list_raw_by_prefix, _save_raw

logger = logging.getLogger(__name__)

DEFAULTS = {"base_url": "", "model": "", "api_key_cipher": "", "enabled": False}

# PlatformConfig.config_key 最长 64 字符：前缀 + 40 位哈希要放得下。
PLATFORM_KEY = "model_connection"
LEGACY_PREFIX = "model_connection:"
USER_PREFIX = "user_model_connection:"

# 进程内只做一次迁移检查；失败时保持 False，下次读取再试（不能让一次 DB 抖动永久跳过迁移）。
_migration_checked = False


class ConnectionInput(BaseModel):
    base_url: str = Field(max_length=2048)
    model: str = Field(max_length=255)
    api_key: str = Field(default="", max_length=8192)
    enabled: bool = True


def normalize(body: ConnectionInput) -> dict:
    base = body.base_url.strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/models"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
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


def _user_hash(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()[:40]


def user_config_key(user_id: str) -> str:
    """个人覆盖的存储键。"""
    return USER_PREFIX + _user_hash(user_id)


def legacy_config_key(user_id: str) -> str:
    """旧版本按用户哈希的存储键——只在迁移/测试里用，运行时不再从它读配置。"""
    return LEGACY_PREFIX + _user_hash(user_id)


def public(data: dict) -> dict:
    return {k: data[k] for k in ("base_url", "model", "enabled")} | {"has_api_key": bool(data["api_key_cipher"])}


def _usable(data: dict) -> bool:
    return bool(data.get("enabled")) and bool(data.get("api_key_cipher"))


def _runtime(data: dict) -> Optional[dict]:
    if not _usable(data):
        return None
    return {"base_url": data["base_url"], "model": data["model"], "api_key": decrypt_secret(data["api_key_cipher"])}


# ---- 旧记录迁移 ----

def pick_legacy_row(rows: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """从旧的按用户记录里挑出要升格为平台默认的那条（纯函数，便于测试）。

    只认 enabled 且有密钥的行；多条都满足时取 config_key 最小的一条并告警——旧版本只有
    管理员能写这张表，实际最多一条，这里只是别让不确定性变成随机行为。
    """
    candidates = sorted(
        (key, data) for key, data in rows.items()
        if key.startswith(LEGACY_PREFIX) and isinstance(data, dict) and _usable(data)
    )
    if not candidates:
        return None
    if len(candidates) > 1:
        logger.warning("对话模型旧配置有 %d 条 enabled 记录，迁移取 %s", len(candidates), candidates[0][0])
    data = dict(DEFAULTS)
    data.update({k: v for k, v in candidates[0][1].items() if k in DEFAULTS})
    return data


async def migrate_legacy_to_platform() -> bool:
    """旧「管理员按用户哈希」记录 → 平台级单记录。幂等：平台级已存在就什么都不做。

    返回是否真的写入了平台级记录。旧行保留不删（回滚旧版本时它仍可用）。
    """
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
    await _save_raw(PLATFORM_KEY, legacy)
    _migration_checked = True
    logger.info("对话模型配置已从旧的按用户记录迁移为平台默认：model=%s base_url=%s（旧行保留）",
                legacy["model"], legacy["base_url"])
    return True


async def ensure_migrated() -> None:
    if _migration_checked:
        return
    try:
        await migrate_legacy_to_platform()
    except Exception:  # noqa: BLE001 - 迁移失败不能拖垮读取；下次读取再试
        logger.warning("对话模型配置迁移失败，本次跳过", exc_info=True)


# ---- 读 ----

async def read_platform() -> dict:
    await ensure_migrated()
    return await _get_raw(PLATFORM_KEY, DEFAULTS)


async def read_user(user_id: str) -> dict:
    return await _get_raw(user_config_key(user_id), DEFAULTS)


# ---- 写 ----

def _prepare(previous: dict, body: ConnectionInput) -> dict:
    data = normalize(body)
    key = body.api_key.strip()
    # 改了地址却没重填密钥：绝不把已存的凭据悄悄发给新主机。
    if not key and previous["api_key_cipher"] and data["base_url"] != previous["base_url"]:
        raise HTTPException(422, "修改请求地址后，请重新输入 API Key")
    data["api_key_cipher"] = encrypt_secret(key) if key else previous["api_key_cipher"]
    if not data["api_key_cipher"]:
        raise HTTPException(422, "请填写 API Key")
    return data


async def prepare_platform(body: ConnectionInput) -> dict:
    return _prepare(await read_platform(), body)


async def prepare_user(user_id: str, body: ConnectionInput) -> dict:
    return _prepare(await read_user(user_id), body)


async def save_platform(body: ConnectionInput) -> dict:
    data = await prepare_platform(body)
    await _save_raw(PLATFORM_KEY, data)
    return public(data)


async def save_user(user_id: str, body: ConnectionInput) -> dict:
    data = await prepare_user(user_id, body)
    await _save_raw(user_config_key(user_id), data)
    return public(data)


# ---- 运行时 ----

async def runtime_platform() -> Optional[dict]:
    """平台默认连接（enabled 且有密钥才算），否则 None。"""
    return _runtime(await read_platform())


async def runtime_user(user_id: str) -> Optional[dict]:
    """用户个人覆盖连接（enabled 且有密钥才算），否则 None。"""
    return _runtime(await read_user(user_id))
