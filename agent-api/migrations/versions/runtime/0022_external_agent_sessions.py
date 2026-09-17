"""Link model audit records to isolated external Agent sessions.

Revision ID: runtime_0022_external_sessions
Revises: runtime_0021_publisher_api
Create Date: 2026-09-11
"""
from alembic import op


revision = "runtime_0022_external_sessions"
down_revision = "runtime_0021_publisher_api"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("agent_model_logical_calls", "agent_model_attempt_audits"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS external_session_id VARCHAR(64)")
        op.execute(
            "CREATE INDEX IF NOT EXISTS "
            f"ix_{table}_external_session_id ON {table} (external_session_id)"
        )


def downgrade() -> None:
    # Keep audit linkage durable; older binaries ignore the optional column.
    pass
