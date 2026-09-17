"""Persist developer-visible workflow evaluation traces.

Revision ID: runtime_0023_eval_runs
Revises: runtime_0022_external_sessions
"""
from alembic import op


revision = "runtime_0023_eval_runs"
down_revision = "runtime_0022_external_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS workflow_evaluation_runs (
        id VARCHAR(64) PRIMARY KEY,
        app_id VARCHAR(64) NOT NULL,
        user_id VARCHAR(64) NOT NULL,
        mode VARCHAR(32) NOT NULL DEFAULT 'execute',
        definition_hash VARCHAR(64) NOT NULL DEFAULT '',
        preview_only BOOLEAN NOT NULL DEFAULT FALSE,
        status VARCHAR(32) NOT NULL DEFAULT 'running',
        input_text TEXT,
        variables JSONB NOT NULL DEFAULT '{}'::jsonb,
        output TEXT,
        error_message TEXT,
        duration_ms BIGINT,
        node_runs JSONB NOT NULL DEFAULT '[]'::jsonb,
        outputs JSONB NOT NULL DEFAULT '{}'::jsonb,
        edges JSONB NOT NULL DEFAULT '[]'::jsonb,
        started_at TIMESTAMP NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workflow_evaluation_runs_app_id ON workflow_evaluation_runs (app_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workflow_evaluation_runs_user_id ON workflow_evaluation_runs (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workflow_evaluation_runs_status ON workflow_evaluation_runs (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workflow_evaluation_runs_app_started ON workflow_evaluation_runs (app_id, started_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workflow_evaluation_runs_app_status ON workflow_evaluation_runs (app_id, status)")


def downgrade() -> None:
    # Evaluation records are audit evidence.  They intentionally survive downgrade.
    pass
