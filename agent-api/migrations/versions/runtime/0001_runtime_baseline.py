"""Runtime PG 域库 baseline（P1 2026-07-17）：接管 runtime_db.init_runtime_tables 全部 DDL。

对照 app/core/runtime_db.py 的 create_all + 裸 SQL 段逐条搬运，全部幂等（PG 原生
IF NOT EXISTS + pg_indexes 探测）：
- 空库/新环境：RuntimeBase.metadata.create_all 建齐 Runtime 域表（env.py 已先确保
  agent_runtime 库存在）；
- 已由启动期 DDL 建好的存量库：`upgrade head` 是无害 no-op（只落 alembic_version_runtime
  版本标记）。

覆盖清单：
- agent_runs：execution_mode / outcome / owner_instance_id / heartbeat_at（多 worker 租约）
- agent_runs 部分唯一索引 uq_agent_runs_active_thread（R0：同 Thread 单活动 Run 原子占位）
- agent_thread_attachments：message_id / kind
- agent_thread_queue：status / lease_at / lease_token / dispatched_run_id（派发租约）
- agent_task_graphs：completion_contract（完成契约独立列）
- agent_run_events：存量去重 + (run_id, sequence>0) 唯一索引（事件幂等）

Revision ID: runtime_0001_baseline
Revises:
Create Date: 2026-07-17
"""
from alembic import op
import sqlalchemy as sa

revision = "runtime_0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

# 逐条对照 runtime_db.init_runtime_tables 的裸 SQL 段（PG 支持 ADD COLUMN IF NOT EXISTS，
# 天然幂等）。
_IDEMPOTENT_DDL = (
    # R0 并发不变量：同一 Thread 同时只允许一个活动 Run。WHERE 集合与
    # task_run_service.ACTIVE_RUN_STATUSES 逐字一致。
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_active_thread "
    "ON agent_runs (thread_id) "
    "WHERE status IN ('running','waiting_user','waiting_confirmation','waiting_system')",
    # 任务模式列（设计稿 §1/§2）
    "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS execution_mode VARCHAR(16) DEFAULT 'chat'",
    "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS outcome VARCHAR(16)",
    # 多 worker Run 租约列（P1 2026-07-17）
    "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS owner_instance_id VARCHAR(64)",
    "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMP",
    # 会话附件资产列（设计稿 §5）
    "ALTER TABLE agent_thread_attachments ADD COLUMN IF NOT EXISTS message_id INTEGER",
    "ALTER TABLE agent_thread_attachments ADD COLUMN IF NOT EXISTS kind VARCHAR(16) DEFAULT ''",
    # 队列派发租约列（§3 / §10.6 端到端不丢）
    "ALTER TABLE agent_thread_queue ADD COLUMN IF NOT EXISTS status VARCHAR(16) DEFAULT 'queued'",
    "ALTER TABLE agent_thread_queue ADD COLUMN IF NOT EXISTS lease_at TIMESTAMP",
    "ALTER TABLE agent_thread_queue ADD COLUMN IF NOT EXISTS lease_token VARCHAR(64)",
    "ALTER TABLE agent_thread_queue ADD COLUMN IF NOT EXISTS dispatched_run_id VARCHAR(64)",
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1) 建齐 Runtime 域表（等价旧启动期 create_all；checkfirst 幂等，不 ALTER 既有表）
    from app.core.runtime_db import RuntimeBase
    import app.runtime_models  # noqa: F401  注册所有 Runtime 表到 RuntimeBase.metadata
    RuntimeBase.metadata.create_all(bind)

    # 2) 幂等补列/索引
    for ddl in _IDEMPOTENT_DDL:
        op.execute(ddl)

    # agent_task_graphs 属于已废弃的主对话任务图模型。当前 RuntimeBase 不再创建该表，
    # 但存量库在 0013 清理它之前仍需补齐旧列。不能只依赖 ADD COLUMN IF NOT EXISTS：
    # PostgreSQL 在表不存在时仍会报 UndefinedTable，导致全新 Runtime 库无法完成 baseline。
    if bind.execute(sa.text("SELECT to_regclass('agent_task_graphs')")).scalar():
        op.execute(
            "ALTER TABLE agent_task_graphs "
            "ADD COLUMN IF NOT EXISTS completion_contract JSON"
        )

    # 3) 事件幂等唯一索引（P0 刷新丢失修复）：仅在索引尚不存在时先清存量重复行（保留最小
    #    id），再建 (run_id, sequence>0) 部分唯一索引——与 runtime_db 同款守卫，避免每次
    #    执行都全表自联。
    idx_exists = bind.execute(sa.text(
        "SELECT 1 FROM pg_indexes WHERE indexname = 'uq_agent_run_events_run_seq'"
    )).scalar()
    if not idx_exists:
        op.execute(
            "DELETE FROM agent_run_events a USING agent_run_events b "
            "WHERE a.id > b.id AND a.run_id = b.run_id "
            "AND a.sequence = b.sequence AND a.sequence > 0")
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_run_events_run_seq "
            "ON agent_run_events (run_id, sequence) WHERE sequence > 0")


def downgrade() -> None:
    # 采纳式 baseline 不提供结构回滚（Runtime 域表承载事件回放/HITL/记忆等事实源，保守不销毁）。
    pass
