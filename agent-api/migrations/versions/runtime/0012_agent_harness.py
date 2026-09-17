"""Canonical Agent Harness Run and Plan contracts.

Revision ID: runtime_0012_agent_harness
Revises: runtime_0011_model_input_audit
"""

from alembic import op


revision = "runtime_0012_agent_harness"
down_revision = "runtime_0011_model_input_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in (
        "ALTER TABLE agent_plans ADD COLUMN IF NOT EXISTS goal TEXT",
        "ALTER TABLE agent_plans ADD COLUMN IF NOT EXISTS goal_revision INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE agent_plans ADD COLUMN IF NOT EXISTS plan_version INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE agent_plan_steps ADD COLUMN IF NOT EXISTS acceptance_criteria JSON",
        "ALTER TABLE agent_plan_steps ADD COLUMN IF NOT EXISTS reason TEXT",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_plans_run_version "
        "ON agent_plans (run_id, plan_version) WHERE plan_version > 0",
    ):
        op.execute(statement)


def downgrade() -> None:
    pass
