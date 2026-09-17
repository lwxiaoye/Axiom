"""联网搜索（ADR-037）与 OCR（ADR-040）的管理端配置路由。

均为管理员专用。密钥返回前端时脱敏，保存时空密钥保留原值。`/test` 端点做尽力而为
的连通性探测。注意：这些探测在服务端向管理员填写的 URL 发起请求；由于配置只有管理员
可写、且自托管 SearXNG 常在内网，此处不套用面向普通用户的 SSRF 内网拦截。
"""
import logging
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException

from app.core.auth import UserContext, current_user, is_admin
from app.services.platform import platform_config_service as cfg

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/platform-config", tags=["platform-config"])


def _require_admin(user: UserContext) -> None:
    if not is_admin(user):
        raise HTTPException(403, "需要管理员权限")


# ---- 联网搜索 ----


@router.get("/web-search")
async def get_web_search(user: UserContext = Depends(current_user)):
    _require_admin(user)
    return await cfg.get_web_search_masked()


@router.put("/web-search")
async def save_web_search(
    body: Dict[str, Any] = Body(...),
    user: UserContext = Depends(current_user),
):
    _require_admin(user)
    return await cfg.save_web_search(body)


@router.post("/web-search/test")
async def test_web_search(
    body: Dict[str, Any] = Body(default_factory=dict),
    user: UserContext = Depends(current_user),
):
    _require_admin(user)
    provider = str(body.get("searchProvider") or "searxng")
    try:
        if provider == "deepseek-official":
            from app.services.knowledge.web_search_service import _stage_search_single
            from app.services.platform.key_service import key_service

            key = await key_service.get_user_key(user.user_id)
            if not key:
                return {"status": "failed", "message": "当前账号未分配模型 API Key"}
            config = await cfg.get_web_search_config()
            config.update({k: body[k] for k in ("deepseekModel", "deepseekMaxTokens") if k in body})
            config = cfg._normalize_provider_pool(config)
            config["deepseekApiKey"] = key
            rows, error = await _stage_search_single("DeepSeek Responses API web_search 官方文档", config, provider)
            if error or not rows:
                return {"status": "failed", "message": error or "未返回可用来源"}
            return {"status": "success", "message": f"DeepSeek 搜索完成，返回 {len(rows)} 个候选来源"}

        if provider == "searxng":
            base = str(body.get("searxngUrl") or "").strip().rstrip("/")
            if not base:
                return {"status": "failed", "message": "未填写 SearXNG 地址"}
            api_key = await cfg.resolve_secret(cfg.WEB_SEARCH_KEY, "searxngApiKey", body.get("searxngApiKey", ""))
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(
                    f"{base}/search",
                    params={"q": "test", "format": "json"},
                    headers=headers,
                )
            if resp.status_code != 200:
                return {"status": "failed", "message": f"SearXNG 返回 {resp.status_code}"}
            data = resp.json()
            count = len(data.get("results") or [])
            return {"status": "success", "message": f"连通成功，返回 {count} 条结果"}

        if provider == "serper":
            key = await cfg.resolve_secret(cfg.WEB_SEARCH_KEY, "serperApiKey", body.get("serperApiKey", ""))
            if not key:
                return {"status": "failed", "message": "未提供 Serper API Key"}
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": key, "Content-Type": "application/json"},
                    json={"q": "test", "num": 1},
                )
            if resp.status_code == 200:
                return {"status": "success", "message": "Serper 连通成功"}
            return {"status": "failed", "message": f"Serper 返回 {resp.status_code}"}

        if provider == "tavily":
            key = await cfg.resolve_secret(cfg.WEB_SEARCH_KEY, "tavilyApiKey", body.get("tavilyApiKey", ""))
            if not key:
                return {"status": "failed", "message": "未提供 Tavily API Key"}
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": key, "query": "test", "max_results": 1},
                )
            if resp.status_code == 200:
                return {"status": "success", "message": "Tavily 连通成功"}
            return {"status": "failed", "message": f"Tavily 返回 {resp.status_code}"}

        return {"status": "failed", "message": f"未知搜索提供方: {provider}"}
    except Exception as e:  # noqa: BLE001
        logger.warning("联网搜索连通性测试失败: %s", e)
        return {"status": "failed", "message": f"连通失败: {str(e)[:160]}"}


# ---- OCR ----

@router.get("/ocr")
async def get_ocr(user: UserContext = Depends(current_user)):
    _require_admin(user)
    return await cfg.get_ocr_masked()


@router.put("/ocr")
async def save_ocr(
    body: Dict[str, Any] = Body(...),
    user: UserContext = Depends(current_user),
):
    _require_admin(user)
    return await cfg.save_ocr(body)


@router.post("/ocr/test")
async def test_ocr(
    body: Dict[str, Any] = Body(default_factory=dict),
    user: UserContext = Depends(current_user),
):
    _require_admin(user)
    strategy = str(body.get("strategy") or "none")
    if strategy == "none":
        return {"status": "failed", "message": "OCR 策略为「关闭」，无需测试"}

    if strategy == "custom_endpoint":
        url = str(body.get("endpointUrl") or "").strip()
        if not url:
            return {"status": "failed", "message": "未填写 OCR 端点地址"}
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                resp = await client.get(url)
            return {"status": "success", "message": f"端点可达（HTTP {resp.status_code}）"}
        except Exception as e:  # noqa: BLE001
            logger.warning("OCR 端点连通性测试失败: %s", e)
            return {"status": "failed", "message": f"端点不可达: {str(e)[:160]}"}

    if strategy == "multimodal_model":
        model = str(body.get("model") or "").strip()
        if not model:
            return {"status": "failed", "message": "未填写视觉模型名称"}
        vision_base = str(body.get("visionBaseUrl") or "").strip().rstrip("/")
        if not vision_base:
            return {"status": "info", "message": "未填独立端点：运行时经平台 New API 用当前用户 Key 调用该模型。填 baseUrl+Key 可在此实测。"}
        # 填了独立端点：真发一张 1x1 测试图，验证 baseUrl + Key + 模型可用
        api_key = await cfg.resolve_secret(cfg.OCR_KEY, "visionApiKey", body.get("visionApiKey", ""))
        if not api_key:
            return {"status": "failed", "message": "填了 baseUrl 但缺少视觉模型 API Key"}
        # 测试图不能用 1x1 极小图：智谱等上游会直接拒收（错误码 1210「图片输入格式/
        # 解析错误」），导致配置明明正确却测试失败。改为现场生成一张正常尺寸的简单图。
        import base64
        import io
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (200, 80), "white")
        ImageDraw.Draw(img).text((20, 30), "TEST 123", fill="black")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        tiny_png = base64.b64encode(buf.getvalue()).decode()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{vision_base}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "这是一张测试图，回复 ok 即可。"},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{tiny_png}"}},
                            ],
                        }],
                        "max_tokens": 16,
                    },
                )
            if resp.status_code == 200 and (resp.json().get("choices")):
                return {"status": "success", "message": f"视觉模型 {model} 调用成功，图片识别可用"}
            return {"status": "failed", "message": f"端点返回 HTTP {resp.status_code}：{resp.text[:160]}"}
        except Exception as e:  # noqa: BLE001
            logger.warning("视觉模型连通性测试失败: %s", e)
            return {"status": "failed", "message": f"调用失败：{str(e)[:160]}"}

    return {"status": "failed", "message": f"未知 OCR 策略: {strategy}"}
