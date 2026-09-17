"""Attach publisher Agent API invocations to runtime model audits.

Revision ID: runtime_0021_publisher_api
Revises: runtime_0020_workflow_results
"""
from alembic import op


revision = "runtime_0021_publisher_api"
down_revision = "runtime_0020_workflow_results"
branch_labels = None
depends_on = None


def _add_attribution_columns(table: str) -> None:
    for column in (
        "external_invocation_id VARCHAR(64)",
        "external_key_id VARCHAR(64)",
        "external_app_id VARCHAR(64)",
        "external_owner_user_id VARCHAR(64)",
    ):
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column}")


def upgrade() -> None:
    for table in ("agent_model_logical_calls", "agent_model_attempt_audits"):
        _add_attribution_columns(table)

    for table in ("agent_model_logical_calls", "agent_model_attempt_audits"):
        op.execute(
            "CREATE INDEX IF NOT EXISTS "
            f"ix_{table}_external_invocation_id ON {table} (external_invocation_id)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS "
            f"ix_{table}_external_app_id ON {table} (external_app_id)"
        )


def downgrade() -> None:
    # Audit linkage remains durable; older binaries ignore the optional columns.
    pass
