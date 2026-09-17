"""任务节点进程恢复专用的完整执行参数快照（N-08，2026-07-22）。

对外审计列 params 仍然脱敏、限深、截断；execution_params 只保存通过敏感键检查且大小有界
的原始参数，供 worker 崩溃后精确恢复。旧行保持 NULL，恢复器会拒绝自动重放，避免拿审计
投影当真实工具参数执行。

Revision ID: runtime_0006_execution_params
Revises: runtime_0005_attempt_idem
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa

revision = "runtime_0006_execution_params"
down_revision = "runtime_0005_attempt_idem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0013 移除了旧任务图运行时；对新库跳过，对存量表保持原补列行为。
    if op.get_bind().execute(sa.text("SELECT to_regclass('agent_task_nodes')")).scalar():
        op.execute(
            "ALTER TABLE agent_task_nodes "
            "ADD COLUMN IF NOT EXISTS execution_params JSON"
        )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE agent_task_nodes "
        "DROP COLUMN IF EXISTS execution_params"
    )
