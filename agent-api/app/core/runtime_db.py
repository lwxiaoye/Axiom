"""Runtime 域表（§16.10）独立 PG 引擎与会话工厂。

与业务 MySQL（`app.core.database`）、LangGraph checkpointer 库均**不交叉读写、不共用连接池**
（§16.10 硬约束）。URL 取 `RUNTIME_DATABASE_URL`，为空则从 `CHECKPOINT_DATABASE_URL` 派生
同实例的独立 `agent_runtime` 库（psycopg 异步驱动）。全部功能对「未配置」优雅降级为不持久化。
"""
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

RuntimeBase = declarative_base()

_engine = None
_session_factory = None
RUNTIME_SCHEMA_HEAD = "runtime_0023_eval_runs"


def _resolve_url() -> Optional[str]:
    url = (settings.RUNTIME_DATABASE_URL or "").strip()
    if url:
        return url
    ck = (settings.CHECKPOINT_DATABASE_URL or "").strip()
    if ck.startswith("postgresql://") or ck.startswith("postgresql+"):
        # 复用 checkpoint PG 实例，切到独立 agent_runtime 库 + psycopg 异步驱动
        after_scheme = ck.split("://", 1)[1]
        creds_host = after_scheme.rsplit("/", 1)[0]
        return f"postgresql+psycopg://{creds_host}/agent_runtime"
    return None


def runtime_enabled() -> bool:
    return _resolve_url() is not None


def get_engine():
    global _engine
    if _engine is None:
        url = _resolve_url()
        if not url:
            return None
        _engine = create_async_engine(url, echo=False, pool_pre_ping=True, pool_size=5, max_overflow=5)
    return _engine


def runtime_session() -> Optional[sessionmaker]:
    global _session_factory
    if _session_factory is None:
        eng = get_engine()
        if eng is None:
            return None
        _session_factory = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    return _session_factory


async def _stamp_runtime_schema_head(conn) -> None:
    """Stamp schemas created by startup DDL so readiness uses the same head contract."""
    from sqlalchemy import text

    await conn.execute(text(
        "CREATE TABLE IF NOT EXISTS alembic_version_runtime "
        "(version_num VARCHAR(32) NOT NULL)"
    ))
    await conn.execute(text("DELETE FROM alembic_version_runtime"))
    await conn.execute(
        text("INSERT INTO alembic_version_runtime (version_num) VALUES (:revision)"),
        {"revision": RUNTIME_SCHEMA_HEAD},
    )


async def _guard_startup_ddl_revision(conn) -> None:
    """Refuse startup ``create_all`` when an existing schema needs real migrations."""

    from sqlalchemy import text

    version_table_exists = bool((await conn.execute(text(
        "SELECT to_regclass('alembic_version_runtime') IS NOT NULL"
    ))).scalar())
    existing_revision = None
    if version_table_exists:
        existing_revision = (
            await conn.execute(text(
                "SELECT version_num FROM alembic_version_runtime LIMIT 1"
            ))
        ).scalar()
    runtime_tables_exist = bool((await conn.execute(text(
        "SELECT to_regclass('agent_runs') IS NOT NULL"
    ))).scalar())
    if existing_revision and str(existing_revision) != RUNTIME_SCHEMA_HEAD:
        raise RuntimeError(
            "Runtime 域库存在待执行的 Alembic 迁移："
            f"当前 {existing_revision}，要求 {RUNTIME_SCHEMA_HEAD}。"
            "禁止用 create_all 跳过数据回填，请先执行 "
            "`alembic -c migrations/alembic.ini -n runtime upgrade head`"
        )
    if runtime_tables_exist and not existing_revision:
        raise RuntimeError(
            "Runtime 域库已有业务表但缺少 Alembic revision；"
            "禁止自动 stamp 为最新版，请由部署员核对并执行版本化迁移"
        )


async def _ensure_database_exists(url: str) -> None:
    """目标库不存在时自动创建（连同实例 postgres 管理库执行 CREATE DATABASE）。

    修复：compose 只初始化 checkpoint 库，派生的 agent_runtime 库此前无任何脚本创建——
    全新环境会让整层 Runtime 持久化（压缩/Run/HITL/引用/记忆）静默失效。
    """
    from sqlalchemy import text
    from sqlalchemy.pool import NullPool

    base, dbname = url.rsplit("/", 1)
    dbname = dbname.split("?", 1)[0]
    admin_engine = create_async_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with admin_engine.connect() as conn:
            exists = (
                await conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": dbname})
            ).scalar()
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{dbname}"'))
                logger.warning("Runtime 域库 %s 不存在，已自动创建", dbname)
    finally:
        await admin_engine.dispose()


async def init_runtime_tables(ddl: bool = True) -> bool:
    """建齐 Runtime 域表（独立 PG 库，create_all 安全——不与业务/ckpt 库交叉）。返回是否启用。

    ddl=False（MIGRATE_ON_STARTUP=false，schema 由 Alembic 管理，见 migrations/README.md）：
    不 create_all、不执行任何裸 SQL，只做连通性探测（「Runtime 域表就绪」的最低保证）后返回
    启用状态；探测失败抛错，由 main.py 的 RUNTIME_REQUIRED fail-closed 逻辑接管。
    """
    url = _resolve_url()
    if url is None:
        logger.info("Runtime 域库未配置，Task Run/Plan/Memory 持久化降级为关闭")
        return False
    if not ddl:
        # fail-fast（P1 五批）：此前只 SELECT 1——库在但表/列没迁移照样健康启动，与
        # 「忘跑迁移会 fail fast」的文档承诺不符。改为校验 Alembic revision 已到 baseline：
        # 未 stamp/未 upgrade（含只靠旧启动 DDL 建过表的库）一律拒绝启动。
        from sqlalchemy import text
        eng = get_engine()
        try:
            async with eng.connect() as conn:
                rev = (
                    await conn.execute(text("SELECT version_num FROM alembic_version_runtime LIMIT 1"))
                ).scalar()
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "Runtime 域库 Alembic 版本表不可读（MIGRATE_ON_STARTUP=false，启动期不执行 DDL）——"
                "请先执行 `alembic -c migrations/alembic.ini -n runtime upgrade head`（见 migrations/README.md）"
            ) from e
        if not rev:
            raise RuntimeError(
                "Runtime 域库迁移未执行（alembic_version_runtime 为空）——"
                "MIGRATE_ON_STARTUP=false 模式下拒绝启动，请先跑 Alembic 迁移")
        if str(rev) != RUNTIME_SCHEMA_HEAD:
            raise RuntimeError(
                f"Runtime 域库迁移版本落后：当前 {rev}，要求 {RUNTIME_SCHEMA_HEAD}。"
                "请先执行 `alembic -c migrations/alembic.ini -n runtime upgrade head`")
        logger.info("Runtime 域表就绪（MIGRATE_ON_STARTUP=false，Alembic revision=%s）", rev)
        return True
    try:
        await _ensure_database_exists(url)
    except Exception as e:  # noqa: BLE001
        # 建库失败不中断——目标库可能已存在而管理库无权限；随后的 create_all 是权威判定
        logger.warning("Runtime 域库存在性检查/创建失败（若库已存在可忽略）: %s", e)
    eng = get_engine()
    import app.runtime_models  # noqa: F401  注册所有 Runtime 表到 RuntimeBase.metadata
    async with eng.begin() as conn:
        # ``create_all`` can create the latest table shapes, but it cannot perform Alembic data
        # migrations (0016, for example, imports the historical request/cache audit rows).  Never
        # let startup DDL silently jump an existing database from an older revision to the current
        # head: doing so would make readiness green while required backfills were skipped.
        from sqlalchemy import text

        await _guard_startup_ddl_revision(conn)
        await conn.run_sync(RuntimeBase.metadata.create_all)
        # R0 并发不变量：同一 Thread 同时只允许一个活动 Run。部分唯一索引在 PG 层原子兜底
        # get_active_run 预检与 create_run 之间的 TOCTOU 竞态（两个并发请求都查到"无活动"后
        # 各建一个）。WHERE 集合与 task_run_service.ACTIVE_RUN_STATUSES 逐字一致；
        # waiting_clarification 不在其中（消歧本轮终点，不阻塞下一条消息重新路由）。
        # create_all 对已存在的表不会补建新索引，故这里用 IF NOT EXISTS 显式幂等建。
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_active_thread "
            "ON agent_runs (thread_id) "
            "WHERE status IN ('created','routing','running','waiting_user','waiting_confirmation','waiting_system')"
        ))
        # Profile 是 Run 的固有属性；旧表由迁移改名后，这里幂等补齐。
        await conn.execute(text(
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS agent_mode VARCHAR(16) NOT NULL DEFAULT 'standard'"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS outcome VARCHAR(16)"
        ))
        # 多 worker Run 租约列（P1 2026-07-17）：owner 实例 + 心跳，幂等补列。
        # 僵尸判定/启动对账据此改「租约过期」口径，消除多副本「扩容即互杀」。
        await conn.execute(text(
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS owner_instance_id VARCHAR(64)"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMP"
        ))
        # 可靠握手幂等键（N-02）：同一用户同一 client_request_id 只允许一个 Run——
        # 并发重复 POST 的第二次 INSERT 在 PG 层冲突，兜住路由层预检的 TOCTOU 窗口。
        await conn.execute(text(
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS client_request_id VARCHAR(64)"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_runs "
            "ADD COLUMN IF NOT EXISTS input_intake_closed_at TIMESTAMP"
        ))
        # Agent retired runtime control plane. These are also present in Alembic 0010; keeping
        # startup DDL idempotent preserves the project's existing MIGRATE_ON_STARTUP contract.
        await conn.execute(text(
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS state_version INTEGER NOT NULL DEFAULT 0"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS parent_run_id VARCHAR(64)"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS root_run_id VARCHAR(64)"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS parent_tool_call_id VARCHAR(64)"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS delegation_depth INTEGER NOT NULL DEFAULT 0"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMP"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS started_at TIMESTAMP"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_agent_runs_parent_run_id ON agent_runs (parent_run_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_agent_runs_root_run_id ON agent_runs (root_run_id)"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_plan_steps ADD COLUMN IF NOT EXISTS step_key VARCHAR(64)"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_plan_steps ADD COLUMN IF NOT EXISTS detail TEXT"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_plan_steps ADD COLUMN IF NOT EXISTS required INTEGER DEFAULT 1"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_plans ADD COLUMN IF NOT EXISTS goal TEXT"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_plans ADD COLUMN IF NOT EXISTS goal_revision INTEGER NOT NULL DEFAULT 0"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_plans ADD COLUMN IF NOT EXISTS plan_version INTEGER NOT NULL DEFAULT 0"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_plan_steps ADD COLUMN IF NOT EXISTS acceptance_criteria JSON"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_plan_steps ADD COLUMN IF NOT EXISTS depends_on JSON"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_plan_steps ADD COLUMN IF NOT EXISTS requires JSON"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_plan_steps ADD COLUMN IF NOT EXISTS reason TEXT"
        ))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_plans_run_version "
            "ON agent_plans (run_id, plan_version) WHERE plan_version > 0"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_tool_calls ADD COLUMN IF NOT EXISTS observation JSON"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_tool_calls ADD COLUMN IF NOT EXISTS raw_result_ref VARCHAR(255)"
        ))
        await conn.execute(text(
            "UPDATE agent_plan_steps SET step_key = CONCAT('legacy-', seq, '-', id) "
            "WHERE step_key IS NULL"
        ))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_plan_steps_plan_key "
            "ON agent_plan_steps (plan_id, step_key) WHERE step_key IS NOT NULL"
        ))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_client_request "
            "ON agent_runs (user_id, client_request_id) "
            "WHERE client_request_id IS NOT NULL"
        ))
        # 会话附件资产列（设计稿 §5）：create_all 不 ALTER 旧表，幂等补 message_id/kind
        await conn.execute(text(
            "ALTER TABLE agent_thread_attachments ADD COLUMN IF NOT EXISTS message_id INTEGER"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_thread_attachments ADD COLUMN IF NOT EXISTS kind VARCHAR(16) DEFAULT ''"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_thread_attachments ADD COLUMN IF NOT EXISTS file_id VARCHAR(64)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_agent_thread_attachments_file_id "
            "ON agent_thread_attachments (file_id)"
        ))
        # 队列派发租约列（§3）：create_all 不 ALTER 已存在的 agent_thread_queue，幂等补
        await conn.execute(text(
            "ALTER TABLE agent_thread_queue ADD COLUMN IF NOT EXISTS status VARCHAR(16) DEFAULT 'queued'"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_thread_queue ADD COLUMN IF NOT EXISTS lease_at TIMESTAMP"
        ))
        # 队列端到端不丢（§10.6 P0）：租约凭证 + 派发 Run 绑定，幂等补列
        await conn.execute(text(
            "ALTER TABLE agent_thread_queue ADD COLUMN IF NOT EXISTS lease_token VARCHAR(64)"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_thread_queue ADD COLUMN IF NOT EXISTS dispatched_run_id VARCHAR(64)"
        ))
        # TurnContext 快照列（审计项 1）：入队时刻的 Skill/知识库/文件/模型/任务模式选择
        await conn.execute(text(
            "ALTER TABLE agent_thread_queue ADD COLUMN IF NOT EXISTS context_json TEXT"
        ))
        # 运行中引导：applying_at 支撑悬挂回收（进程在 applying 与 finish 之间崩溃会永久
        # 阻塞该 Run 的后续引导）；applied_scope 记录被轮级还是图级消费，仅作可观测。
        await conn.execute(text(
            "ALTER TABLE agent_run_inputs ADD COLUMN IF NOT EXISTS applying_at TIMESTAMP"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_run_inputs ADD COLUMN IF NOT EXISTS applied_scope VARCHAR(16)"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_run_inputs ADD COLUMN IF NOT EXISTS source_message_id INTEGER"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_run_inputs "
            "ADD COLUMN IF NOT EXISTS input_sequence INTEGER"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_run_inputs "
            "ADD COLUMN IF NOT EXISTS input_type VARCHAR(24)"
        ))
        await conn.execute(text(
            "ALTER TABLE agent_run_inputs ADD COLUMN IF NOT EXISTS failure_reason TEXT"
        ))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_run_inputs_run_seq "
            "ON agent_run_inputs (run_id, input_sequence) "
            "WHERE input_sequence IS NOT NULL"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_agent_run_inputs_source_message "
            "ON agent_run_inputs (source_message_id) "
            "WHERE source_message_id IS NOT NULL"
        ))
        # 事件幂等唯一索引（P0 刷新丢失修复）：防 resume 双段流/重复落库产生重复
        # (run_id, sequence) 行——订阅回放有 seq 闸能挡，历史轨迹重建此前不挡会双次投影。
        # sequence<=0 的旧兜底行（legacy/异常帧曾以 0 落库）不纳入约束。建索引前先清存量
        # 重复（保留最小 id），且仅在索引尚不存在时执行清理，避免每次启动全表自联。
        idx_exists = (
            await conn.execute(text(
                "SELECT 1 FROM pg_indexes WHERE indexname = 'uq_agent_run_events_run_seq'"
            ))
        ).scalar()
        if not idx_exists:
            await conn.execute(text(
                "DELETE FROM agent_run_events a USING agent_run_events b "
                "WHERE a.id > b.id AND a.run_id = b.run_id "
                "AND a.sequence = b.sequence AND a.sequence > 0"
            ))
            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_run_events_run_seq "
                "ON agent_run_events (run_id, sequence) WHERE sequence > 0"
            ))
        await _stamp_runtime_schema_head(conn)
    logger.info("Runtime 域表就绪（PG 独立库）")
    return True


async def ping(timeout_seconds: float = 2.0) -> bool:
    """Runtime PG 连通性探测（/health 与 /health/ready 用）。未配置返回 False——
    调用方结合 runtime_enabled() 区分「未配置」与「配置了但连不上」。"""
    import asyncio

    from sqlalchemy import text

    eng = get_engine()
    if eng is None:
        return False

    async def _probe() -> bool:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    try:
        return await asyncio.wait_for(_probe(), timeout=timeout_seconds)
    except Exception as e:  # noqa: BLE001
        logger.warning("Runtime 域库探测失败: %s", e)
        return False


async def readiness_probe(timeout_seconds: float = 2.0) -> bool:
    """Require both connectivity and the exact deployed Runtime schema head."""
    import asyncio

    from sqlalchemy import text

    eng = get_engine()
    if eng is None:
        return False

    async def _probe() -> bool:
        async with eng.connect() as conn:
            revision = (
                await conn.execute(text(
                    "SELECT version_num FROM alembic_version_runtime LIMIT 1"
                ))
            ).scalar()
        return str(revision or "") == RUNTIME_SCHEMA_HEAD

    try:
        return await asyncio.wait_for(_probe(), timeout=timeout_seconds)
    except Exception as e:  # noqa: BLE001
        logger.warning("Runtime 域库 readiness 版本探测失败: %s", e)
        return False
