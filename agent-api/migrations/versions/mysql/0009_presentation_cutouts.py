"""Upgrade the campus preset to true-alpha illustration assets and three-rail styling.

Revision ID: mysql_0009_presentation_cutouts
Revises: mysql_0008_preset_backfill
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0009_presentation_cutouts"
down_revision = "mysql_0008_preset_backfill"
branch_labels = None
depends_on = None


NEW_HASHES = {
    "backpack": "5ae12ffe682761093fa29ff90392195434c5580af337e2c5d46e33c73102a977",
    "student-group": "b575b77ef16f32dc9ed09c626a350c40802c17df52c187270651a40691f76c2b",
}
OLD_HASHES = {
    "backpack": "960c7cbabbe6008f46f2ad6a3bcb383befef1e9a972b00e21ddc4e5d3f3c33e2",
    "student-group": "b76ed065876a0bd48ada5571174d6dac0827b149604b53432763908832e84f83",
}


def _table_exists(bind, table: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
    ), {"table": table}).scalar())


def _apply(version: int, description: str, hashes: dict[str, str]) -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "agent_presentation_preset"):
        return
    bind.execute(sa.text(
        """
        UPDATE agent_presentation_preset
        SET version_no = :version, description = :description
        WHERE preset_key = 'campus-welcome-v1'
        """
    ), {"version": version, "description": description})
    if not _table_exists(bind, "agent_presentation_preset_asset"):
        return
    for asset_key, sha256 in hashes.items():
        bind.execute(sa.text(
            """
            UPDATE agent_presentation_preset_asset
            SET sha256 = :sha256, status = 'active'
            WHERE preset_key = 'campus-welcome-v1' AND asset_key = :asset_key
            """
        ), {"asset_key": asset_key, "sha256": sha256})


def upgrade() -> None:
    _apply(2, "校园蓝图贯穿三栏，迎新插画使用透明抠图", NEW_HASHES)


def downgrade() -> None:
    _apply(1, "校园蓝图背景与迎新插画输入框", OLD_HASHES)
