"""Cross-Run thread projection shadow ledger.

Revision ID: runtime_0018_thread_projection
Revises: runtime_0017_tool_results
"""

from alembic import op


revision = "runtime_0018_thread_projection"
down_revision = "runtime_0017_tool_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS agent_thread_context_ledgers (
            id VARCHAR(64) PRIMARY KEY,
            thread_id VARCHAR(64) NOT NULL,
            model VARCHAR(255) NOT NULL,
            transport VARCHAR(32) NOT NULL,
            context_epoch INTEGER NOT NULL DEFAULT 0,
            epoch_reason VARCHAR(64) NOT NULL DEFAULT 'initial',
            base_prompt_hash VARCHAR(64) NOT NULL DEFAULT '',
            tool_schema_hash VARCHAR(64) NOT NULL DEFAULT '',
            state_snapshot_hash VARCHAR(64) NOT NULL DEFAULT '',
            source_cursor BIGINT NOT NULL DEFAULT 0,
            source_history_hash VARCHAR(64) NOT NULL DEFAULT '',
            projected_items JSONB NOT NULL DEFAULT '[]'::jsonb,
            mode VARCHAR(16) NOT NULL DEFAULT 'shadow',
            storage_revision BIGINT NOT NULL DEFAULT 0,
            last_projected_run_id VARCHAR(64),
            last_canary_candidate_run_id VARCHAR(64),
            last_canary_accounted_run_id VARCHAR(64),
            last_canary_error_run_id VARCHAR(64),
            shadow_eligible_pairs BIGINT NOT NULL DEFAULT 0,
            alignment_error_count BIGINT NOT NULL DEFAULT 0,
            canary_completed_runs BIGINT NOT NULL DEFAULT 0,
            canary_error_count BIGINT NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP DEFAULT NOW()
        )"""
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_thread_context_ledger_scope "
        "ON agent_thread_context_ledgers (thread_id, model, transport)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_thread_context_ledgers_thread_id "
        "ON agent_thread_context_ledgers (thread_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_thread_context_ledgers_last_run "
        "ON agent_thread_context_ledgers (last_projected_run_id)"
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS agent_thread_projection_rollout_cohorts (
            model VARCHAR(255) NOT NULL,
            transport VARCHAR(32) NOT NULL,
            shadow_clean_pairs BIGINT NOT NULL DEFAULT 0,
            alignment_error_count BIGINT NOT NULL DEFAULT 0,
            canary_clean_runs BIGINT NOT NULL DEFAULT 0,
            canary_error_count BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (model, transport)
        )"""
    )


def downgrade() -> None:
    # Shadow projection state is retained so a downgrade cannot silently lose comparison history.
    pass
