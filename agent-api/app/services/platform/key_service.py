"""对话模型凭据的统一解析入口。

解析顺序（所有需要模型 Key 的地方都从这里走，不要各自读表）：
1. 用户在「模型配置」页自己填的连接（个人覆盖，可选）；
2. 管理员在「管理配置 → 对话模型」配的平台默认——对所有登录用户生效，这是校园场景
   的常态：学生不该需要自带 API Key；
3. new-api 网关按用户签发的专属 key（``new_api_user_key``，auth-api 注册时写入，
   这里只读）——老部署的路径，保留兼容；
4. 都没有 → ``require_user_key`` 抛 403，文案对普通用户有意义：让他找管理员配，
   而不是暗示「你的账号少了什么」。

命中 1/2 时会把连接（base_url/model）绑定到请求上下文（``bind_model_connection``），
后续 ``get_model_base_url``/``agent_service.get_models`` 就按这份连接工作。
"""
import logging
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select

from app.core.database import async_session
from app.models import NewApiUserKey

logger = logging.getLogger(__name__)

NO_MODEL_MESSAGE = "平台尚未配置对话模型，请联系管理员在管理配置中设置"


class KeyService:
    async def resolve_chat_credential(self, user_id: str) -> Optional[dict]:
        """按上面的顺序解析，返回 {api_key, model, base_url, source} 或 None。

        source ∈ {"user", "platform", "newapi"}，只用于日志和管理端展示，不参与判断。
        """
        from app.core.model_endpoint import bind_model_connection
        from app.services.platform import model_connection

        bind_model_connection(None)
        connection = await model_connection.runtime_user(user_id)
        if connection:
            bind_model_connection(connection)
            return connection | {"source": "user"}
        connection = await model_connection.runtime_platform()
        if connection:
            bind_model_connection(connection)
            return connection | {"source": "platform"}
        async with async_session() as session:
            row = (
                await session.execute(
                    select(NewApiUserKey)
                    .where(NewApiUserKey.user_id == user_id)
                    .order_by(NewApiUserKey.token_id.desc())
                )
            ).scalars().first()
        if row and row.api_key:
            return {"api_key": row.api_key, "model": "", "base_url": "", "source": "newapi"}
        logger.warning("用户 %s 没有可用的对话模型凭据（个人覆盖/平台默认/new-api 均未命中）", user_id)
        return None

    async def get_user_key(self, user_id: str) -> Optional[str]:
        """只要 Key 的调用方用这个；没有可用凭据返回 None（不抛）。"""
        credential = await self.resolve_chat_credential(user_id)
        return credential["api_key"] if credential else None

    async def require_user_key(self, user_id: str) -> str:
        """需要 Key 才能继续的调用方用这个：拿不到就 403，文案统一。

        经 ``self.get_user_key`` 走一遍，是为了让测试里对 get_user_key 的打桩继续生效。
        """
        key = await self.get_user_key(user_id)
        if not key:
            raise HTTPException(status_code=403, detail=NO_MODEL_MESSAGE)
        return key

    async def resolve_search_fallback_key(self, search_config: dict) -> str:
        """联网搜索「付费兜底」（deepseek-official）的取 Key 口。

        优先用调用方随请求带来的那把（主对话在 prepare_chat 里已经按统一顺序解析过，
        写在 search_config["deepseekApiKey"]）；调用方没带时按 _callerUserId 走同一套解析并
        回填，别再让「没带 Key」被说成「用户未分配 Key」。解析不到返回空串。
        """
        key = str(search_config.get("deepseekApiKey") or "").strip()
        if key:
            return key
        user_id = str(search_config.get("_callerUserId") or "").strip()
        if not user_id:
            return ""
        key = await self.get_user_key(user_id) or ""
        search_config["deepseekApiKey"] = key
        return key


key_service = KeyService()
