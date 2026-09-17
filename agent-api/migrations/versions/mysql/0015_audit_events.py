"""Add the unified cross-domain audit event ledger.

Revision ID: mysql_0015_audit_events
Revises: mysql_0014_conversation_logs
Create Date: 2026-09-10
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0015_audit_events"
down_revision = "mysql_0014_conversation_logs"
branch_labels = None
depends_on = None


def _table_exists(bind, table: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
    ), {"table": table}).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "agent_audit_events"):
        return
    bind.execute(sa.text(
        """
        CREATE TABLE agent_audit_events (
            id VARCHAR(64) NOT NULL,
            tenant_id VARCHAR(32) NOT NULL DEFAULT '0',
            category VARCHAR(32) NOT NULL,
            action VARCHAR(128) NOT NULL,
            resource VARCHAR(255) NOT NULL DEFAULT '',
            status VARCHAR(24) NOT NULL DEFAULT 'success',
            actor_user_id VARCHAR(64) NOT NULL,
            actor_username VARCHAR(128) NOT NULL DEFAULT '',
            ip VARCHAR(64) NOT NULL DEFAULT '',
            detail VARCHAR(512) NOT NULL DEFAULT '',
            source VARCHAR(32) NOT NULL DEFAULT 'ui',
            create_time DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY idx_agent_audit_events_tenant_time (tenant_id, create_time),
            KEY idx_agent_audit_events_category_time (category, create_time),
            KEY idx_agent_audit_events_actor_time (actor_user_id, create_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    ))


def downgrade() -> None:
    # Preserve audit evidence on rollback.
    pass
