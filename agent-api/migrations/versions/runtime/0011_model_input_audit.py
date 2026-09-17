"""Append-only model-visible input audit ledger.

Revision ID: runtime_0011_model_input_audit
Revises: runtime_0010_agent_runtime_v2
"""
from alembic import op


revision = "runtime_0011_model_input_audit"
down_revision = "runtime_0010_agent_runtime_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS agent_model_input_audits (
            id VARCHAR(64) PRIMARY KEY,
            run_id VARCHAR(64) NOT NULL,
            thread_id VARCHAR(64),
            request_id VARCHAR(64) NOT NULL,
            request_sequence INTEGER NOT NULL DEFAULT 0,
            model VARCHAR(255) NOT NULL DEFAULT '',
            source_manifest JSON NOT NULL DEFAULT '[]'::json,
            visible_payload JSON NOT NULL DEFAULT '{}'::json,
            payload_hash VARCHAR(64) NOT NULL,
            shadow_hash VARCHAR(64) NOT NULL,
            match_status VARCHAR(16) NOT NULL DEFAULT 'match',
            mismatch_detail JSON,
            created_at TIMESTAMP DEFAULT NOW()
        )"""
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_model_input_audit_request "
        "ON agent_model_input_audits (run_id, request_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_model_input_audits_run_id "
        "ON agent_model_input_audits (run_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_model_input_audits_thread_id "
        "ON agent_model_input_audits (thread_id)"
    )


def downgrade() -> None:
    # Audit history is intentionally append-only and is never dropped automatically.
    pass

