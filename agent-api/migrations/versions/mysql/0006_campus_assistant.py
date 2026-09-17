"""Campus assistant versioned configuration tables.

Revision ID: mysql_0006_campus_assistant
Revises: mysql_0005_trace_projection
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0006_campus_assistant"
down_revision = "mysql_0005_trace_projection"
branch_labels = None
depends_on = None


def _table_exists(bind, table: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
    ), {"table": table}).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "agent_campus_assistant_config"):
        bind.execute(sa.text(
            """
            CREATE TABLE agent_campus_assistant_config (
                id VARCHAR(64) NOT NULL,
                tenant_id VARCHAR(64) NOT NULL,
                current_release_id VARCHAR(64) NULL,
                draft_release_id VARCHAR(64) NULL,
                revision INT NOT NULL DEFAULT 0,
                enabled SMALLINT NOT NULL DEFAULT 1,
                created_by VARCHAR(64) NULL,
                updated_by VARCHAR(64) NULL,
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                UNIQUE KEY uq_campus_assistant_tenant (tenant_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))
    if not _table_exists(bind, "agent_campus_assistant_release"):
        bind.execute(sa.text(
            """
            CREATE TABLE agent_campus_assistant_release (
                id VARCHAR(64) NOT NULL,
                config_id VARCHAR(64) NOT NULL,
                status VARCHAR(16) NOT NULL,
                version_no INT NULL,
                base_release_id VARCHAR(64) NULL,
                rollback_from_release_id VARCHAR(64) NULL,
                model_id VARCHAR(255) NOT NULL,
                official_domains_json MEDIUMTEXT NOT NULL,
                policy_version VARCHAR(32) NOT NULL,
                change_note VARCHAR(1024) NULL,
                config_hash VARCHAR(64) NULL,
                created_by VARCHAR(64) NULL,
                published_by VARCHAR(64) NULL,
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                published_at DATETIME NULL,
                PRIMARY KEY (id),
                KEY idx_campus_release_config (config_id),
                UNIQUE KEY uq_campus_release_version (config_id, version_no)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))
    if not _table_exists(bind, "agent_campus_assistant_release_kb"):
        bind.execute(sa.text(
            """
            CREATE TABLE agent_campus_assistant_release_kb (
                id VARCHAR(64) NOT NULL,
                release_id VARCHAR(64) NOT NULL,
                knowledge_id VARCHAR(64) NOT NULL,
                knowledge_name_snapshot VARCHAR(255) NULL,
                category VARCHAR(64) NULL,
                department VARCHAR(128) NULL,
                priority INT NOT NULL DEFAULT 100,
                enabled SMALLINT NOT NULL DEFAULT 1,
                PRIMARY KEY (id),
                KEY idx_campus_release_kb (release_id),
                UNIQUE KEY uq_campus_release_kb (release_id, knowledge_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))


def downgrade() -> None:
    bind = op.get_bind()
    for table in (
        "agent_campus_assistant_release_kb",
        "agent_campus_assistant_release",
        "agent_campus_assistant_config",
    ):
        if _table_exists(bind, table):
            bind.execute(sa.text(f"DROP TABLE {table}"))
