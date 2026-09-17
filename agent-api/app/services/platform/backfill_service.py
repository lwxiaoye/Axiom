"""把主库 app_info 中的智能体回填进向量库。

被两处复用：
  1. CLI：scripts/backfill_agents.py（手动跑、可 --dry-run）
  2. 启动自动回填：main.py lifespan 在「集合为空」时自动调用（幂等）

为什么需要：日常增量同步由 Java 在发布/编辑时回调 /internal/agents/bulk-sync。
但首次部署、迁移服务器、Qdrant 卷被重建后，存量智能体不会自动入库，对话内
「推荐智能体」就检索不到。本模块读 app_info → 拼 AgentSyncAgent → 复用
agent_sync_service.process_bulk_sync（同一套去重/embedding/写 Qdrant）。

可见性：role_ids/dept_ids 一律留空，vector_service 据此把 is_public 置 True，
即**本租户内**所有用户都能检索到（与「智能体广场对所有人显示」一致）。
租户：tenant_id 取自 app_info.tenant_id（NULL/'' → 全局占位 '0'）。空 ACL 判公开
只放开租户内的角色/部门限制，跨租户由 vector_service.search 的 tenant 过滤兜住——
两者缺一不可：早前只有前者时，任一租户的应用对所有租户可见。
"""

import asyncio
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import text

from app.core.config import settings
from app.core.database import async_session
from app.schemas.schemas import AgentSyncAgent
from app.services.knowledge import vector_service
from app.services.agents.agent_sync_service import _get_embedding_config, process_bulk_sync

logger = logging.getLogger(__name__)

# app_info 表与字段映射（主库列名不同时改这里即可）
APP_TABLE = "app_info"
COL = {
    "id": "id",
    "name": "app_name",
    "description": "app_remark",
    "icon": "app_icon",
    "category": "app_category",
    "status": "status",
    "owner": "create_by",
    "version": "update_time",
    "tenant": "tenant_id",
}
# create_by 新数据存用户 ID，历史数据仍是用户名；回填必须同时兼容两种值。
SYS_USER_TABLE = "sys_user"
SYS_USER_NAME_COL = "username"
SYS_USER_ID_COL = "id"


def _status_to_int(value) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_source_version(value) -> int:
    """把 update_time 转成单调递增的整数版本号。"""
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(datetime.fromisoformat(value).timestamp())
        except ValueError:
            return 1
    return 1


_tenant_column_supported: Optional[bool] = None


async def _has_tenant_column(session) -> bool:
    """探测 app_info 是否有 tenant_id 列（结果进程内缓存）。

    app_info 是 Java 侧的表，本服务只读。直接把新列写死进 SELECT，一旦某个部署的表
    结构没有它，整条回填链路（启动回填 + 轮询对账 + /ensure-indexed）会一起挂掉，
    代价是「推荐智能体全线消失」——比丢租户字段严重。探测不到就退化为全局 '0'。
    """
    global _tenant_column_supported
    if _tenant_column_supported is not None:
        return _tenant_column_supported
    try:
        rows = (
            await session.execute(
                text(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
                ),
                {"t": APP_TABLE, "c": COL["tenant"]},
            )
        ).scalars().all()
        _tenant_column_supported = bool(rows)
    except Exception as e:  # noqa: BLE001
        logger.warning("探测 %s.%s 失败，按无该列处理（租户退化为全局 0）：%s", APP_TABLE, COL["tenant"], e)
        _tenant_column_supported = False
    if not _tenant_column_supported:
        logger.warning(
            "%s 无 %s 列：回填的智能体一律落全局租户 '0'（对所有租户可见），无法按租户隔离",
            APP_TABLE, COL["tenant"],
        )
    return _tenant_column_supported


async def fetch_app_rows(limit: Optional[int]) -> List[dict]:
    async with async_session() as session:
        has_tenant = await _has_tenant_column(session)
        tenant_select = f"a.{COL['tenant']} AS tenant_id, " if has_tenant else "'0' AS tenant_id, "
        sql = (
            f"SELECT a.{COL['id']} AS id, a.{COL['name']} AS name, "
            f"a.{COL['description']} AS description, a.{COL['icon']} AS icon, "
            f"a.{COL['category']} AS category, a.{COL['status']} AS status, "
            f"{tenant_select}"
            f"a.{COL['owner']} AS owner_name, "
            f"u.{SYS_USER_ID_COL} AS owner_user_id, a.{COL['version']} AS version "
            f"FROM {APP_TABLE} a "
            f"LEFT JOIN {SYS_USER_TABLE} u ON (u.{SYS_USER_NAME_COL} = a.{COL['owner']} "
            f"OR u.{SYS_USER_ID_COL} = a.{COL['owner']})"
        )
        sql += f" ORDER BY a.{COL['id']} ASC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        result = await session.execute(text(sql))
        return [dict(row) for row in result.mappings().all()]


def build_agents(
    rows: List[dict],
    include_disabled: bool,
) -> List[AgentSyncAgent]:
    agents: List[AgentSyncAgent] = []
    for row in rows:
        status = _status_to_int(row.get("status"))
        if status != 1 and not include_disabled:
            continue
        # owner_user_id 仅作记录，embedding 统一走平台配置
        owner_user_id = str(row.get("owner_user_id") or "").strip()
        agents.append(
            AgentSyncAgent(
                id=str(row["id"]),
                # 租户维度（深扫 P0）：NULL/'' 归一为全局占位 '0'（对所有租户可见），
                # 非 0 值只对本租户可见——见 vector_service.search 的过滤口径
                tenant_id=str(row.get("tenant_id") or "0").strip() or "0",
                owner_user_id=owner_user_id,
                name=str(row.get("name") or "").strip(),
                description=str(row.get("description") or "").strip(),
                icon=str(row.get("icon") or "").strip(),
                category=str(row.get("category") or "").strip(),
                status=status,
                published=status == 1,
                role_ids=[],   # 留空 = 公开（is_public=True）
                dept_ids=[],
                source_version=_to_source_version(row.get("version")),
            )
        )
    return agents


async def run_backfill(
    *,
    include_disabled: bool = False,
    limit: Optional[int] = None,
    batch: int = 200,
    dry_run: bool = False,
) -> dict:
    """主回填流程，返回汇总 dict。"""
    rows = await fetch_app_rows(limit)
    agents = build_agents(rows, include_disabled)
    logger.info("回填：app_info 读到 %d 条，待同步 %d 条", len(rows), len(agents))

    missing_owner = [a.id for a in agents if a.status == 1 and not a.owner_user_id]
    if missing_owner:
        logger.info("%d 条无法解析 owner，仅记录为空，不影响平台向量化：%s", len(missing_owner), missing_owner[:10])

    if dry_run:
        for a in agents:
            logger.info("[dry-run] id=%s owner=%s name=%s", a.id, a.owner_user_id, a.name)
        return {"status": "dry-run", "total": len(agents)}

    if not agents:
        return {"status": "ok", "indexed": 0, "skipped": 0, "deleted": 0, "failed": 0}

    batch = min(max(1, batch), 200)
    total = {"indexed": 0, "skipped": 0, "deleted": 0, "failed": 0}
    errors: List[str] = []
    for i in range(0, len(agents), batch):
        chunk = agents[i:i + batch]
        result = await process_bulk_sync(chunk)
        for k in total:
            total[k] += result.get(k, 0)
        errors.extend(result.get("errors", []))
        logger.info(
            "回填批次 %d/%d：indexed=%s skipped=%s deleted=%s failed=%s",
            i // batch + 1, (len(agents) + batch - 1) // batch,
            result.get("indexed"), result.get("skipped"),
            result.get("deleted"), result.get("failed"),
        )
    total["status"] = "partial" if total["failed"] else "ok"
    total["errors"] = errors[:20]

    # 全量回填成功后清理陈旧集合（旧版本副本 + 裸 `agents`）：
    #   - 仅全量（limit is None）：--limit 是抽样/局部，当前集合不代表全量，绝不据此删旧；
    #   - 仅无失败（status == ok）：有 failed 说明本轮当前集合可能不完整，保守留旧到下轮；
    #   - vector_service.cleanup 内部再校验“当前集合非空”，是删除的最终安全阀。
    # 非致命：清理异常不影响本次回填结果。
    if limit is None and total["status"] == "ok":
        try:
            total["cleaned_collections"] = await _cleanup_stale_collections()
        except Exception as e:  # noqa: BLE001 —— 清理失败不该让回填“失败”
            logger.warning("[backfill] 陈旧集合清理失败（不影响本次回填）：%s", e)
            total["cleaned_collections"] = []
    return total


async def _cleanup_stale_collections() -> List[str]:
    """解析当前 embedding 配置对应的集合名，交给 vector_service 清理更低版本的同配置集合。

    拿不到 embedding 配置就什么都不删（回填本也不会写入任何集合）。
    """
    config = await _get_embedding_config()
    if not config:
        return []
    _model_id, _dimension, collection = config
    return await vector_service.cleanup_stale_agent_collections(collection)


async def reconcile_once(
    since_version: int,
) -> tuple[dict, int]:
    """单轮对账：同步「自 since_version 之后变更」的应用 + 清理已删除的应用。

    返回 (汇总, 本轮见到的最大 source_version)。只处理变更行，避免每轮重写全量。
    """
    config = await _get_embedding_config()
    if not config:
        return {"status": "skip", "reason": "no embedding model"}, since_version
    _model_id, dimension, collection = config
    await vector_service.ensure_collection(collection, dimension)

    rows = await fetch_app_rows(None)
    max_version = since_version
    changed: List[dict] = []
    for row in rows:
        v = _to_source_version(row.get("version"))
        if v > max_version:
            max_version = v
        if v > since_version:
            changed.append(row)

    summary: dict = {"indexed": 0, "skipped": 0, "deleted": 0, "failed": 0, "removed_stale": 0}

    # 变更 / 新增 / 停用：include_disabled 让停用的进列表 → process_bulk_sync 会删除它们
    agents = build_agents(changed, include_disabled=True)
    if agents:
        result = await process_bulk_sync(agents)
        for k in ("indexed", "skipped", "deleted", "failed"):
            summary[k] = result.get(k, 0)

    # 硬删除对账：app_info 里已不存在的，从 Qdrant 清掉
    db_ids = {str(r["id"]) for r in rows}
    try:
        vec_ids = await vector_service.list_agent_ids(collection)
        stale = list(vec_ids - db_ids)
        if stale:
            await vector_service.delete_by_agent_ids(collection, stale)
            summary["removed_stale"] = len(stale)
    except Exception as e:
        logger.warning("[sync] 删除对账失败（下轮重试）：%s", e)

    return summary, max_version


async def run_sync_loop() -> None:
    """后台轮询对账循环。启动时以当前最大版本为基线，仅响应之后的变更。"""
    interval = max(10, settings.SYNC_POLL_INTERVAL)
    # 基线：当前 app_info 的最大版本（启动回填已覆盖存量，无需重处理全量）
    since_version = 0
    try:
        rows = await fetch_app_rows(None)
        since_version = max((_to_source_version(r.get("version")) for r in rows), default=0)
    except Exception as e:
        logger.warning("[sync] 初始化基线失败，从 0 开始：%s", e)

    logger.info("[sync] 轮询对账启动，间隔 %ds，基线版本 %d", interval, since_version)
    while True:
        await asyncio.sleep(interval)
        try:
            summary, since_version = await reconcile_once(since_version)
            if any(summary.get(k) for k in ("indexed", "deleted", "failed", "removed_stale")):
                logger.info("[sync] 对账变更：%s", summary)
        except Exception as e:
            logger.warning("[sync] 对账异常（下轮重试）：%s", e)


async def auto_backfill_if_empty() -> None:
    """启动钩子：仅当向量集合为空时自动回填。幂等，正常重启不重复灌。"""
    config = await _get_embedding_config()
    if not config:
        logger.info("[auto-backfill] 无默认 Embedding 模型，跳过")
        return
    _model_id, dimension, collection = config
    await vector_service.ensure_collection(collection, dimension)
    try:
        count = await vector_service.count_collection(collection)
    except Exception as e:
        logger.warning("[auto-backfill] 读取集合数量失败，跳过：%s", e)
        return
    if count > 0:
        logger.info("[auto-backfill] 集合 %s 已有 %d 条向量，跳过", collection, count)
        return

    logger.info("[auto-backfill] 集合为空，开始自动回填……")
    try:
        result = await run_backfill()
        logger.info("[auto-backfill] 完成：%s", result)
    except Exception as e:
        logger.warning("[auto-backfill] 失败（不影响服务启动）：%s", e)
