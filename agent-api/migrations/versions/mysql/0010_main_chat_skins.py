"""Portable main-chat skin versions, assets and campus release binding.

Revision ID: mysql_0010_main_chat_skins
Revises: mysql_0009_presentation_cutouts
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0010_main_chat_skins"
down_revision = "mysql_0009_presentation_cutouts"
branch_labels = None
depends_on = None


def _table_exists(bind, table: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
    ), {"table": table}).scalar())


def _column_exists(bind, table: str, column: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table AND COLUMN_NAME = :column"
    ), {"table": table, "column": column}).scalar())


def _index_exists(bind, table: str, index: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table AND INDEX_NAME = :index"
    ), {"table": table, "index": index}).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "agent_main_chat_skin"):
        bind.execute(sa.text(
            """
            CREATE TABLE agent_main_chat_skin (
                id VARCHAR(64) NOT NULL,
                tenant_id VARCHAR(64) NOT NULL,
                skin_key VARCHAR(64) NOT NULL,
                version VARCHAR(32) NOT NULL,
                schema_version INT NOT NULL DEFAULT 1,
                name VARCHAR(128) NOT NULL,
                description VARCHAR(512) NULL,
                renderer_key VARCHAR(64) NOT NULL,
                manifest_json MEDIUMTEXT NOT NULL,
                content_hash VARCHAR(64) NOT NULL,
                source_type VARCHAR(24) NOT NULL DEFAULT 'imported',
                status VARCHAR(16) NOT NULL DEFAULT 'active',
                installed_by VARCHAR(64) NULL,
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                KEY idx_main_chat_skin_tenant (tenant_id),
                KEY idx_main_chat_skin_status (status),
                UNIQUE KEY uq_main_chat_skin_version (tenant_id, skin_key, version),
                UNIQUE KEY uq_main_chat_skin_content (tenant_id, content_hash)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))
    if not _table_exists(bind, "agent_main_chat_skin_asset"):
        bind.execute(sa.text(
            """
            CREATE TABLE agent_main_chat_skin_asset (
                id VARCHAR(64) NOT NULL,
                skin_id VARCHAR(64) NOT NULL,
                asset_key VARCHAR(64) NOT NULL,
                asset_path VARCHAR(255) NOT NULL,
                mime_type VARCHAR(64) NOT NULL,
                sha256 VARCHAR(64) NOT NULL,
                byte_size INT NOT NULL,
                content LONGBLOB NOT NULL,
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                KEY idx_main_chat_skin_asset_skin (skin_id),
                UNIQUE KEY uq_main_chat_skin_asset_key (skin_id, asset_key),
                UNIQUE KEY uq_main_chat_skin_asset_path (skin_id, asset_path)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))
    if not _table_exists(bind, "agent_sub_agent_skin"):
        bind.execute(sa.text(
            """
            CREATE TABLE agent_sub_agent_skin (
                id VARCHAR(64) NOT NULL,
                tenant_id VARCHAR(64) NOT NULL,
                skin_key VARCHAR(64) NOT NULL,
                version VARCHAR(32) NOT NULL,
                assignment_key VARCHAR(128) NOT NULL,
                schema_version INT NOT NULL DEFAULT 1,
                name VARCHAR(128) NOT NULL,
                description VARCHAR(512) NULL,
                renderer_key VARCHAR(64) NOT NULL,
                manifest_json MEDIUMTEXT NOT NULL,
                content_hash VARCHAR(64) NOT NULL,
                source_type VARCHAR(24) NOT NULL DEFAULT 'imported',
                status VARCHAR(16) NOT NULL DEFAULT 'active',
                installed_by VARCHAR(64) NULL,
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                KEY idx_sub_agent_skin_tenant (tenant_id),
                KEY idx_sub_agent_skin_status (status),
                UNIQUE KEY uq_sub_agent_skin_version (tenant_id, skin_key, version),
                UNIQUE KEY uq_sub_agent_skin_assignment (tenant_id, assignment_key),
                UNIQUE KEY uq_sub_agent_skin_content (tenant_id, content_hash)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))
    if not _table_exists(bind, "agent_sub_agent_skin_asset"):
        bind.execute(sa.text(
            """
            CREATE TABLE agent_sub_agent_skin_asset (
                id VARCHAR(64) NOT NULL,
                skin_id VARCHAR(64) NOT NULL,
                asset_key VARCHAR(64) NOT NULL,
                asset_path VARCHAR(255) NOT NULL,
                mime_type VARCHAR(64) NOT NULL,
                sha256 VARCHAR(64) NOT NULL,
                byte_size INT NOT NULL,
                content LONGBLOB NOT NULL,
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                KEY idx_sub_agent_skin_asset_skin (skin_id),
                UNIQUE KEY uq_sub_agent_skin_asset_key (skin_id, asset_key),
                UNIQUE KEY uq_sub_agent_skin_asset_path (skin_id, asset_path)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))
    if _table_exists(bind, "agent_campus_assistant_release") and not _column_exists(
        bind, "agent_campus_assistant_release", "main_chat_skin_id"
    ):
        bind.execute(sa.text(
            "ALTER TABLE agent_campus_assistant_release "
            "ADD COLUMN main_chat_skin_id VARCHAR(64) NULL AFTER model_id"
        ))
    if _table_exists(bind, "agent_campus_assistant_release") and not _index_exists(
        bind, "agent_campus_assistant_release", "idx_campus_release_main_chat_skin"
    ):
        bind.execute(sa.text(
            "CREATE INDEX idx_campus_release_main_chat_skin "
            "ON agent_campus_assistant_release (main_chat_skin_id)"
        ))


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "agent_campus_assistant_release") and _index_exists(
        bind, "agent_campus_assistant_release", "idx_campus_release_main_chat_skin"
    ):
        bind.execute(sa.text(
            "DROP INDEX idx_campus_release_main_chat_skin ON agent_campus_assistant_release"
        ))
    if _table_exists(bind, "agent_campus_assistant_release") and _column_exists(
        bind, "agent_campus_assistant_release", "main_chat_skin_id"
    ):
        bind.execute(sa.text(
            "ALTER TABLE agent_campus_assistant_release DROP COLUMN main_chat_skin_id"
        ))
    for table in (
        "agent_sub_agent_skin_asset",
        "agent_sub_agent_skin",
        "agent_main_chat_skin_asset",
        "agent_main_chat_skin",
    ):
        if _table_exists(bind, table):
            bind.execute(sa.text(f"DROP TABLE {table}"))
