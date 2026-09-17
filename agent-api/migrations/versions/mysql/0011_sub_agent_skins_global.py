"""Make the portable sub-agent skin catalog global.

Revision ID: mysql_0011_global_sub_skins
Revises: mysql_0010_main_chat_skins
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0011_global_sub_skins"
down_revision = "mysql_0010_main_chat_skins"
branch_labels = None
depends_on = None


def _table_exists(bind, table: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
    ), {"table": table}).scalar())


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


def _first_conflict(bind, columns: str):
    return bind.execute(sa.text(
        f"SELECT {columns}, COUNT(*) AS total, COUNT(DISTINCT content_hash) AS variants "
        "FROM agent_sub_agent_skin "
        f"GROUP BY {columns} HAVING total > 1 AND variants > 1 LIMIT 1"
    )).mappings().first()


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "agent_sub_agent_skin") and _column_exists(
        bind, "agent_sub_agent_skin", "tenant_id"
    ):
        version_conflict = _first_conflict(bind, "skin_key, version")
        assignment_conflict = _first_conflict(bind, "assignment_key")
        conflict = version_conflict or assignment_conflict
        if conflict:
            identity = conflict.get("assignment_key") or (
                f"{conflict.get('skin_key')}@{conflict.get('version')}"
            )
            raise RuntimeError(
                "Cannot globalize sub-agent skins: conflicting packages share identity "
                f"{identity}. Export or remove one conflicting version before retrying."
            )

        # Equal package bytes may have been imported once per tenant. Keep the oldest installation;
        # assignments use the deterministic assignment_key, so every existing reference remains valid.
        rows = bind.execute(sa.text(
            "SELECT id, content_hash FROM agent_sub_agent_skin "
            "ORDER BY created_at ASC, id ASC"
        )).mappings().all()
        keep_by_hash: dict[str, str] = {}
        duplicate_ids: list[str] = []
        for row in rows:
            content_hash = str(row["content_hash"])
            if content_hash in keep_by_hash:
                duplicate_ids.append(str(row["id"]))
            else:
                keep_by_hash[content_hash] = str(row["id"])
        for skin_id in duplicate_ids:
            bind.execute(sa.text(
                "DELETE FROM agent_sub_agent_skin_asset WHERE skin_id = :skin_id"
            ), {"skin_id": skin_id})
            bind.execute(sa.text(
                "DELETE FROM agent_sub_agent_skin WHERE id = :skin_id"
            ), {"skin_id": skin_id})

        for index in (
            "uq_sub_agent_skin_version",
            "uq_sub_agent_skin_assignment",
            "uq_sub_agent_skin_content",
            "idx_sub_agent_skin_tenant",
        ):
            if _index_exists(bind, "agent_sub_agent_skin", index):
                bind.execute(sa.text(f"DROP INDEX {index} ON agent_sub_agent_skin"))
        bind.execute(sa.text("ALTER TABLE agent_sub_agent_skin DROP COLUMN tenant_id"))
        bind.execute(sa.text(
            "CREATE UNIQUE INDEX uq_sub_agent_skin_version "
            "ON agent_sub_agent_skin (skin_key, version)"
        ))
        bind.execute(sa.text(
            "CREATE UNIQUE INDEX uq_sub_agent_skin_assignment "
            "ON agent_sub_agent_skin (assignment_key)"
        ))
        bind.execute(sa.text(
            "CREATE UNIQUE INDEX uq_sub_agent_skin_content "
            "ON agent_sub_agent_skin (content_hash)"
        ))

    for table, column, index in (
        ("agent_presentation_preset", "owner_tenant_id", "idx_presentation_preset_owner"),
        ("agent_presentation_preset_asset", "owner_tenant_id", "idx_presentation_asset_owner"),
    ):
        if _table_exists(bind, table) and _column_exists(bind, table, column):
            if _index_exists(bind, table, index):
                bind.execute(sa.text(f"DROP INDEX {index} ON {table}"))
            bind.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN {column}"))
    if _table_exists(bind, "agent_presentation_preset_grant"):
        bind.execute(sa.text("DROP TABLE agent_presentation_preset_grant"))


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "agent_presentation_preset") and not _column_exists(
        bind, "agent_presentation_preset", "owner_tenant_id"
    ):
        bind.execute(sa.text(
            "ALTER TABLE agent_presentation_preset "
            "ADD COLUMN owner_tenant_id VARCHAR(64) NOT NULL DEFAULT '0' AFTER source_type"
        ))
        bind.execute(sa.text(
            "CREATE INDEX idx_presentation_preset_owner "
            "ON agent_presentation_preset (owner_tenant_id)"
        ))
    if _table_exists(bind, "agent_presentation_preset_asset") and not _column_exists(
        bind, "agent_presentation_preset_asset", "owner_tenant_id"
    ):
        bind.execute(sa.text(
            "ALTER TABLE agent_presentation_preset_asset "
            "ADD COLUMN owner_tenant_id VARCHAR(64) NOT NULL DEFAULT '0' AFTER asset_kind"
        ))
        bind.execute(sa.text(
            "CREATE INDEX idx_presentation_asset_owner "
            "ON agent_presentation_preset_asset (owner_tenant_id)"
        ))
    if not _table_exists(bind, "agent_presentation_preset_grant"):
        bind.execute(sa.text(
            """
            CREATE TABLE agent_presentation_preset_grant (
                id VARCHAR(64) NOT NULL,
                tenant_id VARCHAR(64) NOT NULL,
                preset_key VARCHAR(64) NOT NULL,
                enabled SMALLINT NOT NULL DEFAULT 1,
                granted_by VARCHAR(64) NULL,
                granted_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME NULL,
                revoked_by VARCHAR(64) NULL,
                revoked_at DATETIME NULL,
                note VARCHAR(512) NULL,
                PRIMARY KEY (id),
                UNIQUE KEY uq_presentation_tenant_grant (tenant_id, preset_key),
                KEY idx_presentation_grant_tenant (tenant_id),
                KEY idx_presentation_grant_preset (preset_key)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))

    if _table_exists(bind, "agent_sub_agent_skin") and not _column_exists(
        bind, "agent_sub_agent_skin", "tenant_id"
    ):
        for index in (
            "uq_sub_agent_skin_version",
            "uq_sub_agent_skin_assignment",
            "uq_sub_agent_skin_content",
        ):
            if _index_exists(bind, "agent_sub_agent_skin", index):
                bind.execute(sa.text(f"DROP INDEX {index} ON agent_sub_agent_skin"))
        bind.execute(sa.text(
            "ALTER TABLE agent_sub_agent_skin "
            "ADD COLUMN tenant_id VARCHAR(64) NOT NULL DEFAULT '0' AFTER id"
        ))
        bind.execute(sa.text(
            "CREATE INDEX idx_sub_agent_skin_tenant ON agent_sub_agent_skin (tenant_id)"
        ))
        bind.execute(sa.text(
            "CREATE UNIQUE INDEX uq_sub_agent_skin_version "
            "ON agent_sub_agent_skin (tenant_id, skin_key, version)"
        ))
        bind.execute(sa.text(
            "CREATE UNIQUE INDEX uq_sub_agent_skin_assignment "
            "ON agent_sub_agent_skin (tenant_id, assignment_key)"
        ))
        bind.execute(sa.text(
            "CREATE UNIQUE INDEX uq_sub_agent_skin_content "
            "ON agent_sub_agent_skin (tenant_id, content_hash)"
        ))
