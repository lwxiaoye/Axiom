"""Scope workflow results without manufacturing a main Harness Run.

Revision ID: runtime_0020_workflow_results
Revises: runtime_0019_prompt_history
"""

from alembic import op

revision = "runtime_0020_workflow_results"
down_revision = "runtime_0019_prompt_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_tool_result_blobs ADD COLUMN IF NOT EXISTS workflow_execution_id VARCHAR(64)")
    op.execute("ALTER TABLE agent_tool_result_blobs ALTER COLUMN run_id DROP NOT NULL")
    op.execute("""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_tool_result_execution_owner') THEN
            ALTER TABLE agent_tool_result_blobs ADD CONSTRAINT ck_tool_result_execution_owner CHECK (
                (run_id IS NOT NULL AND workflow_execution_id IS NULL) OR
                (run_id IS NULL AND workflow_execution_id IS NOT NULL));
        END IF;
    END $$""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_tool_result_blobs_workflow_call ON agent_tool_result_blobs (workflow_execution_id, call_id)")


def downgrade() -> None:
    # Preserve existing workflow results and the main Run FK. Older binaries only query run_id.
    # Removing this scope would require deleting recoverable user data, which is not a rollback.
    pass
