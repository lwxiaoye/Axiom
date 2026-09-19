"""智能体广场的模型预检接口。

前端广场在打开内置智能体前用它确认当前用户 Key 可用的对话模型与平台启用的 Embedding 模型。
原为 /workflow/marketplace/model/options，工作流编排整体删除后原样搬到 /marketplace/model/options
（响应结构不变，见 platform/marketplace_model_options.build_marketplace_model_options）。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select

from app.core.auth import UserContext, current_user
from app.core.database import async_session
from app.models import EmbeddingModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/marketplace", tags=["marketplace"])


async def _available_embedding_model_ids(session) -> list[str]:
    rows = (
        await session.execute(
            select(EmbeddingModel.model_id).where(or_(EmbeddingModel.enabled == 1, EmbeddingModel.is_active == 1))
        )
    ).scalars().all()
    return [str(model_id).strip() for model_id in rows if str(model_id or "").strip()]


@router.get("/model/options")
async def marketplace_model_options(user: UserContext = Depends(current_user)):
    try:
        from app.services.platform.key_service import key_service

        api_key = await key_service.get_user_key(user.user_id)
        if not api_key:
            return []
        from app.services.agents.agent_service import agent_service
        from app.services.platform.marketplace_model_options import build_marketplace_model_options

        chat_models = await agent_service.get_models(user_key=api_key, raise_on_lookup_failure=True)
        async with async_session() as session:
            embedding_models = await _available_embedding_model_ids(session)
        return build_marketplace_model_options(chat_models, embedding_models)
    except Exception as exc:
        logger.warning("load marketplace model options failed", exc_info=True)
        raise HTTPException(503, "广场模型列表暂不可用") from exc
