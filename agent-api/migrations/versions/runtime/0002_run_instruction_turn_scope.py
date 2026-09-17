"""Runtime PG：运行中引导轮级消费配套列（2026-07-21）。

轮级引导（main_agent.stream_tool_loop 在工具轮边界注入正在跑的这一轮，不中断模型运行）
需要两列：

- applying_at：认领时刻。进程在「置 applying」与「finish」之间崩溃会让该条永久停在
  applying，而「同一 Run 最多一条 applying」的门禁会连带永久阻塞这个 Run 的所有后续引导。
  claim_next 据此做超时回收（打回 queued）。created_at 是提交时刻，不能替代。
- applied_scope：turn（轮级注入）/ graph（图级 replan），仅作可观测记录，不参与调度判定。

与 app/core/runtime_db.init_runtime_tables 的启动期幂等 DDL 同源，两边都用
ADD COLUMN IF NOT EXISTS，先跑哪个都无害。

Revision ID: runtime_0002_instr_scope
Revises: runtime_0001_baseline
Create Date: 2026-07-21
"""
from alembic import op

revision = "runtime_0002_instr_scope"
down_revision = "runtime_0001_baseline"
branch_labels = None
depends_on = None

_UPGRADE_DDL = (
    "ALTER TABLE agent_run_inputs ADD COLUMN IF NOT EXISTS applying_at TIMESTAMP",
    "ALTER TABLE agent_run_inputs ADD COLUMN IF NOT EXISTS applied_scope VARCHAR(16)",
)

_DOWNGRADE_DDL = (
    "ALTER TABLE agent_run_inputs DROP COLUMN IF EXISTS applying_at",
    "ALTER TABLE agent_run_inputs DROP COLUMN IF EXISTS applied_scope",
)


def upgrade() -> None:
    for stmt in _UPGRADE_DDL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _DOWNGRADE_DDL:
        op.execute(stmt)
