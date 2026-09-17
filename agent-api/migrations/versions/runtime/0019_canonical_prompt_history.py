"""Bind canonical Provider history to the durable public chat transcript.

Revision ID: runtime_0019_prompt_history
Revises: runtime_0018_thread_projection
"""

from alembic import op


revision = "runtime_0019_prompt_history"
down_revision = "runtime_0018_thread_projection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_thread_context_ledgers "
        "ADD COLUMN IF NOT EXISTS display_history_count BIGINT NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE agent_thread_context_ledgers "
        "ADD COLUMN IF NOT EXISTS display_history_hash VARCHAR(64) NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE agent_thread_context_ledgers "
        "ADD COLUMN IF NOT EXISTS expected_reset_count BIGINT NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE agent_thread_context_ledgers "
        "ADD COLUMN IF NOT EXISTS unexpected_reset_count BIGINT NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE agent_thread_projection_rollout_cohorts "
        "ADD COLUMN IF NOT EXISTS expected_reset_count BIGINT NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE agent_thread_projection_rollout_cohorts "
        "ADD COLUMN IF NOT EXISTS unexpected_reset_count BIGINT NOT NULL DEFAULT 0"
    )
    # Version 1 rows cannot prove which public transcript their Provider-only items belong to.
    # Keep the diagnostic payload, but force a fresh shadow baseline before reuse or canary.
    op.execute(
        "UPDATE agent_thread_context_ledgers SET "
        "version = 2, mode = 'shadow', epoch_reason = 'history_replaced', "
        "display_history_count = 0, display_history_hash = '', "
        "shadow_eligible_pairs = 0, alignment_error_count = 0, "
        "expected_reset_count = 0, unexpected_reset_count = 0, "
        "canary_completed_runs = 0, canary_error_count = 0"
    )
    op.execute(
        "UPDATE agent_thread_projection_rollout_cohorts SET "
        "shadow_clean_pairs = 0, alignment_error_count = 0, "
        "expected_reset_count = 0, unexpected_reset_count = 0, "
        "canary_clean_runs = 0, canary_error_count = 0, updated_at = NOW()"
    )


def downgrade() -> None:
    # Canonical transcript fingerprints are retained. Old binaries ignore the extra columns and
    # keeping them avoids silently re-authorizing an unbound Provider history after re-upgrade.
    pass
