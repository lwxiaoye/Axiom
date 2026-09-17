"""Persist per-thread model settings and the most recently accepted Run model.

Revision ID: mysql_0003_thread_models
Revises: mysql_0002_subagent_discovery
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa

revision = "mysql_0003_thread_models"
down_revision = "mysql_0002_subagent_discovery"
branch_labels = None
depends_on = None


def _has_column(bind, column: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ai_chat_threads' "
        "AND COLUMN_NAME = :column"
    ), {"column": column}).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "model"):
        bind.execute(sa.text(
            "ALTER TABLE ai_chat_threads ADD COLUMN model VARCHAR(255) NULL"
        ))
    if not _has_column(bind, "last_run_model"):
        bind.execute(sa.text(
            "ALTER TABLE ai_chat_threads ADD COLUMN last_run_model VARCHAR(255) NULL"
        ))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "last_run_model"):
        bind.execute(sa.text(
            "ALTER TABLE ai_chat_threads DROP COLUMN last_run_model"
        ))
    if _has_column(bind, "model"):
        bind.execute(sa.text(
            "ALTER TABLE ai_chat_threads DROP COLUMN model"
        ))
