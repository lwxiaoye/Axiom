"""子智能体候选语义发现（2026-07-22）：路由元数据列 + 召回索引 + 存量对齐。

- agent_workflow_version：+routing_json（发布时冻结的路由元数据快照）
- agent_capability_registry：+tenant_id / routing_json / route_text_hash / index_version，
  复合索引 (tenant_id, source_system, execution_scope, enabled, health)
- app_role / app_dept（Java 侧同步表，可能不存在）：补 (tenant_id, role_id/dept_id, app_id)
  或至少 (role_id/dept_id, app_id) 索引——完整 ACL 一次查询的支撑
- 回填：registry.tenant_id ← workflow_app.tenant_id；published_version 与
  workflow_definition.published_version 对齐；无 published_json / 零版本的
  agent_workbench 条目删除（与 sync_from_app 的移除语义一致）
- routing_json / route_text_hash 的内容回填在应用层 capability_registry.backfill_all
  （需计算路由文本 hash 并重建 Qdrant 索引，不在迁移里做）

全部幂等：执行前检查表/列/索引是否存在，可在已有环境安全重放。

Revision ID: mysql_0002_subagent_discovery
Revises: mysql_0001_baseline
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa

revision = "mysql_0002_subagent_discovery"
down_revision = "mysql_0001_baseline"
branch_labels = None
depends_on = None


def _scalar(bind, sql: str, **params):
    return bind.execute(sa.text(sql), params).scalar()


def _table_exists(bind, table: str) -> bool:
    return bool(_scalar(
        bind,
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t", t=table))


def _has_column(bind, table: str, column: str) -> bool:
    return bool(_scalar(
        bind,
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c",
        t=table, c=column))


def _has_index(bind, table: str, index: str) -> bool:
    return bool(_scalar(
        bind,
        "SELECT COUNT(*) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i",
        t=table, i=index))


def _add_column(bind, table: str, column: str, ddl: str) -> None:
    if _table_exists(bind, table) and not _has_column(bind, table, column):
        bind.execute(sa.text(f"ALTER TABLE `{table}` ADD COLUMN {ddl}"))


def _add_index(bind, table: str, index: str, columns: str) -> None:
    if _table_exists(bind, table) and not _has_index(bind, table, index):
        bind.execute(sa.text(f"CREATE INDEX `{index}` ON `{table}` ({columns})"))


def upgrade() -> None:
    bind = op.get_bind()

    # ---- agent_workflow_version：路由元数据快照 ----
    _add_column(bind, "agent_workflow_version", "routing_json",
                "`routing_json` MEDIUMTEXT NULL")

    # ---- agent_capability_registry：租户 + 路由元数据 + 索引版本 ----
    _add_column(bind, "agent_capability_registry", "tenant_id",
                "`tenant_id` VARCHAR(32) NOT NULL DEFAULT '0'")
    _add_column(bind, "agent_capability_registry", "routing_json",
                "`routing_json` MEDIUMTEXT NULL")
    _add_column(bind, "agent_capability_registry", "route_text_hash",
                "`route_text_hash` VARCHAR(64) NULL")
    _add_column(bind, "agent_capability_registry", "index_version",
                "`index_version` INT NOT NULL DEFAULT 1")
    _add_index(bind, "agent_capability_registry", "idx_capreg_tenant_route",
               "`tenant_id`, `source_system`, `execution_scope`, `enabled`, `health`")

    # ---- app_role / app_dept：完整 ACL 一次查询的索引（Java 表可能不存在，存在才补）----
    for table, id_col in (("app_role", "role_id"), ("app_dept", "dept_id")):
        if not _table_exists(bind, table):
            continue
        if not (_has_column(bind, table, "app_id") and _has_column(bind, table, id_col)):
            continue
        if _has_column(bind, table, "tenant_id"):
            _add_index(bind, table, f"idx_{table}_tenant_subject_app",
                       f"`tenant_id`, `{id_col}`, `app_id`")
        else:
            _add_index(bind, table, f"idx_{table}_subject_app", f"`{id_col}`, `app_id`")

    # ---- 存量对齐（幂等）----
    if not _table_exists(bind, "agent_capability_registry"):
        return
    # tenant_id ← workflow_app.tenant_id（空/NULL 归一为 '0'）
    if _table_exists(bind, "agent_workflow_app"):
        bind.execute(sa.text(
            "UPDATE agent_capability_registry r "
            "JOIN agent_workflow_app a ON a.id = r.app_id "
            "SET r.tenant_id = COALESCE(NULLIF(a.tenant_id, ''), '0')"
        ))
    if _table_exists(bind, "agent_workflow_definition"):
        # published_version 必须与 definition.published_version 一致
        bind.execute(sa.text(
            "UPDATE agent_capability_registry r "
            "JOIN agent_workflow_definition d ON d.app_id = r.app_id "
            "SET r.published_version = COALESCE(d.published_version, 0) "
            "WHERE r.source_system = 'agent_workbench' "
            "AND r.published_version <> COALESCE(d.published_version, 0)"
        ))
        # 没有 published_json / 零版本指针的内部条目：删除（与 sync_from_app 移除语义一致）
        bind.execute(sa.text(
            "DELETE r FROM agent_capability_registry r "
            "LEFT JOIN agent_workflow_definition d ON d.app_id = r.app_id "
            "WHERE r.source_system = 'agent_workbench' "
            "AND (d.app_id IS NULL OR d.published_json IS NULL OR d.published_json = '' "
            "OR COALESCE(d.published_version, 0) = 0)"
        ))


def downgrade() -> None:
    bind = op.get_bind()
    for table, index in (
        ("agent_capability_registry", "idx_capreg_tenant_route"),
        ("app_role", "idx_app_role_tenant_subject_app"),
        ("app_role", "idx_app_role_subject_app"),
        ("app_dept", "idx_app_dept_tenant_subject_app"),
        ("app_dept", "idx_app_dept_subject_app"),
    ):
        if _table_exists(bind, table) and _has_index(bind, table, index):
            bind.execute(sa.text(f"DROP INDEX `{index}` ON `{table}`"))
    for table, column in (
        ("agent_workflow_version", "routing_json"),
        ("agent_capability_registry", "routing_json"),
        ("agent_capability_registry", "route_text_hash"),
        ("agent_capability_registry", "index_version"),
        ("agent_capability_registry", "tenant_id"),
    ):
        if _table_exists(bind, table) and _has_column(bind, table, column):
            bind.execute(sa.text(f"ALTER TABLE `{table}` DROP COLUMN `{column}`"))
