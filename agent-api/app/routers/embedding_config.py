import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.auth import UserContext, current_user, is_admin
from app.core.config import settings
from app.core.database import async_session
from app.models import EmbeddingModel
from app.services.knowledge import embedding_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/embedding-config", tags=["embedding-config"])


class EmbeddingConfigResponse(BaseModel):
    id: Optional[int] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key_masked: Optional[str] = None
    dimension: Optional[int] = None
    is_active: bool = False
    test_status: Optional[str] = None
    test_message: Optional[str] = None
    last_test_time: Optional[str] = None


class EmbeddingConfigUpdate(BaseModel):
    model: str
    base_url: str
    api_key: Optional[str] = None


class TestResult(BaseModel):
    status: str
    dimension: Optional[int] = None
    message: str
    # 本次探测结果是否写入了生效配置行（false=测的是未保存的候选，纯一次性探测）
    persisted: bool = False


def _mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


def _same_target(row: EmbeddingModel, body: EmbeddingConfigUpdate) -> bool:
    """被测配置是否就是这行生效配置（模型 + Base URL 双匹配，忽略尾斜杠/空白）。"""
    def norm(value) -> str:
        return str(value or "").strip().rstrip("/")

    return norm(row.model_id) == norm(body.model) and norm(row.base_url) == norm(body.base_url)


@router.get("", response_model=EmbeddingConfigResponse)
async def get_config(user: UserContext = Depends(current_user)):
    """获取平台 Embedding 配置（API Key 脱敏）。"""
    if not is_admin(user):
        raise HTTPException(403, "需要管理员权限")

    async with async_session() as session:
        row = (
            await session.execute(
                select(EmbeddingModel)
                .where(EmbeddingModel.is_active == 1)
                .order_by(EmbeddingModel.id.desc())
            )
        ).scalars().first()

    if not row:
        return EmbeddingConfigResponse()

    # 库里是 Fernet 密文，遮罩必须对解密后的明文做：直接遮密文会把页面上的 `sk-w****Ck8n`
    # 变成 `gAAA****xxxx`，管理员会误以为 key 被人改了。解不开的密文 _read_key 返回空 →
    # 页面显示无 key，正好提示重填。
    plain_key = embedding_service._read_key(row) if row.api_key else ""

    return EmbeddingConfigResponse(
        id=row.id,
        model=row.model_id,
        base_url=row.base_url,
        api_key_masked=_mask_key(plain_key) if plain_key else None,
        dimension=row.dimension,
        is_active=bool(row.is_active),
        test_status=row.test_status,
        test_message=row.test_message,
        last_test_time=row.last_test_time.isoformat() if row.last_test_time else None,
    )


@router.put("", response_model=dict)
async def update_config(
    body: EmbeddingConfigUpdate,
    user: UserContext = Depends(current_user),
):
    """保存平台 Embedding 配置。"""
    if not is_admin(user):
        raise HTTPException(403, "需要管理员权限")

    # api_key 列只存 Fernet 密文（与对话/重排模型一致），明文不落库；超长等入库前就能判定的
    # 问题按 400 返回，不让它变成一条截断后永远解不开的记录
    try:
        stored_key = embedding_service._store_key(body.api_key) if body.api_key else None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    async with async_session() as session:
        active_row = (
            await session.execute(
                select(EmbeddingModel)
                .where(EmbeddingModel.is_active == 1)
            )
        ).scalars().first()
        model_row = (
            await session.execute(
                select(EmbeddingModel)
                .where(EmbeddingModel.model_id == body.model)
            )
        ).scalars().first()

        row = model_row or active_row

        if row:
            # 换模型/换端点必须作废旧 dimension（深扫收尾 2026-07-26）：集合名按
            # (model, dimension) 哈希，沿用旧维度会让「改模型→保存→测试」把新维度写进
            # 生效行前的窗口期检索指向错误集合；置空后 /ensure-indexed 与测试通过共同恢复
            _target_changed = (
                (row.model_id or "") != (body.model or "")
                or (row.base_url or "").rstrip("/") != (body.base_url or "").rstrip("/")
            )
            if _target_changed:
                row.dimension = None
            row.model_id = body.model
            row.base_url = body.base_url
            if stored_key:
                row.api_key = stored_key
            row.enabled = 1
            row.is_active = 1
            row.test_status = None
            row.test_message = None
        else:
            row = EmbeddingModel(
                model_id=body.model,
                dimension=1024,
                enabled=1,
                is_default=0,
                api_key=stored_key,
                base_url=body.base_url,
                is_active=1,
            )
            session.add(row)

        # 新建时 row.id 此刻仍是 None，而 SQLAlchemy 会把 `id != None` 编译成
        # `id IS NOT NULL`——那条「停用其它配置」的语句会连刚建的这行一起置 0，
        # 导致新增的 Embedding 配置永远无法激活。先 flush 拿到主键再比较。
        await session.flush()
        rows = (
            await session.execute(
                select(EmbeddingModel).where(EmbeddingModel.id != row.id)
            )
        ).scalars().all()
        for item in rows:
            item.is_active = 0

        await session.commit()
        return {"message": "保存成功", "id": row.id}


@router.post("/test", response_model=TestResult)
async def test_config(
    body: EmbeddingConfigUpdate,
    user: UserContext = Depends(current_user),
):
    """测试 Embedding 配置连通性。"""
    if not is_admin(user):
        raise HTTPException(403, "需要管理员权限")

    api_key = body.api_key
    if not api_key:
        async with async_session() as session:
            row = (
                await session.execute(
                    select(EmbeddingModel).where(EmbeddingModel.is_active == 1)
                )
            ).scalars().first()
            if row:
                # 库里是密文，回落时解成明文再拿去探测；解不开返回空 → 下面按「未提供」处理
                api_key = embedding_service._read_key(row)

    if not api_key:
        return TestResult(status="failed", message="未提供 API Key")

    result = await embedding_service.test_embedding_config(body.model, api_key, body.base_url)

    # 探测结果只允许回写「被测的就是当前生效行」的情况。
    # 旧实现无条件写 is_active==1 的行：管理员试一个候选模型（未保存），探测到的
    # dimension=3072 会被盖到线上 bge-large-zh 的生效行上——集合名按 (model, dimension)
    # 哈希，维度一变就指向一个空集合，全站向量检索静默失效（且 test_status=success
    # 还会被当成可用配置）。改为身份校验后再落库；不匹配则纯一次性探测，
    # 结果（含 dimension）只经响应体返回给前端。
    persisted = False
    async with async_session() as session:
        row = (
            await session.execute(
                select(EmbeddingModel).where(EmbeddingModel.is_active == 1)
            )
        ).scalars().first()
        if row and _same_target(row, body):
            row.test_status = result["status"]
            row.test_message = result["message"]
            row.last_test_time = datetime.now()
            if result.get("dimension"):
                row.dimension = result["dimension"]
            await session.commit()
            persisted = True

    if result.get("status") == "success" and not persisted:
        # 前端凭 GET 回来的 test_status 判断配置是否可用：这里明确提示先保存。
        result = {**result, "message": f"{result.get('message') or '测试通过'}（未保存的候选配置，结果不写入生效配置）"}

    return TestResult(**result, persisted=persisted)
