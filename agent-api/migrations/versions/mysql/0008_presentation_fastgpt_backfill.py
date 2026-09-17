"""Backfill presentation assignments from both persisted workflow envelopes.

Revision ID: mysql_0008_preset_backfill
Revises: mysql_0007_presentation
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0008_preset_backfill"
down_revision = "mysql_0007_presentation"
branch_labels = None
depends_on = None


def _table_exists(bind, table: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
    ), {"table": table}).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "agent_presentation_assignment"):
        return
    bind.execute(sa.text(
        """
        INSERT INTO agent_presentation_assignment
            (id, app_id, tenant_id, draft_preset_key, published_preset_key,
             draft_updated_by, published_version)
        SELECT
            MD5(CONCAT('presentation:', a.id)), a.id, COALESCE(NULLIF(a.tenant_id, ''), '0'),
            CASE
              WHEN JSON_VALID(d.draft_json) AND COALESCE(
                JSON_UNQUOTE(JSON_EXTRACT(d.draft_json, '$.chatConfig.presentation.preset')),
                JSON_UNQUOTE(JSON_EXTRACT(d.draft_json, '$.fastgpt.chatConfig.presentation.preset'))
              ) = 'campus-welcome-v1'
              THEN 'campus-welcome-v1' ELSE 'default'
            END,
            CASE
              WHEN JSON_VALID(d.published_json) AND COALESCE(
                JSON_UNQUOTE(JSON_EXTRACT(d.published_json, '$.chatConfig.presentation.preset')),
                JSON_UNQUOTE(JSON_EXTRACT(d.published_json, '$.fastgpt.chatConfig.presentation.preset'))
              ) = 'campus-welcome-v1'
              THEN 'campus-welcome-v1' ELSE 'default'
            END,
            a.owner_user_id, COALESCE(d.published_version, 0)
        FROM agent_workflow_app a
        JOIN agent_workflow_definition d ON d.app_id = a.id
        ON DUPLICATE KEY UPDATE
            tenant_id = VALUES(tenant_id),
            draft_preset_key = VALUES(draft_preset_key),
            published_preset_key = VALUES(published_preset_key),
            draft_updated_by = VALUES(draft_updated_by),
            published_version = VALUES(published_version)
        """
    ))


def downgrade() -> None:
    # Data-only compatibility backfill.  Reverting it would erase legitimate assignments written
    # after the migration, so downgrade intentionally leaves rows untouched.
    pass
