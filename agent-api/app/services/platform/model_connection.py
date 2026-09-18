"""Per-account OpenAI-compatible chat configuration, encrypted at rest."""
import hashlib
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.services.connectors.crypto import decrypt_secret, encrypt_secret
from app.services.platform.platform_config_service import _get_raw, _save_raw

DEFAULTS = {"base_url": "", "model": "", "api_key_cipher": "", "enabled": False}


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


def config_key(user_id: str) -> str:
    # PlatformConfig.config_key is limited to 64 characters.
    return "model_connection:" + hashlib.sha256(user_id.encode()).hexdigest()[:40]


async def read(user_id: str) -> dict:
    return await _get_raw(config_key(user_id), DEFAULTS)


def public(data: dict) -> dict:
    return {k: data[k] for k in ("base_url", "model", "enabled")} | {"has_api_key": bool(data["api_key_cipher"])}


async def prepare(user_id: str, body: ConnectionInput) -> dict:
    data = normalize(body)
    previous = await read(user_id)
    key = body.api_key.strip()
    # Never silently send a saved credential to a newly entered host/path.
    if not key and previous["api_key_cipher"] and data["base_url"] != previous["base_url"]:
        raise HTTPException(422, "修改请求地址后，请重新输入 API Key")
    data["api_key_cipher"] = encrypt_secret(key) if key else previous["api_key_cipher"]
    if not data["api_key_cipher"]:
        raise HTTPException(422, "请填写 API Key")
    return data


async def save(user_id: str, body: ConnectionInput) -> dict:
    data = await prepare(user_id, body)
    await _save_raw(config_key(user_id), data)
    return public(data)


async def runtime(user_id: str):
    data = await read(user_id)
    if not data["enabled"] or not data["api_key_cipher"]:
        return None
    return {"base_url": data["base_url"], "model": data["model"], "api_key": decrypt_secret(data["api_key_cipher"])}
