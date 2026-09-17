"""Durable full tool-result blobs.

Revision ID: runtime_0017_tool_results
Revises: runtime_0016_model_attempts
"""

from alembic import op


revision = "runtime_0017_tool_results"
down_revision = "runtime_0016_model_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS agent_tool_result_blobs (
            handle VARCHAR(64) PRIMARY KEY,
            run_id VARCHAR(64) NOT NULL,
            thread_id VARCHAR(64) NOT NULL,
            user_id VARCHAR(64) NOT NULL,
            tool_name VARCHAR(128) NOT NULL,
            call_id VARCHAR(128) NOT NULL,
            content_type VARCHAR(64) NOT NULL DEFAULT 'text/plain',
            content_hash VARCHAR(64) NOT NULL,
            utf8_bytes BIGINT NOT NULL DEFAULT 0,
            chars BIGINT NOT NULL DEFAULT 0,
            estimated_tokens BIGINT NOT NULL DEFAULT 0,
            trust VARCHAR(32) NOT NULL DEFAULT 'internal',
            source VARCHAR(64) NOT NULL DEFAULT 'tool',
            applied_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
            content TEXT NOT NULL,
            full_available BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW(),
            expires_at TIMESTAMP,
            CONSTRAINT fk_agent_tool_result_blob_run
                FOREIGN KEY (run_id)
                REFERENCES agent_runs (id)
                ON DELETE CASCADE
        )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_tool_result_blobs_run_call "
        "ON agent_tool_result_blobs (run_id, call_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_tool_result_blobs_thread_user "
        "ON agent_tool_result_blobs (thread_id, user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_tool_result_blobs_expires_at "
        "ON agent_tool_result_blobs (expires_at)"
    )


def downgrade() -> None:
    # Full results can be referenced by historical observations; never drop them implicitly.
    pass
