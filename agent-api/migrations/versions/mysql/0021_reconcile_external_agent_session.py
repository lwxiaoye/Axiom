"""Reconcile legacy external-session storage with publisher API isolation.

Revision ID: mysql_0021_external_session_fix
Revises: mysql_0020_recoverable_agent_api_keys
Create Date: 2026-09-15
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0021_external_session_fix"
down_revision = "mysql_0020_recoverable_agent_api_keys"
branch_labels = None
depends_on = None


def _column_exists(bind, column: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_external_session' "
        "AND COLUMN_NAME = :column",
    ), {"column": column}).scalar())


def _index_exists(bind, index: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_external_session' "
        "AND INDEX_NAME = :index",
    ), {"index": index}).scalar())


def upgrade() -> None:
    """Keep pre-existing browser-session rows while enabling key-scoped API sessions.

    Older deployments already used ``agent_external_session`` for embed visitors.
    ``CREATE TABLE IF NOT EXISTS`` in mysql_0019 deliberately preserved those
    rows, but did not add the publisher-API columns. They remain nullable here
    because their original key ownership cannot be reconstructed safely. The
    legacy ticket metadata is also added to fresh tables: API sessions populate
    safe compatibility values when an older schema still requires them.
    """
    bind = op.get_bind()
    columns = {
        "published_version": "INT NULL",
        "visitor_id": "VARCHAR(64) NULL",
        "origin": "VARCHAR(512) NULL",
        "api_key_id": "VARCHAR(64) NULL",
        "owner_user_id": "VARCHAR(64) NULL",
        "session_id": "VARCHAR(128) NULL",
        "workspace_ref": "VARCHAR(255) NULL",
        "updated_at": "DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
    }
    for name, definition in columns.items():
        if not _column_exists(bind, name):
            op.execute(f"ALTER TABLE agent_external_session ADD COLUMN {name} {definition}")

    # The legacy table required this field, whereas key-scoped API sessions
    # intentionally have no expiry unless one is configured later.
    op.execute("ALTER TABLE agent_external_session MODIFY COLUMN expires_at DATETIME NULL")

    indexes = {
        "uq_agent_external_session_scope": (
            "CREATE UNIQUE INDEX uq_agent_external_session_scope "
            "ON agent_external_session (app_id, api_key_id, session_id)"
        ),
        "ix_agent_external_session_api_key_id": (
            "CREATE INDEX ix_agent_external_session_api_key_id "
            "ON agent_external_session (api_key_id)"
        ),
        "ix_agent_external_session_owner_user_id": (
            "CREATE INDEX ix_agent_external_session_owner_user_id "
            "ON agent_external_session (owner_user_id)"
        ),
        "ix_agent_external_session_status": (
            "CREATE INDEX ix_agent_external_session_status "
            "ON agent_external_session (status)"
        ),
    }
    for name, statement in indexes.items():
        if not _index_exists(bind, name):
            op.execute(statement)


def downgrade() -> None:
    # Legacy and external run records are retained for auditability.
    pass
