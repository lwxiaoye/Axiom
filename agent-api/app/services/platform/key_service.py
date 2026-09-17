import logging
from typing import Optional

from sqlalchemy import select

from app.core.database import async_session
from app.models import NewApiUserKey

logger = logging.getLogger(__name__)


class KeyService:
    """按 user_id 从 new_api_user_key 表读取该用户在 new-api 网关的专属 key。

    该表由 Java 侧在用户注册时写入，agent-api 只读不写。
    """

    async def get_user_key(self, user_id: str) -> Optional[str]:
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
