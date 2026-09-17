"""MySQL 业务库 baseline（P1 2026-07-17）：接管 main.py 启动期全部隐式 DDL。

对照 app/main.py 的 create_all + _migrate_* 系列逐条搬运，全部幂等：
- 空库/新环境：Base.metadata.create_all 建齐当前模型表（模型已含全部新列，后面的补列
  探测到列已存在即跳过）；
- 已由启动期 DDL 建好的存量库：所有补列/索引/约束先查 information_schema 判存在再执行，
  `upgrade head` 是无害 no-op（只落 alembic_version_mysql 版本标记）。

此后 MIGRATE_ON_STARTUP=false 的环境以本链为唯一 MySQL schema 事实源；新 DDL 一律在
migrations/versions/mysql/ 追加迁移，禁止再回到启动期裸 ALTER。

Revision ID: mysql_0001_baseline
Revises:
Create Date: 2026-07-17
"""
from alembic import op
import sqlalchemy as sa

revision = "mysql_0001_baseline"
down_revision = None
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


def _column_data_type(bind, table: str, column: str) -> str:
    return str(_scalar(
        bind,
        "SELECT DATA_TYPE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c",
        t=table, c=column) or "").lower()


def _has_index(bind, table: str, index: str) -> bool:
    return bool(_scalar(
        bind,
        "SELECT COUNT(*) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i",
        t=table, i=index))


def _has_constraint(bind, table: str, name: str) -> bool:
    return bool(_scalar(
        bind,
        "SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND CONSTRAINT_NAME = :n",
        t=table, n=name))


# (table, column, DDL) —— 逐条对照 main.py _migrate_* 系列（MySQL 无 ADD COLUMN IF NOT
# EXISTS，靠上面的 information_schema 探测幂等）。
_ADD_COLUMNS = [
    # _migrate_embedding_model
    ("ai_embedding_model", "api_key",
     "ALTER TABLE ai_embedding_model ADD COLUMN api_key VARCHAR(512) NULL"),
    ("ai_embedding_model", "base_url",
     "ALTER TABLE ai_embedding_model ADD COLUMN base_url VARCHAR(512) NULL"),
    ("ai_embedding_model", "is_active",
     "ALTER TABLE ai_embedding_model ADD COLUMN is_active TINYINT DEFAULT 0"),
    ("ai_embedding_model", "test_status",
     "ALTER TABLE ai_embedding_model ADD COLUMN test_status VARCHAR(32) NULL"),
    ("ai_embedding_model", "test_message",
     "ALTER TABLE ai_embedding_model ADD COLUMN test_message VARCHAR(512) NULL"),
    ("ai_embedding_model", "last_test_time",
     "ALTER TABLE ai_embedding_model ADD COLUMN last_test_time DATETIME NULL"),
    # _migrate_agent_skill
    ("agent_skill_version", "package_b64",
     "ALTER TABLE agent_skill_version ADD COLUMN package_b64 MEDIUMTEXT NULL"),
    # _migrate_chat_columns —— threads 各列
    ("ai_chat_threads", "pinned",
     "ALTER TABLE ai_chat_threads ADD COLUMN pinned SMALLINT DEFAULT 0"),
    ("ai_chat_threads", "app_id",
     "ALTER TABLE ai_chat_threads ADD COLUMN app_id VARCHAR(64) NULL"),
    ("ai_chat_threads", "ai_app_type",
     "ALTER TABLE ai_chat_threads ADD COLUMN ai_app_type VARCHAR(32) NULL"),
    ("ai_chat_threads", "parent_thread_id",
     "ALTER TABLE ai_chat_threads ADD COLUMN parent_thread_id VARCHAR(64) NULL"),
    ("ai_chat_threads", "subagent_id",
     "ALTER TABLE ai_chat_threads ADD COLUMN subagent_id VARCHAR(64) NULL"),
    # _migrate_chat_thread_origin
    ("ai_chat_threads", "origin",
     "ALTER TABLE ai_chat_threads ADD COLUMN origin VARCHAR(16) NULL"),
    # _migrate_chat_columns —— messages 各列（attachments_json 直接按终态 MEDIUMTEXT 建）
    ("ai_chat_messages", "feedback",
     "ALTER TABLE ai_chat_messages ADD COLUMN feedback VARCHAR(8) NULL"),
    ("ai_chat_messages", "attachments_json",
     "ALTER TABLE ai_chat_messages ADD COLUMN attachments_json MEDIUMTEXT NULL"),
    ("ai_chat_messages", "run_id",
     "ALTER TABLE ai_chat_messages ADD COLUMN run_id VARCHAR(64) NULL"),
    ("ai_chat_messages", "status",
     "ALTER TABLE ai_chat_messages ADD COLUMN status VARCHAR(16) NULL"),
    # _migrate_workflow_publish_visibility
    ("agent_workflow_version", "visible_role_ids",
     "ALTER TABLE agent_workflow_version ADD COLUMN visible_role_ids TEXT NULL"),
    ("agent_workflow_version", "visible_dept_ids",
     "ALTER TABLE agent_workflow_version ADD COLUMN visible_dept_ids TEXT NULL"),
    # _migrate_user_file_columns
    ("agent_user_file", "folder_id",
     "ALTER TABLE agent_user_file ADD COLUMN folder_id VARCHAR(64) NULL"),
]


def upgrade() -> None:
    bind = op.get_bind()

    # 1) 建齐模型表（等价旧启动期 create_all；checkfirst 幂等，不 ALTER 既有表）
    from app.core.database import Base
    import app.models  # noqa: F401  注册所有业务表到 Base.metadata
    Base.metadata.create_all(bind)

    # 2) 存量表补列（新库上一步已按模型建出全列，这里全部探测跳过）
    for table, column, ddl in _ADD_COLUMNS:
        if _table_exists(bind, table) and not _has_column(bind, table, column):
            op.execute(ddl)

    # 3) attachments_json TEXT→MEDIUMTEXT 升级（图片缩略图 data URL 超 64KB；
    #    已是 mediumtext 则跳过——对照 main.py 的幂等 MODIFY）
    if (_table_exists(bind, "ai_chat_messages")
            and _column_data_type(bind, "ai_chat_messages", "attachments_json") == "text"):
        op.execute("ALTER TABLE ai_chat_messages MODIFY COLUMN attachments_json MEDIUMTEXT NULL")

    # 4) 消息 run 归属索引（P0 刷新丢失修复：对账回填/历史恢复按 run_id 查）
    if (_table_exists(bind, "ai_chat_messages")
            and not _has_index(bind, "ai_chat_messages", "idx_ai_chat_messages_run_id")):
        op.execute("ALTER TABLE ai_chat_messages ADD INDEX idx_ai_chat_messages_run_id (run_id)")

    # 5) 版本表唯一约束（_migrate_user_file_version_constraints，并发防护双保险）
    if (_table_exists(bind, "agent_user_file_version")
            and not _has_constraint(bind, "agent_user_file_version", "uq_user_file_version_no")):
        op.execute(
            "ALTER TABLE agent_user_file_version "
            "ADD CONSTRAINT uq_user_file_version_no UNIQUE (file_id, version_no)")

    # 6) 存量委派会话 origin 回填（_migrate_chat_thread_origin；WHERE origin IS NULL 天然幂等）
    if _table_exists(bind, "ai_chat_threads"):
        op.execute(
            "UPDATE ai_chat_threads SET origin='delegation' "
            "WHERE origin IS NULL AND app_id IS NOT NULL "
            "AND (parent_thread_id IS NOT NULL OR title LIKE '委派·%' OR title='主对话委派')")


def downgrade() -> None:
    # 采纳式 baseline 不提供结构回滚（与旧 alembic/ 0004 同哲学：保守不销毁数据/标记）。
    pass
