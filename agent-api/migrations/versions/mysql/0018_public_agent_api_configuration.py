"""Move publisher API exposure from release channels to app-level public configuration.

Revision ID: mysql_0018_public_api_config
Revises: mysql_0017_embed_access_keys
Create Date: 2026-09-11
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0018_public_api_config"
down_revision = "mysql_0017_embed_access_keys"
branch_labels = None
depends_on = None


def _column_exists(bind, column: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_workflow_app' AND COLUMN_NAME = :column",
    ), {"column": column}).scalar())


def _table_exists(bind) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_workflow_app'",
    )).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    # 2026-09-19：工作流编排整体删除后 ORM 不再建 agent_workflow_app，全新库跑到这里时
    # 表不存在；存量库行为不变（表在则照旧补列），mysql_0024 随后整表删除。
    if not _table_exists(bind):
        return
    if not _column_exists(bind, "api_enabled"):
        bind.execute(sa.text(
            "ALTER TABLE agent_workflow_app ADD COLUMN api_enabled TINYINT(1) NOT NULL DEFAULT 0"
        ))
    if not _column_exists(bind, "iframe_embed_enabled"):
        bind.execute(sa.text(
            "ALTER TABLE agent_workflow_app ADD COLUMN iframe_embed_enabled TINYINT(1) NOT NULL DEFAULT 0"
        ))


def downgrade() -> None:
    # Keep public exposure state on downgrade; old binaries ignore additive columns.
    pass
