import hashlib
import hmac
import time
from collections import OrderedDict

from fastapi import HTTPException, Request

from app.core.config import settings

_seen_signatures: OrderedDict[str, int] = OrderedDict()


async def verify_internal_request(request: Request) -> None:
    """验证内部同步请求的 HMAC 签名。"""
    timestamp = request.headers.get("X-Internal-Timestamp", "")
    signature = request.headers.get("X-Internal-Signature", "")

    if not timestamp or not signature:
        raise HTTPException(status_code=401, detail="缺少内部鉴权头")
    if not settings.INTERNAL_SYNC_SECRET or settings.INTERNAL_SYNC_SECRET in {"CHANGE_ME", "change-me"}:
        raise HTTPException(status_code=503, detail="内部同步密钥未配置")

    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="时间戳格式错误")

    if abs(time.time() - ts) > settings.INTERNAL_SYNC_MAX_AGE_SECONDS:
        raise HTTPException(status_code=401, detail="请求已过期")

    body = await request.body()
    expected = hmac.new(
        settings.INTERNAL_SYNC_SECRET.encode(),
        f"{timestamp}\n".encode() + body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="签名验证失败")

    now = int(time.time())
    expired_before = now - settings.INTERNAL_SYNC_MAX_AGE_SECONDS
    while _seen_signatures:
        first_signature, seen_at = next(iter(_seen_signatures.items()))
        if seen_at >= expired_before:
            break
        _seen_signatures.pop(first_signature, None)
    if signature in _seen_signatures:
        raise HTTPException(status_code=401, detail="请求已被使用")
    _seen_signatures[signature] = now
