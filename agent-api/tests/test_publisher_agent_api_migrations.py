from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_publisher_api_migrations_define_storage_and_heads():
    mysql = (ROOT / "migrations/versions/mysql/0015_publisher_agent_api.py").read_text(
        encoding="utf-8"
    )
    runtime = (ROOT / "migrations/versions/runtime/0021_publisher_api_attribution.py").read_text(
        encoding="utf-8"
    )

    assert 'revision = "mysql_0015_publisher_agent_api"' in mysql
    assert 'down_revision = "mysql_0014_conversation_logs"' in mysql
    assert "agent_api_access_key" in mysql
    assert "agent_api_invocation" in mysql
    assert "publish_channels" in mysql
    assert "embed_origins_json" in mysql
    assert "WHERE publish_channels IS NULL" in mysql
    assert "information_schema" in mysql

    assert 'revision = "runtime_0021_publisher_api"' in runtime
    assert 'down_revision = "runtime_0020_workflow_results"' in runtime
    assert "external_invocation_id" in runtime
    assert "external_key_id" in runtime
    assert "external_app_id" in runtime
    assert "external_owner_user_id" in runtime
    assert "ADD COLUMN IF NOT EXISTS" in runtime

    external_sessions = (ROOT / "migrations/versions/mysql/0019_external_agent_sessions.py").read_text(
        encoding="utf-8"
    )
    external_runtime = (ROOT / "migrations/versions/runtime/0022_external_agent_sessions.py").read_text(
        encoding="utf-8"
    )
    assert 'revision = "mysql_0019_external_sessions"' in external_sessions
    assert 'down_revision = "mysql_0018_public_api_config"' in external_sessions
    assert "agent_external_session" in external_sessions
    assert "agent_external_session_file" in external_sessions
    assert "agent_external_interaction" in external_sessions
    assert 'revision = "runtime_0022_external_sessions"' in external_runtime
    assert 'down_revision = "runtime_0021_publisher_api"' in external_runtime
    assert "external_session_id" in external_runtime

    recoverable_keys = (ROOT / "migrations/versions/mysql/0020_recoverable_agent_api_keys.py").read_text(
        encoding="utf-8"
    )
    assert 'revision = "mysql_0020_recoverable_agent_api_keys"' in recoverable_keys
    assert 'down_revision = "mysql_0019_external_sessions"' in recoverable_keys
    assert "secret_ciphertext" in recoverable_keys

    reconcile_sessions = (ROOT / "migrations/versions/mysql/0021_reconcile_external_agent_session.py").read_text(
        encoding="utf-8"
    )
    assert 'revision = "mysql_0021_external_session_fix"' in reconcile_sessions
    assert 'down_revision = "mysql_0020_recoverable_agent_api_keys"' in reconcile_sessions
    for column in ("published_version", "visitor_id", "origin", "api_key_id", "owner_user_id", "workspace_ref"):
        assert column in reconcile_sessions
    assert "uq_agent_external_session_scope" in reconcile_sessions


def test_publisher_api_orm_exposes_release_key_invocation_and_attribution_contracts():
    from app.core.database import MYSQL_SCHEMA_HEAD
    from app.core.runtime_db import RUNTIME_SCHEMA_HEAD
    from app.models import (
        AgentApiAccessKey,
        AgentApiInvocation,
        ExternalAgentInteraction,
        ExternalAgentSession,
        ExternalAgentSessionFile,
        WorkflowApp,
        WorkflowVersion,
    )
    from app.runtime_models import AgentModelAttemptAudit, AgentModelLogicalCall

    assert MYSQL_SCHEMA_HEAD == "mysql_0021_external_session_fix"
    assert RUNTIME_SCHEMA_HEAD == "runtime_0023_eval_runs"
    assert AgentApiAccessKey.__tablename__ == "agent_api_access_key"
    assert AgentApiInvocation.__tablename__ == "agent_api_invocation"
    assert "secret_ciphertext" in AgentApiAccessKey.__table__.c
    assert {"published_version", "visitor_id", "origin"} <= set(ExternalAgentSession.__table__.c.keys())
    assert any(
        constraint.__class__.__name__ == "UniqueConstraint"
        and tuple(constraint.columns.keys()) == ("secret_hash",)
        for constraint in AgentApiAccessKey.__table__.constraints
    )
    assert "publish_channels" in WorkflowVersion.__table__.c
    assert "embed_origins_json" in WorkflowVersion.__table__.c
    assert "api_enabled" in WorkflowApp.__table__.c
    assert "iframe_embed_enabled" in WorkflowApp.__table__.c

    for model in (AgentModelLogicalCall, AgentModelAttemptAudit):
        for column in (
            "external_invocation_id",
            "external_key_id",
            "external_app_id",
            "external_owner_user_id",
            "external_session_id",
        ):
            assert column in model.__table__.c

    for column in (
        "started_at",
        "first_byte_at",
        "completed_at",
        "input_tokens",
        "output_tokens",
        "usage_known",
        "provider_amount_raw",
    ):
        assert column in AgentApiInvocation.__table__.c

    assert {"app_id", "api_key_id", "session_id", "workspace_ref", "status"} <= set(
        ExternalAgentSession.__table__.c.keys()
    )
    assert {"external_session_id", "storage_ref", "original_name"} <= set(
        ExternalAgentSessionFile.__table__.c.keys()
    )
    assert {"external_session_id", "run_id", "kind", "schema_json", "status"} <= set(
        ExternalAgentInteraction.__table__.c.keys()
    )
