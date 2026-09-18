"""重排模型配置：管理员限定。GET 脱敏、PUT 保存（密文入库）、POST /test 用草稿探测、不落库。"""
from fastapi import APIRouter, Depends

from app.core.auth import UserContext
from app.routers.model_connection import admin
from app.services.knowledge import rerank_service as service

router = APIRouter(prefix="/rerank-config", tags=["rerank-config"])


@router.get("")
async def get_config(user: UserContext = Depends(admin)):
    return service.public(await service.read())


@router.put("")
async def save_config(body: service.RerankInput, user: UserContext = Depends(admin)):
    return await service.save(body)


@router.post("/test")
async def test_config(body: service.RerankInput, user: UserContext = Depends(admin)):
    data = await service.prepare(body)
    # 开关关着也允许测：测的是地址/密钥/模型通不通，不是是否启用。
    return await service.test_rerank(service.to_config(data | {"enabled": True}))
