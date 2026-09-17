"""Rename the active-Run steering store to the canonical Harness input contract.

Revision ID: runtime_0014_run_inputs
Revises: runtime_0013_harness_cutover
"""

from alembic import op


revision = "runtime_0014_run_inputs"
down_revision = "runtime_0013_harness_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF to_regclass('agent_run_inputs') IS NULL "
        "AND to_regclass('agent_run_instructions') IS NOT NULL THEN "
        "ALTER TABLE agent_run_instructions RENAME TO agent_run_inputs; "
        "END IF; END $$"
    )
    for table, old_name, new_name in (
        ("agent_runs", "instruction_intake_closed_at", "input_intake_closed_at"),
        ("agent_run_inputs", "instruction_sequence", "input_sequence"),
        ("agent_run_inputs", "instruction_type", "input_type"),
        ("agent_run_inputs", "base_graph_version", "base_goal_revision"),
        ("agent_run_inputs", "applied_graph_version", "applied_goal_revision"),
    ):
        op.execute(
            "DO $$ BEGIN "
            f"IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='{table}' "
            f"AND column_name='{old_name}') AND NOT EXISTS "
            f"(SELECT 1 FROM information_schema.columns WHERE table_name='{table}' "
            f"AND column_name='{new_name}') THEN "
            f"ALTER TABLE {table} RENAME COLUMN {old_name} TO {new_name}; "
            "END IF; END $$"
        )
    op.execute("DROP INDEX IF EXISTS uq_agent_run_instructions_run_seq")
    op.execute("DROP INDEX IF EXISTS ix_agent_run_instructions_source_message")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_run_inputs_run_seq "
        "ON agent_run_inputs (run_id, input_sequence) "
        "WHERE input_sequence IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_run_inputs_source_message "
        "ON agent_run_inputs (source_message_id) "
        "WHERE source_message_id IS NOT NULL"
    )


def downgrade() -> None:
    # The development cutover intentionally does not recreate retired naming.
    pass
