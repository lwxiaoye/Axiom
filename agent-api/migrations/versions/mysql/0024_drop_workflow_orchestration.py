"""Drop the workflow orchestration tables (user-built workflow agents, sub-agents, public Agent API).

工作流编排整体下线：所有智能体都是代码内置、预置在广场的三个（校园百事通 / 演示文稿助手 /
面试助手），用户不自建智能体，线上没有已发布的工作流应用。删除只为「定义/发布/审核/执行
用户自建工作流图」与「把它暴露给外部系统」存在的表；对应 ORM 模型已从 app/models.py 移除。

保留：app_info / app_role / app_dept（内置智能体的广场上架记录与 ACL）、agent_tool_gateway_call
（主对话敏感工具审批）、ai_chat_threads 上的 app_id / parent_thread_id / subagent_id 遗留列
（核心会话表不做破坏性变更，主对话按 IS NULL 过滤存量行）。

Revision ID: mysql_0024_drop_orchestration
Revises: mysql_0023_drop_skins
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa


revision = "mysql_0024_drop_orchestration"
down_revision = "mysql_0023_drop_skins"
branch_labels = None
depends_on = None


# 先子后父：外部交互/会话文件引用外部会话，外部会话与调用记录引用 API Key 与应用，
# Key/ACL/审计/版本/定义引用应用，Registry 引用应用。（均为逻辑引用，无数据库级外键。）
DROPPED_TABLES = (
    "agent_external_interaction",
    "agent_external_session_file",
    "agent_external_session",
    "agent_api_invocation",
    "agent_api_access_key",
    "agent_workflow_admin_audit",
    "agent_workflow_acl",
    "agent_workflow_version",
    "agent_workflow_definition",
    "agent_workflow_app",
    "agent_capability_registry",
    "app_info_capability_registry",
    "ai_agent_index_event",
)


def _table_exists(bind, table: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
    ), {"table": table}).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    for table in DROPPED_TABLES:
        if _table_exists(bind, table):
            bind.execute(sa.text(f"DROP TABLE {table}"))


# ---- downgrade：按删除前 ORM 的最终形态重建表结构（数据不可恢复）----

_CREATE = {
    "agent_workflow_app": """
        CREATE TABLE agent_workflow_app (
            id VARCHAR(64) NOT NULL,
            tenant_id VARCHAR(32) NULL,
            ai_app_type VARCHAR(32) NOT NULL,
            name VARCHAR(128) NOT NULL,
            description VARCHAR(512) NULL,
            app_category VARCHAR(64) NULL,
            app_icon VARCHAR(512) NULL,
            config_json TEXT NULL,
            status VARCHAR(32) NOT NULL,
            owner_user_id VARCHAR(64) NOT NULL,
            owner_username VARCHAR(128) NULL,
            published_at DATETIME NULL,
            published_by VARCHAR(64) NULL,
            api_enabled TINYINT(1) NOT NULL DEFAULT 0,
            iframe_embed_enabled TINYINT(1) NOT NULL DEFAULT 0,
            create_time DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
            update_time DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY ix_agent_workflow_app_owner_user_id (owner_user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "agent_workflow_definition": """
        CREATE TABLE agent_workflow_definition (
            id VARCHAR(64) NOT NULL,
            app_id VARCHAR(64) NOT NULL,
            draft_json MEDIUMTEXT NULL,
            published_json MEDIUMTEXT NULL,
            published_version INT NULL,
            status VARCHAR(32) NULL,
            create_time DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
            update_time DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY ix_agent_workflow_definition_app_id (app_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "agent_workflow_version": """
        CREATE TABLE agent_workflow_version (
            id VARCHAR(64) NOT NULL,
            app_id VARCHAR(64) NOT NULL,
            version_no INT NOT NULL,
            ai_app_type VARCHAR(32) NULL,
            definition_json MEDIUMTEXT NULL,
            config_json TEXT NULL,
            status VARCHAR(24) NOT NULL,
            change_note VARCHAR(1024) NULL,
            visible_role_ids TEXT NULL,
            visible_dept_ids TEXT NULL,
            publish_channels TEXT NULL,
            embed_origins_json TEXT NULL,
            routing_json MEDIUMTEXT NULL,
            submitted_by VARCHAR(64) NULL,
            submitted_by_name VARCHAR(128) NULL,
            submitted_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_by VARCHAR(64) NULL,
            reviewed_by_name VARCHAR(128) NULL,
            reviewed_at DATETIME NULL,
            review_comment VARCHAR(1024) NULL,
            published_at DATETIME NULL,
            create_time DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY ix_agent_workflow_version_app_id (app_id),
            KEY ix_agent_workflow_version_status (status),
            KEY ix_agent_workflow_version_submitted_by (submitted_by)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "agent_workflow_acl": """
        CREATE TABLE agent_workflow_acl (
            id VARCHAR(64) NOT NULL,
            app_id VARCHAR(64) NOT NULL,
            subject_type VARCHAR(16) NOT NULL,
            subject_id VARCHAR(64) NOT NULL,
            permission VARCHAR(16) NOT NULL,
            create_time DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY ix_agent_workflow_acl_app_id (app_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "agent_workflow_admin_audit": """
        CREATE TABLE agent_workflow_admin_audit (
            id VARCHAR(64) NOT NULL,
            tenant_id VARCHAR(32) NOT NULL DEFAULT '0',
            app_id VARCHAR(64) NOT NULL,
            action VARCHAR(32) NOT NULL,
            actor_user_id VARCHAR(64) NOT NULL,
            actor_username VARCHAR(128) NULL,
            target_user_id VARCHAR(64) NULL,
            reason VARCHAR(512) NULL,
            before_json TEXT NULL,
            after_json TEXT NULL,
            create_time DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY ix_agent_workflow_admin_audit_tenant_id (tenant_id),
            KEY ix_agent_workflow_admin_audit_app_id (app_id),
            KEY ix_agent_workflow_admin_audit_action (action),
            KEY ix_agent_workflow_admin_audit_create_time (create_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "agent_api_access_key": """
        CREATE TABLE agent_api_access_key (
            id VARCHAR(64) NOT NULL,
            app_id VARCHAR(64) NOT NULL,
            owner_user_id VARCHAR(64) NOT NULL,
            name VARCHAR(128) NOT NULL DEFAULT '',
            key_prefix VARCHAR(20) NOT NULL,
            secret_hash VARCHAR(128) NOT NULL,
            secret_ciphertext TEXT NULL,
            key_kind VARCHAR(16) NOT NULL DEFAULT 'api',
            embed_origin VARCHAR(255) NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'active',
            expires_at DATETIME NULL,
            revoked_at DATETIME NULL,
            last_used_at DATETIME NULL,
            created_by VARCHAR(64) NULL,
            created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uq_agent_api_access_key_secret_hash (secret_hash),
            KEY ix_agent_api_access_key_app_id (app_id),
            KEY ix_agent_api_access_key_owner_user_id (owner_user_id),
            KEY ix_agent_api_access_key_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "agent_api_invocation": """
        CREATE TABLE agent_api_invocation (
            id VARCHAR(64) NOT NULL,
            app_id VARCHAR(64) NOT NULL,
            version_id VARCHAR(64) NOT NULL,
            api_key_id VARCHAR(64) NOT NULL,
            owner_user_id VARCHAR(64) NOT NULL,
            external_session_id VARCHAR(64) NULL,
            source VARCHAR(16) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'running',
            request_user VARCHAR(255) NULL,
            http_status INT NULL,
            error_code VARCHAR(128) NULL,
            error_message VARCHAR(512) NULL,
            started_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
            first_byte_at DATETIME NULL,
            completed_at DATETIME NULL,
            first_byte_latency_ms BIGINT NULL,
            latency_ms BIGINT NULL,
            input_tokens BIGINT NULL,
            output_tokens BIGINT NULL,
            reasoning_tokens BIGINT NULL,
            usage_known TINYINT(1) NOT NULL DEFAULT 0,
            provider_amount_raw TEXT NULL,
            provider_amount_unit VARCHAR(32) NULL,
            PRIMARY KEY (id),
            KEY ix_agent_api_invocation_app_id (app_id),
            KEY ix_agent_api_invocation_version_id (version_id),
            KEY ix_agent_api_invocation_api_key_id (api_key_id),
            KEY ix_agent_api_invocation_owner_user_id (owner_user_id),
            KEY ix_agent_api_invocation_external_session_id (external_session_id),
            KEY ix_agent_api_invocation_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "agent_external_session": """
        CREATE TABLE agent_external_session (
            id VARCHAR(64) NOT NULL,
            app_id VARCHAR(64) NOT NULL,
            published_version INT NULL,
            visitor_id VARCHAR(64) NULL,
            origin VARCHAR(512) NULL,
            api_key_id VARCHAR(64) NOT NULL,
            owner_user_id VARCHAR(64) NOT NULL,
            session_id VARCHAR(128) NOT NULL,
            workspace_ref VARCHAR(255) NOT NULL,
            status VARCHAR(24) NOT NULL DEFAULT 'active',
            expires_at DATETIME NULL,
            created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uq_agent_external_session_scope (app_id, api_key_id, session_id),
            KEY ix_agent_external_session_app_id (app_id),
            KEY ix_agent_external_session_visitor_id (visitor_id),
            KEY ix_agent_external_session_api_key_id (api_key_id),
            KEY ix_agent_external_session_owner_user_id (owner_user_id),
            KEY ix_agent_external_session_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "agent_external_session_file": """
        CREATE TABLE agent_external_session_file (
            id VARCHAR(64) NOT NULL,
            external_session_id VARCHAR(64) NOT NULL,
            storage_ref VARCHAR(512) NOT NULL,
            original_name VARCHAR(255) NOT NULL,
            mime_type VARCHAR(255) NULL,
            size_bytes BIGINT NOT NULL DEFAULT 0,
            status VARCHAR(24) NOT NULL DEFAULT 'ready',
            created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY ix_agent_external_session_file_external_session_id (external_session_id),
            KEY ix_agent_external_session_file_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "agent_external_interaction": """
        CREATE TABLE agent_external_interaction (
            id VARCHAR(64) NOT NULL,
            external_session_id VARCHAR(64) NOT NULL,
            run_id VARCHAR(64) NOT NULL,
            node_id VARCHAR(128) NULL,
            kind VARCHAR(32) NOT NULL,
            schema_json MEDIUMTEXT NOT NULL,
            submitted_value_json MEDIUMTEXT NULL,
            status VARCHAR(24) NOT NULL DEFAULT 'pending',
            expires_at DATETIME NULL,
            submitted_at DATETIME NULL,
            created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY ix_agent_external_interaction_external_session_id (external_session_id),
            KEY ix_agent_external_interaction_run_id (run_id),
            KEY ix_agent_external_interaction_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "agent_capability_registry": """
        CREATE TABLE agent_capability_registry (
            app_id VARCHAR(64) NOT NULL,
            tenant_id VARCHAR(32) NOT NULL DEFAULT '0',
            name VARCHAR(128) NOT NULL,
            description VARCHAR(512) NULL,
            ai_app_type VARCHAR(32) NULL,
            source_system VARCHAR(32) NULL,
            execution_scope VARCHAR(32) NULL,
            runtime_type VARCHAR(32) NULL,
            published_version INT NULL,
            enabled SMALLINT NULL,
            health VARCHAR(16) NULL,
            capabilities_json TEXT NULL,
            routing_json MEDIUMTEXT NULL,
            route_text_hash VARCHAR(64) NULL,
            index_version INT NOT NULL DEFAULT 1,
            owner_user_id VARCHAR(64) NULL,
            updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (app_id),
            KEY ix_agent_capability_registry_source_system (source_system),
            KEY ix_agent_capability_registry_owner_user_id (owner_user_id),
            KEY idx_capreg_tenant_route (tenant_id, source_system, execution_scope, enabled, health)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "app_info_capability_registry": """
        CREATE TABLE app_info_capability_registry (
            id VARCHAR(36) NOT NULL,
            app_info_id VARCHAR(36) NOT NULL,
            tenant_id VARCHAR(32) NULL,
            capability_code VARCHAR(64) NOT NULL,
            capability_name VARCHAR(128) NULL,
            source_system VARCHAR(32) NULL,
            source_app_id VARCHAR(64) NULL,
            source_published_version INT NULL,
            route_description TEXT NULL,
            trigger_examples TEXT NULL,
            negative_examples TEXT NULL,
            tags VARCHAR(255) NULL,
            capability_type VARCHAR(32) NULL,
            execution_scope VARCHAR(32) NULL,
            provider VARCHAR(64) NULL,
            data_sharing_policy VARCHAR(32) NULL,
            launch_mode VARCHAR(32) NULL,
            runtime_type VARCHAR(32) NULL,
            endpoint VARCHAR(500) NULL,
            remote_app_id VARCHAR(128) NULL,
            secret_ref VARCHAR(128) NULL,
            protocol_version VARCHAR(32) NULL,
            input_schema MEDIUMTEXT NULL,
            output_schema MEDIUMTEXT NULL,
            risk_level VARCHAR(16) NULL,
            approval_policy VARCHAR(32) NULL,
            enabled SMALLINT NULL,
            version INT NULL,
            timeout_seconds INT NULL,
            health_status VARCHAR(16) NULL,
            owner VARCHAR(64) NULL,
            create_by VARCHAR(50) NULL,
            create_time DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
            update_by VARCHAR(50) NULL,
            update_time DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY ix_app_info_capability_registry_app_info_id (app_info_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "ai_agent_index_event": """
        CREATE TABLE ai_agent_index_event (
            event_id VARCHAR(64) NOT NULL,
            event_type VARCHAR(32) NULL,
            agent_id VARCHAR(64) NULL,
            source_version BIGINT NULL,
            processed_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (event_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
}


def downgrade() -> None:
    """重建表结构（删除前 ORM 的最终形态），数据不可恢复。先父后子。"""
    bind = op.get_bind()
    for table in reversed(DROPPED_TABLES):
        if not _table_exists(bind, table):
            bind.execute(sa.text(_CREATE[table]))
