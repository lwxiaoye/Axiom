"""Tenant-authorized run-page presentation presets.

Revision ID: mysql_0007_presentation
Revises: mysql_0006_campus_assistant
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0007_presentation"
down_revision = "mysql_0006_campus_assistant"
branch_labels = None
depends_on = None


def _table_exists(bind, table: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
    ), {"table": table}).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "agent_presentation_preset"):
        bind.execute(sa.text(
            """
            CREATE TABLE agent_presentation_preset (
                preset_key VARCHAR(64) NOT NULL,
                version_no INT NOT NULL DEFAULT 1,
                name VARCHAR(128) NOT NULL,
                description VARCHAR(512) NULL,
                source_type VARCHAR(24) NOT NULL DEFAULT 'builtin',
                owner_tenant_id VARCHAR(64) NOT NULL DEFAULT '0',
                renderer_key VARCHAR(64) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'active',
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (preset_key),
                KEY idx_presentation_preset_owner (owner_tenant_id),
                KEY idx_presentation_preset_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))
    if not _table_exists(bind, "agent_presentation_preset_asset"):
        bind.execute(sa.text(
            """
            CREATE TABLE agent_presentation_preset_asset (
                id VARCHAR(64) NOT NULL,
                preset_key VARCHAR(64) NOT NULL,
                asset_key VARCHAR(64) NOT NULL,
                asset_kind VARCHAR(24) NOT NULL DEFAULT 'image',
                owner_tenant_id VARCHAR(64) NOT NULL DEFAULT '0',
                resource_ref VARCHAR(512) NOT NULL,
                sha256 VARCHAR(64) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'active',
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                UNIQUE KEY uq_presentation_preset_asset (preset_key, asset_key),
                KEY idx_presentation_asset_preset (preset_key),
                KEY idx_presentation_asset_owner (owner_tenant_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
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
    if not _table_exists(bind, "agent_presentation_assignment"):
        bind.execute(sa.text(
            """
            CREATE TABLE agent_presentation_assignment (
                id VARCHAR(64) NOT NULL,
                app_id VARCHAR(64) NOT NULL,
                tenant_id VARCHAR(64) NOT NULL,
                draft_preset_key VARCHAR(64) NOT NULL DEFAULT 'default',
                published_preset_key VARCHAR(64) NOT NULL DEFAULT 'default',
                draft_updated_by VARCHAR(64) NULL,
                published_version INT NOT NULL DEFAULT 0,
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                UNIQUE KEY uq_presentation_assignment_app (app_id),
                KEY idx_presentation_assignment_app (app_id),
                KEY idx_presentation_assignment_tenant (tenant_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))

    # 内置衣服只登记 code-owned renderer key；数据库不能携带 CSS、HTML 或外部 URL。
    bind.execute(sa.text(
        """
        INSERT INTO agent_presentation_preset
            (preset_key, version_no, name, description, source_type, owner_tenant_id, renderer_key, status)
        VALUES
            ('campus-welcome-v1', 1, '校园迎新', '校园蓝图背景与迎新插画输入框',
             'builtin', '0', 'campus-welcome-v1', 'active')
        ON DUPLICATE KEY UPDATE
            version_no = VALUES(version_no), name = VALUES(name),
            description = VALUES(description), renderer_key = VALUES(renderer_key), status = 'active'
        """
    ))

    assets = (
        ("campus-background", "bundle://presentation/campus-welcome-v1/campus-background",
         "776717e1ae0d326d735bd0d7319f761323b060745425a9b31a37ebba9dc272b8"),
        ("backpack", "bundle://presentation/campus-welcome-v1/backpack",
         "960c7cbabbe6008f46f2ad6a3bcb383befef1e9a972b00e21ddc4e5d3f3c33e2"),
        ("student-group", "bundle://presentation/campus-welcome-v1/student-group",
         "b76ed065876a0bd48ada5571174d6dac0827b149604b53432763908832e84f83"),
        ("preview", "bundle://presentation/campus-welcome-v1/preview",
         "9293286683c1cd9e8dfd8ce92ad9aed8336f05f493cc559cd8b767cbab4981eb"),
    )
    for asset_key, resource_ref, sha256 in assets:
        bind.execute(sa.text(
            """
            INSERT INTO agent_presentation_preset_asset
                (id, preset_key, asset_key, asset_kind, owner_tenant_id, resource_ref, sha256, status)
            VALUES
                (:id, 'campus-welcome-v1', :asset_key, 'image', '0', :resource_ref, :sha256, 'active')
            ON DUPLICATE KEY UPDATE
                resource_ref = VALUES(resource_ref), sha256 = VALUES(sha256), status = 'active'
            """
        ), {
            "id": f"builtin-campus-welcome-{asset_key}",
            "asset_key": asset_key,
            "resource_ref": resource_ref,
            "sha256": sha256,
        })

    # 当前单租户环境的迎新样式已经由用户明确指定，迁移后给 tenant=0 补一条显式授权；
    # 其它客户不会自动获得，必须由平台管理员在“外观授权”中逐租户开启。
    bind.execute(sa.text(
        """
        INSERT INTO agent_presentation_preset_grant
            (id, tenant_id, preset_key, enabled, granted_by, note)
        VALUES
            ('builtin-campus-welcome-grant-tenant0', '0', 'campus-welcome-v1', 1,
             'migration', '迎新智能体首期外观授权')
        ON DUPLICATE KEY UPDATE enabled = 1, revoked_by = NULL, revoked_at = NULL
        """
    ))

    # 把已有工作流 JSON 中的选择登记成草稿/线上穿衣记录，后续保存和发布再严格校验。
    bind.execute(sa.text(
        """
        INSERT INTO agent_presentation_assignment
            (id, app_id, tenant_id, draft_preset_key, published_preset_key,
             draft_updated_by, published_version)
        SELECT
            MD5(CONCAT('presentation:', a.id)), a.id, COALESCE(NULLIF(a.tenant_id, ''), '0'),
            CASE
              WHEN JSON_VALID(d.draft_json)
               AND JSON_UNQUOTE(JSON_EXTRACT(d.draft_json, '$.chatConfig.presentation.preset')) = 'campus-welcome-v1'
              THEN 'campus-welcome-v1' ELSE 'default'
            END,
            CASE
              WHEN JSON_VALID(d.published_json)
               AND JSON_UNQUOTE(JSON_EXTRACT(d.published_json, '$.chatConfig.presentation.preset')) = 'campus-welcome-v1'
              THEN 'campus-welcome-v1' ELSE 'default'
            END,
            a.owner_user_id, COALESCE(d.published_version, 0)
        FROM agent_workflow_app a
        JOIN agent_workflow_definition d ON d.app_id = a.id
        ON DUPLICATE KEY UPDATE
            tenant_id = VALUES(tenant_id),
            draft_preset_key = VALUES(draft_preset_key),
            published_preset_key = VALUES(published_preset_key),
            published_version = VALUES(published_version)
        """
    ))


def downgrade() -> None:
    bind = op.get_bind()
    for table in (
        "agent_presentation_assignment",
        "agent_presentation_preset_grant",
        "agent_presentation_preset_asset",
        "agent_presentation_preset",
    ):
        if _table_exists(bind, table):
            bind.execute(sa.text(f"DROP TABLE {table}"))
