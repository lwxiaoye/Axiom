"""Add origin-bound, browser-scoped publisher embed keys.

Revision ID: mysql_0017_embed_access_keys
Revises: mysql_0016_merge_publisher_audit
Create Date: 2026-09-11
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0017_embed_access_keys"
down_revision = "mysql_0016_merge_publisher_audit"
branch_labels = None
depends_on = None


def _column_exists(bind, column: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_api_access_key' AND COLUMN_NAME = :column",
    ), {"column": column}).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, "key_kind"):
        bind.execute(sa.text("ALTER TABLE agent_api_access_key ADD COLUMN key_kind VARCHAR(16) NOT NULL DEFAULT 'api'"))
    if not _column_exists(bind, "embed_origin"):
        bind.execute(sa.text("ALTER TABLE agent_api_access_key ADD COLUMN embed_origin VARCHAR(255) NULL"))
    bind.execute(sa.text("UPDATE agent_api_access_key SET key_kind = 'api' WHERE key_kind IS NULL OR key_kind = ''"))


def downgrade() -> None:
    pass
