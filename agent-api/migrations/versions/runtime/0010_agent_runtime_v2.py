"""Durable Agent Runtime V2 control plane.

Revision ID: runtime_0010_agent_runtime_v2
Revises: runtime_0009_attach_file
Create Date: 2026-08-04
"""
from alembic import op


revision = "runtime_0010_agent_runtime_v2"
down_revision = "runtime_0009_attach_file"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing runtime data remains intact. New columns are nullable/defaulted so historical
    # runs keep rendering through the legacy event projector.
    for statement in (
        "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS state_version INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS parent_run_id VARCHAR(64)",
        "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS root_run_id VARCHAR(64)",
        "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS parent_tool_call_id VARCHAR(64)",
        "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS delegation_depth INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMP",
        "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS started_at TIMESTAMP",
        "CREATE INDEX IF NOT EXISTS ix_agent_runs_parent_run_id ON agent_runs (parent_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_agent_runs_root_run_id ON agent_runs (root_run_id)",
        "ALTER TABLE agent_plan_steps ADD COLUMN IF NOT EXISTS step_key VARCHAR(64)",
        "ALTER TABLE agent_plan_steps ADD COLUMN IF NOT EXISTS detail TEXT",
        "ALTER TABLE agent_plan_steps ADD COLUMN IF NOT EXISTS required INTEGER DEFAULT 1",
        "ALTER TABLE agent_tool_calls ADD COLUMN IF NOT EXISTS observation JSON",
        "ALTER TABLE agent_tool_calls ADD COLUMN IF NOT EXISTS raw_result_ref VARCHAR(255)",
        """CREATE TABLE IF NOT EXISTS agent_run_jobs (
            id VARCHAR(64) PRIMARY KEY,
            run_id VARCHAR(64) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'queued',
            available_at TIMESTAMP,
            lease_owner VARCHAR(64),
            lease_expires_at TIMESTAMP,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            wake_reason VARCHAR(64),
            last_error TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )""",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_run_jobs_run ON agent_run_jobs (run_id)",
        "CREATE INDEX IF NOT EXISTS ix_agent_run_jobs_claim ON agent_run_jobs (status, available_at)",
        """CREATE TABLE IF NOT EXISTS agent_run_context_snapshots (
            id VARCHAR(64) PRIMARY KEY,
            run_id VARCHAR(64) NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            covered_sequence INTEGER NOT NULL DEFAULT 0,
            summary JSON NOT NULL DEFAULT '{}'::json,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_run_context_snapshot ON agent_run_context_snapshots (run_id, version)",
    ):
        op.execute(statement)

    # Acceptance happens before a Worker lease. ``created`` and ``routing`` therefore reserve the
    # Thread just like ``running``; otherwise two concurrent HTTP 202 requests can both enqueue.
    op.execute("DROP INDEX IF EXISTS uq_agent_runs_active_thread")
    op.execute(
        "CREATE UNIQUE INDEX uq_agent_runs_active_thread ON agent_runs (thread_id) "
        "WHERE status IN ('created','routing','running','waiting_user','waiting_confirmation','waiting_system')"
    )

    # Old plan tables were intentionally retained. Give any non-empty historical rows a stable
    # key before creating the V2 uniqueness constraint.
    op.execute(
        "UPDATE agent_plan_steps SET step_key = CONCAT('legacy-', seq, '-', id) "
        "WHERE step_key IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_plan_steps_plan_key "
        "ON agent_plan_steps (plan_id, step_key) WHERE step_key IS NOT NULL"
    )


def downgrade() -> None:
    # Do not drop runtime history tables or V2 columns automatically: this migration is designed
    # for a shared production database and downgrade must remain non-destructive.
    pass
