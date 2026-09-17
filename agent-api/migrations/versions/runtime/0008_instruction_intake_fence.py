"""RunInstruction 受理闸门与消息精确关联。

Revision ID: runtime_0008_instruction_fence
Revises: runtime_0007_instruction_v2
Create Date: 2026-07-23
"""
from alembic import op

revision = "runtime_0008_instruction_fence"
down_revision = "runtime_0007_instruction_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_runs "
        "ADD COLUMN IF NOT EXISTS input_intake_closed_at TIMESTAMP"
    )
    op.execute(
        "ALTER TABLE agent_run_inputs "
        "ADD COLUMN IF NOT EXISTS source_message_id INTEGER"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_run_inputs_source_message "
        "ON agent_run_inputs (source_message_id) "
        "WHERE source_message_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_run_inputs_source_message")
    op.execute(
        "ALTER TABLE agent_run_inputs "
        "DROP COLUMN IF EXISTS source_message_id"
    )
    op.execute(
        "ALTER TABLE agent_runs "
        "DROP COLUMN IF EXISTS input_intake_closed_at"
    )
