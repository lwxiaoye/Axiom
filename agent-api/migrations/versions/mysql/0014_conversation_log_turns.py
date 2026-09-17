"""Add stable turn correlation to persisted chat messages.

Revision ID: mysql_0014_conversation_logs
Revises: mysql_0013_merge
Create Date: 2026-09-08
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0014_conversation_logs"
down_revision = "mysql_0013_merge"
branch_labels = None
depends_on = None


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
    if not _column_exists(bind, "ai_chat_messages", "turn_id"):
        bind.execute(sa.text("ALTER TABLE ai_chat_messages ADD COLUMN turn_id VARCHAR(64) NULL"))
    if not _index_exists(bind, "ai_chat_messages", "ix_chat_messages_thread_turn_role"):
        bind.execute(sa.text(
            "CREATE INDEX ix_chat_messages_thread_turn_role "
            "ON ai_chat_messages (thread_id, turn_id, role, id)"
        ))
    if not _index_exists(bind, "ai_chat_messages", "ix_chat_messages_thread_role_created"):
        bind.execute(sa.text(
            "CREATE INDEX ix_chat_messages_thread_role_created "
            "ON ai_chat_messages (thread_id, role, created_at, id)"
        ))


def downgrade() -> None:
    # 该迁移只为历史消息增加可选关联字段，降级时不破坏已写入的审计数据。
    pass
