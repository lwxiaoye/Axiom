"""agent_task_attempts 幂等成功唯一索引（N-09，2026-07-22）。

idempotency_key = {graph_id}:{node_key}:{语义输入哈希}（scheduler._attempt_idempotency_key
起真正写入；attempt_no 不参与——重试/恢复不改变幂等身份）。部分唯一索引限定
status='succeeded'：同一语义工作至多成功一次，跨进程恢复的重复执行在第二次成功提交时
被 PG 拦下；失败重试不受影响。存量行 idempotency_key 均为 NULL，不受约束。

与 app/core/runtime_db.init_runtime_tables 的启动期幂等 DDL 同源（IF NOT EXISTS）。

Revision ID: runtime_0005_attempt_idem
Revises: runtime_0004_queue_ctx
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa

revision = "runtime_0005_attempt_idem"
down_revision = "runtime_0004_queue_ctx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0013 移除了旧任务图运行时；全新库不会创建 agent_task_attempts。
    # 已存在的历史表仍按原语义补索引，随后由 0013 回收。
    if op.get_bind().execute(sa.text("SELECT to_regclass('agent_task_attempts')")).scalar():
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_task_attempts_idem_success "
            "ON agent_task_attempts (idempotency_key) "
            "WHERE idempotency_key IS NOT NULL AND status = 'succeeded'"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_task_attempts_idem_success")
