"""Drop the skin / run-page presentation variant system.

全站只保留一套默认样式：删除主对话皮肤、子智能体皮肤与运行页外观目录/分配的全部表，
以及校园百事通发布快照上的 main_chat_skin_id 列。对应 ORM 模型已从 app/models.py 移除。

Revision ID: mysql_0023_drop_skins
Revises: mysql_0022_merge_work_folders
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0023_drop_skins"
down_revision = "mysql_0022_merge_work_folders"
branch_labels = None
depends_on = None


# 先子后父：资源表引用皮肤表，分配/素材表引用预设表。
DROPPED_TABLES = (
    "agent_sub_agent_skin_asset",
    "agent_sub_agent_skin",
    "agent_main_chat_skin_asset",
    "agent_main_chat_skin",
    "agent_presentation_assignment",
    "agent_presentation_preset_grant",
    "agent_presentation_preset_asset",
    "agent_presentation_preset",
)


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
    if _table_exists(bind, "agent_campus_assistant_release"):
        if _index_exists(bind, "agent_campus_assistant_release", "idx_campus_release_main_chat_skin"):
            bind.execute(sa.text(
                "DROP INDEX idx_campus_release_main_chat_skin ON agent_campus_assistant_release"
            ))
        if _column_exists(bind, "agent_campus_assistant_release", "main_chat_skin_id"):
            bind.execute(sa.text(
                "ALTER TABLE agent_campus_assistant_release DROP COLUMN main_chat_skin_id"
            ))
    for table in DROPPED_TABLES:
        if _table_exists(bind, table):
            bind.execute(sa.text(f"DROP TABLE {table}"))


def downgrade() -> None:
    """重建表结构（0010 / 0011 / 0007 / 0011 之后的最终形态），数据不可恢复。"""
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
    # 子智能体皮肤自 0011 起为全局目录（无 tenant_id）。
    if not _table_exists(bind, "agent_sub_agent_skin"):
        bind.execute(sa.text(
            """
            CREATE TABLE agent_sub_agent_skin (
                id VARCHAR(64) NOT NULL,
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
                KEY idx_sub_agent_skin_status (status),
                UNIQUE KEY uq_sub_agent_skin_version (skin_key, version),
                UNIQUE KEY uq_sub_agent_skin_assignment (assignment_key),
                UNIQUE KEY uq_sub_agent_skin_content (content_hash)
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
    # 运行页外观目录 / 分配：0007 建表、0011 去掉 owner_tenant_id 与 grant 表后的形态。
    if not _table_exists(bind, "agent_presentation_preset"):
        bind.execute(sa.text(
            """
            CREATE TABLE agent_presentation_preset (
                preset_key VARCHAR(64) NOT NULL,
                version_no INT NOT NULL DEFAULT 1,
                name VARCHAR(128) NOT NULL,
                description VARCHAR(512) NULL,
                source_type VARCHAR(24) NOT NULL DEFAULT 'builtin',
                renderer_key VARCHAR(64) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'active',
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (preset_key),
                KEY idx_presentation_preset_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))
    if not _table_exists(bind, "agent_presentation_preset_asset"):
        bind.execute(sa.text(
            """
            CREATE TABLE agent_presentation_preset_asset (
                id VARCHAR(64) NOT NULL,
                preset_key VARCHAR(64) NOT NULL,
                asset_key VARCHAR(64) NOT NULL,
                asset_kind VARCHAR(24) NOT NULL DEFAULT 'image',
                resource_ref VARCHAR(512) NOT NULL,
                sha256 VARCHAR(64) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'active',
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                UNIQUE KEY uq_presentation_preset_asset (preset_key, asset_key),
                KEY idx_presentation_asset_preset (preset_key)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))
    if not _table_exists(bind, "agent_presentation_assignment"):
        bind.execute(sa.text(
            """
            CREATE TABLE agent_presentation_assignment (
                id VARCHAR(64) NOT NULL,
                app_id VARCHAR(64) NOT NULL,
                tenant_id VARCHAR(64) NOT NULL,
                draft_preset_key VARCHAR(64) NOT NULL DEFAULT 'default',
                published_preset_key VARCHAR(64) NOT NULL DEFAULT 'default',
                draft_updated_by VARCHAR(64) NULL,
                published_version INT NOT NULL DEFAULT 0,
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                UNIQUE KEY uq_presentation_assignment_app (app_id),
                KEY idx_presentation_assignment_app (app_id),
                KEY idx_presentation_assignment_tenant (tenant_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))
    if _table_exists(bind, "agent_campus_assistant_release"):
        if not _column_exists(bind, "agent_campus_assistant_release", "main_chat_skin_id"):
            bind.execute(sa.text(
                "ALTER TABLE agent_campus_assistant_release "
                "ADD COLUMN main_chat_skin_id VARCHAR(64) NULL AFTER model_id"
            ))
        if not _index_exists(bind, "agent_campus_assistant_release", "idx_campus_release_main_chat_skin"):
            bind.execute(sa.text(
                "CREATE INDEX idx_campus_release_main_chat_skin "
                "ON agent_campus_assistant_release (main_chat_skin_id)"
            ))
