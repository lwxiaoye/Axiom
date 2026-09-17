"""Persist publisher-only Agent API release and invocation metadata.

Revision ID: mysql_0015_publisher_agent_api
Revises: mysql_0014_conversation_logs
Create Date: 2026-09-10
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0015_publisher_agent_api"
down_revision = "mysql_0014_conversation_logs"
branch_labels = None
depends_on = None


def _scalar(bind, sql: str, **params):
    return bind.execute(sa.text(sql), params).scalar()


def _table_exists(bind, table: str) -> bool:
    return bool(_scalar(
        bind,
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table",
        table=table,
    ))


def _column_exists(bind, table: str, column: str) -> bool:
    return bool(_scalar(
        bind,
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table AND COLUMN_NAME = :column",
        table=table,
        column=column,
    ))


def _index_exists(bind, table: str, index: str) -> bool:
    return bool(_scalar(
        bind,
        "SELECT COUNT(*) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table AND INDEX_NAME = :index",
        table=table,
        index=index,
    ))


def _add_column(bind, table: str, column: str, ddl: str) -> None:
    if _table_exists(bind, table) and not _column_exists(bind, table, column):
        bind.execute(sa.text(f"ALTER TABLE `{table}` ADD COLUMN {ddl}"))


def _add_index(bind, table: str, index: str, columns: str, *, unique: bool = False) -> None:
    if _table_exists(bind, table) and not _index_exists(bind, table, index):
        qualifier = "UNIQUE " if unique else ""
        bind.execute(sa.text(f"CREATE {qualifier}INDEX `{index}` ON `{table}` ({columns})"))


def _create_access_key_table(bind) -> None:
    if _table_exists(bind, "agent_api_access_key"):
        return
    bind.execute(sa.text(
        """
        CREATE TABLE agent_api_access_key (
            id VARCHAR(64) NOT NULL,
            app_id VARCHAR(64) NOT NULL,
            owner_user_id VARCHAR(64) NOT NULL,
            name VARCHAR(128) NOT NULL DEFAULT '',
            key_prefix VARCHAR(20) NOT NULL,
            secret_hash VARCHAR(128) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'active',
            expires_at DATETIME NULL,
            revoked_at DATETIME NULL,
            last_used_at DATETIME NULL,
            created_by VARCHAR(64) NULL,
            created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uq_agent_api_access_key_secret_hash (secret_hash),
            KEY ix_agent_api_access_key_app_id (app_id),
            KEY ix_agent_api_access_key_owner_user_id (owner_user_id),
            KEY ix_agent_api_access_key_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    ))


def _create_invocation_table(bind) -> None:
    if _table_exists(bind, "agent_api_invocation"):
        return
    bind.execute(sa.text(
        """
        CREATE TABLE agent_api_invocation (
            id VARCHAR(64) NOT NULL,
            app_id VARCHAR(64) NOT NULL,
            version_id VARCHAR(64) NOT NULL,
            api_key_id VARCHAR(64) NOT NULL,
            owner_user_id VARCHAR(64) NOT NULL,
            source VARCHAR(16) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'running',
            request_user VARCHAR(255) NULL,
            http_status INT NULL,
            error_code VARCHAR(128) NULL,
            error_message VARCHAR(512) NULL,
            started_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
            first_byte_at DATETIME NULL,
            completed_at DATETIME NULL,
            first_byte_latency_ms BIGINT NULL,
            latency_ms BIGINT NULL,
            input_tokens BIGINT NULL,
            output_tokens BIGINT NULL,
            reasoning_tokens BIGINT NULL,
            usage_known TINYINT(1) NOT NULL DEFAULT 0,
            provider_amount_raw TEXT NULL,
            provider_amount_unit VARCHAR(32) NULL,
            PRIMARY KEY (id),
            KEY ix_agent_api_invocation_app_id (app_id),
            KEY ix_agent_api_invocation_version_id (version_id),
            KEY ix_agent_api_invocation_api_key_id (api_key_id),
            KEY ix_agent_api_invocation_owner_user_id (owner_user_id),
            KEY ix_agent_api_invocation_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    ))


def _ensure_access_key_columns(bind) -> None:
    for column, ddl in (
        ("app_id", "`app_id` VARCHAR(64) NOT NULL"),
        ("owner_user_id", "`owner_user_id` VARCHAR(64) NOT NULL"),
        ("name", "`name` VARCHAR(128) NOT NULL DEFAULT ''"),
        ("key_prefix", "`key_prefix` VARCHAR(20) NOT NULL"),
        ("secret_hash", "`secret_hash` VARCHAR(128) NOT NULL"),
        ("status", "`status` VARCHAR(16) NOT NULL DEFAULT 'active'"),
        ("expires_at", "`expires_at` DATETIME NULL"),
        ("revoked_at", "`revoked_at` DATETIME NULL"),
        ("last_used_at", "`last_used_at` DATETIME NULL"),
        ("created_by", "`created_by` VARCHAR(64) NULL"),
        ("created_at", "`created_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP"),
    ):
        _add_column(bind, "agent_api_access_key", column, ddl)
    _add_index(bind, "agent_api_access_key", "uq_agent_api_access_key_secret_hash", "`secret_hash`", unique=True)
    _add_index(bind, "agent_api_access_key", "ix_agent_api_access_key_app_id", "`app_id`")
    _add_index(bind, "agent_api_access_key", "ix_agent_api_access_key_owner_user_id", "`owner_user_id`")
    _add_index(bind, "agent_api_access_key", "ix_agent_api_access_key_status", "`status`")


def _ensure_invocation_columns(bind) -> None:
    for column, ddl in (
        ("app_id", "`app_id` VARCHAR(64) NOT NULL"),
        ("version_id", "`version_id` VARCHAR(64) NOT NULL"),
        ("api_key_id", "`api_key_id` VARCHAR(64) NOT NULL"),
        ("owner_user_id", "`owner_user_id` VARCHAR(64) NOT NULL"),
        ("source", "`source` VARCHAR(16) NOT NULL"),
        ("status", "`status` VARCHAR(16) NOT NULL DEFAULT 'running'"),
        ("request_user", "`request_user` VARCHAR(255) NULL"),
        ("http_status", "`http_status` INT NULL"),
        ("error_code", "`error_code` VARCHAR(128) NULL"),
        ("error_message", "`error_message` VARCHAR(512) NULL"),
        ("started_at", "`started_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP"),
        ("first_byte_at", "`first_byte_at` DATETIME NULL"),
        ("completed_at", "`completed_at` DATETIME NULL"),
        ("first_byte_latency_ms", "`first_byte_latency_ms` BIGINT NULL"),
        ("latency_ms", "`latency_ms` BIGINT NULL"),
        ("input_tokens", "`input_tokens` BIGINT NULL"),
        ("output_tokens", "`output_tokens` BIGINT NULL"),
        ("reasoning_tokens", "`reasoning_tokens` BIGINT NULL"),
        ("usage_known", "`usage_known` TINYINT(1) NOT NULL DEFAULT 0"),
        ("provider_amount_raw", "`provider_amount_raw` TEXT NULL"),
        ("provider_amount_unit", "`provider_amount_unit` VARCHAR(32) NULL"),
    ):
        _add_column(bind, "agent_api_invocation", column, ddl)
    _add_index(bind, "agent_api_invocation", "ix_agent_api_invocation_app_id", "`app_id`")
    _add_index(bind, "agent_api_invocation", "ix_agent_api_invocation_version_id", "`version_id`")
    _add_index(bind, "agent_api_invocation", "ix_agent_api_invocation_api_key_id", "`api_key_id`")
    _add_index(bind, "agent_api_invocation", "ix_agent_api_invocation_owner_user_id", "`owner_user_id`")
    _add_index(bind, "agent_api_invocation", "ix_agent_api_invocation_status", "`status`")


def upgrade() -> None:
    bind = op.get_bind()

    _add_column(
        bind,
        "agent_workflow_version",
        "publish_channels",
        "`publish_channels` MEDIUMTEXT NULL",
    )
    _add_column(
        bind,
        "agent_workflow_version",
        "embed_origins_json",
        "`embed_origins_json` MEDIUMTEXT NULL",
    )
    if _table_exists(bind, "agent_workflow_version"):
        bind.execute(sa.text(
            "UPDATE agent_workflow_version SET publish_channels = '[\"marketplace\"]' "
            "WHERE publish_channels IS NULL"
        ))

    _create_access_key_table(bind)
    _ensure_access_key_columns(bind)
    _create_invocation_table(bind)
    _ensure_invocation_columns(bind)


def downgrade() -> None:
    # Invocation and access-key records are audit evidence. Removing fields or tables would
    # discard cost attribution, so old binaries safely ignore this additive schema.
    pass
