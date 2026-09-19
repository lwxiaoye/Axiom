"""Drop the workflow evaluation trace table.

工作流编排整体下线（用户不自建智能体），开发者可见的工作流评估运行记录随之删除；
对应 ORM 模型 WorkflowEvaluationRun 已从 app/runtime_models.py 移除。

Revision ID: runtime_0024_drop_eval_runs
Revises: runtime_0023_eval_runs
Create Date: 2026-09-19
"""
from alembic import op


revision = "runtime_0024_drop_eval_runs"
down_revision = "runtime_0023_eval_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS workflow_evaluation_runs")


def downgrade() -> None:
    """重建表结构（runtime_0023 的形态），数据不可恢复。"""
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
