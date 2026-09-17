"""Append-only model cache usage audit.

Revision ID: runtime_0015_model_cache_audit
Revises: runtime_0014_run_inputs
"""
from alembic import op


revision = "runtime_0015_model_cache_audit"
down_revision = "runtime_0014_run_inputs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS agent_model_cache_audits (
            id VARCHAR(64) PRIMARY KEY,
            run_id VARCHAR(64) NOT NULL,
            thread_id VARCHAR(64),
            request_id VARCHAR(64) NOT NULL,
            request_sequence INTEGER NOT NULL DEFAULT 0,
            model VARCHAR(255) NOT NULL DEFAULT '',
            transport VARCHAR(32) NOT NULL DEFAULT '',
            context_epoch INTEGER NOT NULL DEFAULT 0,
            epoch_reason VARCHAR(64) NOT NULL DEFAULT 'initial',
            base_prompt_hash VARCHAR(64) NOT NULL DEFAULT '',
            tool_schema_hash VARCHAR(64) NOT NULL DEFAULT '',
            state_snapshot_hash VARCHAR(64) NOT NULL DEFAULT '',
            prefix_diagnostics JSON NOT NULL DEFAULT '{}'::json,
            input_tokens BIGINT NOT NULL DEFAULT 0,
            output_tokens BIGINT NOT NULL DEFAULT 0,
            cache_read_tokens BIGINT,
            cache_miss_tokens BIGINT,
            cache_write_tokens BIGINT,
            cache_miss_source VARCHAR(16) NOT NULL DEFAULT 'unknown',
            usage_schema VARCHAR(32) NOT NULL DEFAULT 'unknown',
            created_at TIMESTAMP DEFAULT NOW(),
            CONSTRAINT fk_agent_model_cache_audit_request
                FOREIGN KEY (run_id, request_id)
                REFERENCES agent_model_input_audits (run_id, request_id)
                ON DELETE CASCADE
        )"""
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_model_cache_audit_request "
        "ON agent_model_cache_audits (run_id, request_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_model_cache_audits_run_id "
        "ON agent_model_cache_audits (run_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_model_cache_audits_thread_id "
        "ON agent_model_cache_audits (thread_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_model_cache_audits_model "
        "ON agent_model_cache_audits (model)"
    )


def downgrade() -> None:
    # Cache audit history is operational evidence and is never dropped automatically.
    pass
