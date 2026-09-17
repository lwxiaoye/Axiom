"""RunInstruction V2：Run 内序号、指令类型与失败原因。

Revision ID: runtime_0007_instruction_v2
Revises: runtime_0006_execution_params
Create Date: 2026-07-23
"""
from alembic import op

revision = "runtime_0007_instruction_v2"
down_revision = "runtime_0006_execution_params"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_run_inputs "
        "ADD COLUMN IF NOT EXISTS input_sequence INTEGER"
    )
    op.execute(
        "ALTER TABLE agent_run_inputs "
        "ADD COLUMN IF NOT EXISTS input_type VARCHAR(24)"
    )
    op.execute(
        "ALTER TABLE agent_run_inputs "
        "ADD COLUMN IF NOT EXISTS failure_reason TEXT"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_run_inputs_run_seq "
        "ON agent_run_inputs (run_id, input_sequence) "
        "WHERE input_sequence IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_agent_run_inputs_run_seq")
    op.execute(
        "ALTER TABLE agent_run_inputs "
        "DROP COLUMN IF EXISTS failure_reason"
    )
    op.execute(
        "ALTER TABLE agent_run_inputs "
        "DROP COLUMN IF EXISTS input_type"
    )
    op.execute(
        "ALTER TABLE agent_run_inputs "
        "DROP COLUMN IF EXISTS input_sequence"
    )
