"""Persist isolated contexts for external Agent API and static embed sessions.

Revision ID: mysql_0019_external_sessions
Revises: mysql_0018_public_api_config
Create Date: 2026-09-11
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0019_external_sessions"
down_revision = "mysql_0018_public_api_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_external_session (
            id VARCHAR(64) NOT NULL,
            app_id VARCHAR(64) NOT NULL,
            api_key_id VARCHAR(64) NOT NULL,
            owner_user_id VARCHAR(64) NOT NULL,
            session_id VARCHAR(128) NOT NULL,
            workspace_ref VARCHAR(255) NOT NULL,
            status VARCHAR(24) NOT NULL DEFAULT 'active',
            expires_at DATETIME NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uq_agent_external_session_scope (app_id, api_key_id, session_id),
            KEY ix_agent_external_session_app_id (app_id),
            KEY ix_agent_external_session_api_key_id (api_key_id),
            KEY ix_agent_external_session_owner_user_id (owner_user_id),
            KEY ix_agent_external_session_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    has_invocation_session_link = bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_api_invocation' "
        "AND COLUMN_NAME = 'external_session_id'"
    )).scalar())
    if not has_invocation_session_link:
        op.execute("ALTER TABLE agent_api_invocation ADD COLUMN external_session_id VARCHAR(64) NULL")
        op.execute("CREATE INDEX ix_agent_api_invocation_external_session_id ON agent_api_invocation (external_session_id)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_external_session_file (
            id VARCHAR(64) NOT NULL,
            external_session_id VARCHAR(64) NOT NULL,
            storage_ref VARCHAR(512) NOT NULL,
            original_name VARCHAR(255) NOT NULL,
            mime_type VARCHAR(255) NULL,
            size_bytes BIGINT NOT NULL DEFAULT 0,
            status VARCHAR(24) NOT NULL DEFAULT 'ready',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY ix_agent_external_session_file_external_session_id (external_session_id),
            KEY ix_agent_external_session_file_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_external_interaction (
            id VARCHAR(64) NOT NULL,
            external_session_id VARCHAR(64) NOT NULL,
            run_id VARCHAR(64) NOT NULL,
            node_id VARCHAR(128) NULL,
            kind VARCHAR(32) NOT NULL,
            schema_json MEDIUMTEXT NOT NULL,
            submitted_value_json MEDIUMTEXT NULL,
            status VARCHAR(24) NOT NULL DEFAULT 'pending',
            expires_at DATETIME NULL,
            submitted_at DATETIME NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY ix_agent_external_interaction_external_session_id (external_session_id),
            KEY ix_agent_external_interaction_run_id (run_id),
            KEY ix_agent_external_interaction_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def downgrade() -> None:
    # External run records are audit and billing evidence; retain them for old binaries to ignore.
    pass
