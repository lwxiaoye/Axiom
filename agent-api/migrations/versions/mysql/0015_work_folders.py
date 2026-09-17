"""Persist the explicit working folder of a chat Thread."""
from alembic import op
import sqlalchemy as sa

revision = "mysql_0015_work_folders"
down_revision = "mysql_0014_conversation_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The baseline adopts Base.metadata, which already contains these on a new DB.
    inspector = sa.inspect(op.get_bind())
    if "workspace_folder_id" not in {column["name"] for column in inspector.get_columns("ai_chat_threads")}:
        op.add_column("ai_chat_threads", sa.Column("workspace_folder_id", sa.String(64), nullable=True))
    if "ix_ai_chat_threads_workspace_folder_id" not in {index["name"] for index in inspector.get_indexes("ai_chat_threads")}:
        op.create_index("ix_ai_chat_threads_workspace_folder_id", "ai_chat_threads", ["workspace_folder_id"])


def downgrade() -> None:
    # Preserve Thread bindings when rolling application code back.
    pass
