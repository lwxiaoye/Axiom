import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.auth import UserContext, current_user, is_admin
from app.core.config import settings
from app.core.database import async_session
from app.models import EmbeddingModel
from app.services.knowledge import embedding_service, vector_service
from app.services.platform import backfill_service

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

    return EmbeddingConfigResponse(
        id=row.id,
        model=row.model_id,
        base_url=row.base_url,
        api_key_masked=_mask_key(row.api_key) if row.api_key else None,
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
            if body.api_key:
                row.api_key = body.api_key
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
                api_key=body.api_key,
                base_url=body.base_url,
                is_active=1,
            )
            session.add(row)

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
                api_key = row.api_key

    if not api_key:
        return TestResult(status="failed", message="未提供 API Key")

    result = await embedding_service.test_embedding_config(body.model, api_key, body.base_url)

    # 探测结果只允许回写「被测的就是当前生效行」的情况。
    # 旧实现无条件写 is_active==1 的行：管理员试一个候选模型（未保存），探测到的
    # dimension=3072 会被盖到线上 bge-large-zh 的生效行上——集合名按 (model, dimension)
    # 哈希，维度一变就指向一个空集合，全站向量检索静默失效（且 test_status=success
    # 还会放行 reindex/ensure-indexed）。改为身份校验后再落库；不匹配则纯一次性探测，
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
        # 前端凭 GET 回来的 test_status 才放开「重新索引」按钮：这里明确提示先保存，
        # 否则用户会以为测试通过了却点不动重建索引。
        result = {**result, "message": f"{result.get('message') or '测试通过'}（未保存的候选配置，结果不写入生效配置）"}

    return TestResult(**result, persisted=persisted)


_reindex_jobs: dict[str, dict] = {}
_MAX_JOB_RECORDS = 50

# 全量回填互斥（单进程内）：run_backfill 是全表重算向量的重活，重复并发跑只会浪费
# 配额、互相覆盖进度。_backfill_lock 只保护「检查 + 置位」这一小段，不跨越回填本身；
# 真正在跑的标记是 _backfill_active（当前 job_id），由后台任务在 finally 清空。
# 多实例部署下仍可能各起一份（遗留项，需分布式锁/DB 排他标记才能根治）。
_backfill_lock = asyncio.Lock()
_backfill_active: Optional[str] = None
# 回填任务强引用（B4 教训）：事件循环对 task 只持弱引用，裸 create_task 可能被 GC
_backfill_tasks: set = set()


def _new_job_id(prefix: str) -> str:
    # 秒级时间戳同秒两次点击会撞成同一个 job_id（进度互相覆盖），补 uuid 短后缀去重
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _trim_jobs() -> None:
    while len(_reindex_jobs) > _MAX_JOB_RECORDS:
        oldest = next(iter(_reindex_jobs))
        if oldest == _backfill_active:
            break
        _reindex_jobs.pop(oldest, None)


async def _start_backfill_job(prefix: str) -> tuple[str, bool]:
    """抢占回填令牌并起后台任务。返回 (job_id, started)；已有回填在跑时返回其 job_id + False。"""
    global _backfill_active
    async with _backfill_lock:
        if _backfill_active:
            return _backfill_active, False
        job_id = _new_job_id(prefix)
        _backfill_active = job_id
        _reindex_jobs[job_id] = {
            "status": "running",
            "started_at": datetime.now().isoformat(),
            "indexed": 0,
            "failed": 0,
            "total": 0,
        }
        _trim_jobs()

    async def _run():
        global _backfill_active
        try:
            result = await backfill_service.run_backfill(include_disabled=False)
            _reindex_jobs[job_id].update({
                "status": "completed",
                "indexed": result.get("indexed", 0),
                "failed": result.get("failed", 0),
                "total": result.get("indexed", 0) + result.get("failed", 0),
                "completed_at": datetime.now().isoformat(),
            })
        except Exception as e:
            logger.warning("向量回填任务失败 job=%s", job_id, exc_info=True)
            _reindex_jobs[job_id].update({
                "status": "failed",
                "message": str(e)[:200],
                "completed_at": datetime.now().isoformat(),
            })
        finally:
            _backfill_active = None

    # 持强引用防 GC（B4 教训，同 run_hub._spawn_bg）：裸 create_task 的任务可能被回收，
    # 届时 _reindex_jobs[job_id] 永远停在 running、互斥位被闩死
    task = asyncio.create_task(_run())
    _backfill_tasks.add(task)
    task.add_done_callback(_backfill_tasks.discard)
    return job_id, True


@router.post("/reindex", response_model=dict)
async def reindex(user: UserContext = Depends(current_user)):
    """启动全量重新索引任务。"""
    if not is_admin(user):
        raise HTTPException(403, "需要管理员权限")

    config = await embedding_service.get_active_embedding_config()
    if not config:
        raise HTTPException(400, "未配置平台 Embedding 模型，请先保存并测试成功")

    if config.test_status != "success":
        raise HTTPException(400, "Embedding 配置测试未通过，请先测试成功")

    job_id, started = await _start_backfill_job("reindex")
    # 已有回填在跑：返回在跑的 job_id 让前端接着轮询同一个任务，不再起第二份
    return {"job_id": job_id, "status": "started" if started else "already_running"}


@router.post("/ensure-indexed", response_model=dict)
async def ensure_indexed(user: UserContext = Depends(current_user)):
    """普通用户登录后触发：若当前平台集合为空，则后台补齐索引。

    刻意**不**要求管理员：这是普通用户登录链路的自愈入口（新部署后集合为空时补齐），
    加管理员门禁就等于废掉它。放大风险由 _start_backfill_job 的互斥消掉——集合为空时
    N 个用户几秒内登录只会起一份回填，其余直接拿到 already_running；除此之外本接口
    只读配置 + 数一次集合数量，无副作用。
    """
    config = await embedding_service.get_active_embedding_config()
    if not config or config.test_status != "success" or not config.dimension:
        return {"status": "skipped", "reason": "no_active_embedding_config"}

    collection = vector_service.collection_name(config.model, config.dimension)
    await vector_service.ensure_collection(collection, config.dimension)
    try:
        count = await vector_service.count_collection(collection)
    except Exception as e:
        logger.warning("ensure-indexed 读取集合数量失败: %s", e)
        return {"status": "skipped", "reason": "qdrant_unavailable"}

    if count > 0:
        return {"status": "skipped", "reason": "collection_not_empty", "count": count}

    job_id, started = await _start_backfill_job("ensure")
    return {
        "status": "started" if started else "already_running",
        "job_id": job_id,
        "collection": collection,
    }


@router.get("/reindex/{job_id}", response_model=dict)
async def get_reindex_status(
    job_id: str,
    user: UserContext = Depends(current_user),
):
    """查询重新索引任务进度。"""
    if not is_admin(user):
        raise HTTPException(403, "需要管理员权限")

    job = _reindex_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return job
