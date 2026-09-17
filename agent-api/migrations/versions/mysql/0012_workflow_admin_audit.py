"""Add immutable administrator workflow governance audit events.

Revision ID: mysql_0012_workflow_admin_audit
Revises: mysql_0011_global_sub_skins
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0012_workflow_admin_audit"
down_revision = "mysql_0011_global_sub_skins"
branch_labels = None
depends_on = None


def _table_exists(bind, table: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
    ), {"table": table}).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "agent_workflow_admin_audit"):
        return
    bind.execute(sa.text(
        """
        CREATE TABLE agent_workflow_admin_audit (
            id VARCHAR(64) NOT NULL,
            tenant_id VARCHAR(32) NOT NULL DEFAULT '0',
            app_id VARCHAR(64) NOT NULL,
            action VARCHAR(32) NOT NULL,
            actor_user_id VARCHAR(64) NOT NULL,
            actor_username VARCHAR(128) NOT NULL DEFAULT '',
            target_user_id VARCHAR(64) NULL,
            reason VARCHAR(512) NOT NULL DEFAULT '',
            before_json TEXT NULL,
            after_json TEXT NULL,
            create_time DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY idx_workflow_admin_audit_tenant (tenant_id),
            KEY idx_workflow_admin_audit_app (app_id),
            KEY idx_workflow_admin_audit_action (action),
            KEY idx_workflow_admin_audit_create_time (create_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    ))


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "agent_workflow_admin_audit"):
        bind.execute(sa.text("DROP TABLE agent_workflow_admin_audit"))
