"""会话附件关联统一文件资产。

Revision ID: runtime_0009_attach_file
Revises: runtime_0008_instruction_fence
Create Date: 2026-07-23
"""
from alembic import op

revision = "runtime_0009_attach_file"
down_revision = "runtime_0008_instruction_fence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_thread_attachments "
        "ADD COLUMN IF NOT EXISTS file_id VARCHAR(64)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_thread_attachments_file_id "
        "ON agent_thread_attachments (file_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_thread_attachments_file_id")
    op.execute(
        "ALTER TABLE agent_thread_attachments "
        "DROP COLUMN IF EXISTS file_id"
    )
