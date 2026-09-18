import logging
from typing import Optional

from sqlalchemy import select

from app.core.database import async_session
from app.models import NewApiUserKey

logger = logging.getLogger(__name__)


class KeyService:
    """优先使用账号保存的模型连接，否则读取 new-api 网关专属 key。

    该表由 Java 侧在用户注册时写入，agent-api 只读不写。
    """

    async def get_user_key(self, user_id: str) -> Optional[str]:
        from app.core.model_endpoint import bind_model_connection
        from app.services.platform.model_connection import runtime
        bind_model_connection(None)
        connection = await runtime(user_id)
        if connection:
            bind_model_connection(connection)
            return connection["api_key"]
        async with async_session() as session:
            row = (
                await session.execute(
                    select(NewApiUserKey)
                    .where(NewApiUserKey.user_id == user_id)
                    .order_by(NewApiUserKey.token_id.desc())
                )
            ).scalars().first()
            if row and row.api_key:
                return row.api_key
            logger.warning("用户 %s 无有效 API Key", user_id)
            return None


key_service = KeyService()
