"""Remove the retired main-chat graph runtime and normalize Harness naming.

Revision ID: runtime_0013_harness_cutover
Revises: runtime_0012_agent_harness
"""

from alembic import op


revision = "runtime_0013_harness_cutover"
down_revision = "runtime_0012_agent_harness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This checkout is still in development; old Run history is intentionally not interpreted.
    op.execute(
        "UPDATE agent_runs SET status = 'cancelled', "
        "error = COALESCE(error, '主对话架构已切换为 Agent Harness'), "
        "completed_at = COALESCE(completed_at, NOW()) "
        "WHERE status IN ('created','routing','running','waiting_user',"
        "'waiting_confirmation','waiting_system','waiting_clarification')"
    )
    op.execute(
        "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS agent_mode VARCHAR(16) "
        "NOT NULL DEFAULT 'standard'"
    )
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_name='agent_runs' AND column_name='execution_mode') THEN "
        "EXECUTE $sql$UPDATE agent_runs SET agent_mode = CASE "
        "WHEN execution_mode = 'research' THEN 'research' "
        "WHEN execution_mode IN ('task','plan') THEN 'plan' "
        "ELSE 'standard' END$sql$; "
        "END IF; END $$"
    )
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS execution_mode")
    op.execute("DROP TABLE IF EXISTS agent_task_attempts")
    op.execute("DROP TABLE IF EXISTS agent_task_nodes")
    op.execute("DROP TABLE IF EXISTS agent_task_graphs")
    op.execute("DROP TABLE IF EXISTS agent_task_briefs")


def downgrade() -> None:
    # Retired execution state is intentionally not recreated.
    pass
