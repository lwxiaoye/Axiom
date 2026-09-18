"""对话模型连接管理。

- ``/model-connection``（管理员）：平台默认，对所有登录用户生效——管理页「对话模型」tab。
- ``/model-connection/personal``（任意登录用户）：个人覆盖，可选——用户侧「模型配置」页。
  GET 额外带 ``platform`` 摘要（只有模型名和是否已配置，不带地址和密钥），页面据此提示
  「平台已提供默认模型 xxx」。

允许私网地址：本地模型服务是常见部署形态。
"""
import asyncio
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import UserContext, current_user, is_admin
from app.services.platform import model_connection as service
from app.services.connectors.crypto import decrypt_secret

router = APIRouter(prefix="/model-connection", tags=["model-connection"])
TEST_TIMEOUT_SECONDS = 15


def admin(user: UserContext = Depends(current_user)) -> UserContext:
    if not is_admin(user):
        raise HTTPException(403, "需要管理员权限")
    return user


async def _probe(data: dict) -> dict:
    """用准备好的（已合并密钥的）配置发一条最短消息，只回连通结论，不回显任何响应正文。"""
    started = time.monotonic()
    try:
        async with asyncio.timeout(TEST_TIMEOUT_SECONDS), httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5), follow_redirects=False, trust_env=False) as client:
            response = await client.post(
                data["base_url"] + "/chat/completions",
                headers={"Authorization": "Bearer " + decrypt_secret(data["api_key_cipher"])},
                json={"model": data["model"], "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 32, "stream": False},
            )
        if response.status_code != 200:
            hint = {401: "API Key 无效", 403: "没有模型访问权限", 404: "请检查 API 地址和模型名称", 429: "请求限流或额度不足"}.get(response.status_code, "模型服务返回错误")
            return {"success": False, "message": f"{hint}（HTTP {response.status_code}）"}
        payload = response.json()
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not choices or not isinstance(choices[0], dict) or not isinstance(choices[0].get("message"), dict):
            return {"success": False, "message": "服务响应不符合 Chat Completions 格式"}
        return {"success": True, "message": "连接成功，模型已响应", "latency_ms": round((time.monotonic() - started) * 1000)}
    except (httpx.TimeoutException, TimeoutError):
        return {"success": False, "message": "测试等待超时（最多 15 秒），请检查地址或稍后重试；超时不代表密钥无效"}
    except (httpx.HTTPError, ValueError, TypeError, IndexError, KeyError):
        return {"success": False, "message": "连接失败，请检查地址、网络及服务响应格式"}


# ---- 平台默认（管理员） ----

@router.get("")
async def get_connection(user: UserContext = Depends(admin)):
    return service.public(await service.read_platform())


@router.put("")
async def save_connection(body: service.ConnectionInput, user: UserContext = Depends(admin)):
    return await service.save_platform(body)


@router.post("/test")
async def test_connection(body: service.ConnectionInput, user: UserContext = Depends(admin)):
    return await _probe(await service.prepare_platform(body))


# ---- 个人覆盖（任意登录用户） ----

async def _platform_summary() -> dict:
    """给用户页看的平台默认摘要：只暴露「有没有」和模型名，地址和密钥不出管理端。"""
    platform = await service.read_platform()
    configured = bool(platform["enabled"] and platform["api_key_cipher"])
    return {"configured": configured, "model": platform["model"] if configured else ""}


@router.get("/personal")
async def get_personal_connection(user: UserContext = Depends(current_user)):
    return service.public(await service.read_user(user.user_id)) | {"platform": await _platform_summary()}


@router.put("/personal")
async def save_personal_connection(body: service.ConnectionInput, user: UserContext = Depends(current_user)):
    return await service.save_user(user.user_id, body) | {"platform": await _platform_summary()}


@router.post("/personal/test")
async def test_personal_connection(body: service.ConnectionInput, user: UserContext = Depends(current_user)):
    return await _probe(await service.prepare_user(user.user_id, body))
