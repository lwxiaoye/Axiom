"""Persist immutable terminal execution-trace projections with chat history.

Revision ID: mysql_0005_trace_projection
Revises: mysql_0004_message_sender_type
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0005_trace_projection"
down_revision = "mysql_0004_message_sender_type"
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
    if not _has_column(bind, "execution_trace_json"):
        bind.execute(sa.text(
            "ALTER TABLE ai_chat_messages "
            "ADD COLUMN execution_trace_json MEDIUMTEXT NULL"
        ))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "execution_trace_json"):
        bind.execute(sa.text(
            "ALTER TABLE ai_chat_messages DROP COLUMN execution_trace_json"
        ))
