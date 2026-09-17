"""Persist the sender of user-role messages in subagent conversations.

Revision ID: mysql_0004_message_sender_type
Revises: mysql_0003_thread_models
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0004_message_sender_type"
down_revision = "mysql_0003_thread_models"
branch_labels = None
depends_on = None


def _has_column(bind, column: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ai_chat_messages' "
        "AND COLUMN_NAME = :column"
    ), {"column": column}).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "sender_type"):
        bind.execute(sa.text(
            "ALTER TABLE ai_chat_messages ADD COLUMN sender_type VARCHAR(24) NULL"
        ))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "sender_type"):
        bind.execute(sa.text(
            "ALTER TABLE ai_chat_messages DROP COLUMN sender_type"
        ))
