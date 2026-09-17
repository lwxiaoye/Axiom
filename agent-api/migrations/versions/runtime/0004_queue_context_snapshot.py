"""agent_thread_queue TurnContext 快照列（审计项 1，2026-07-22）。

context_json：入队时刻的 Skill/知识库/我的文件/联网/模型/任务模式选择快照。派发时以本快照
为准，不读「当前全局选择」——排队期间用户改选资源不得漂移到已排队的消息上。

与 app/core/runtime_db.init_runtime_tables 的启动期幂等 DDL 同源（IF NOT EXISTS）。

Revision ID: runtime_0004_queue_ctx
Revises: runtime_0003_client_request
Create Date: 2026-07-22
"""
from alembic import op

revision = "runtime_0004_queue_ctx"
down_revision = "runtime_0003_client_request"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_thread_queue ADD COLUMN IF NOT EXISTS context_json TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE agent_thread_queue DROP COLUMN IF EXISTS context_json")
