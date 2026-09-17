"""Store publisher API keys encrypted so their owner can copy them again.

Revision ID: mysql_0020_recoverable_agent_api_keys
Revises: mysql_0019_external_sessions
Create Date: 2026-09-15
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0020_recoverable_agent_api_keys"
down_revision = "mysql_0019_external_sessions"
branch_labels = None
depends_on = None


def _column_exists(bind, column: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_api_access_key' AND COLUMN_NAME = :column",
    ), {"column": column}).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, "secret_ciphertext"):
        # Existing HMAC-only rows remain intentionally unrecoverable.
        bind.execute(sa.text("ALTER TABLE agent_api_access_key ADD COLUMN secret_ciphertext TEXT NULL"))


def downgrade() -> None:
    # Retain encrypted credential records; older binaries ignore this column.
    pass
