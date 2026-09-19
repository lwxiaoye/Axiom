from pathlib import Path


RUNTIME_VERSIONS = (
    Path(__file__).resolve().parents[1] / "migrations" / "versions" / "runtime"
)


def _read(name: str) -> str:
    return (RUNTIME_VERSIONS / name).read_text(encoding="utf-8")


def test_model_attempt_migration_uses_strict_db_sequence_and_approved_table():
    text = _read("0016_model_attempts.py")
    assert 'revision = "runtime_0016_model_attempts"' in text
    assert 'down_revision = "runtime_0015_model_cache_audit"' in text
    assert "agent_model_attempt_audits" in text
    assert "CREATE TABLE IF NOT EXISTS agent_model_attempts" not in text
    assert "ON CONFLICT (run_id) DO UPDATE" in text
    assert "ROW_NUMBER() OVER" in text
    assert "PARTITION BY ima.run_id" in text
    assert "provider_amount_raw TEXT" in text
    assert "provider_key_fingerprint VARCHAR(64)" in text
    assert "execution_segment VARCHAR(64)" in text
    assert "run_request_sequence BIGINT NOT NULL" in text
    assert "MAX(run_request_sequence)" in text
    assert "(run_id, run_request_sequence)" in text
    assert "run_sequence BIGINT NOT NULL" not in text
    assert "trusted_usage BOOLEAN" in text
    assert "partial_text_seen BOOLEAN" in text
    assert "reasoning_tokens BIGINT" in text
    assert "unknown_provider_charge BOOLEAN" in text
    assert "billed_but_not_committed BOOLEAN" in text
    assert "ck_agent_model_attempt_purpose" in text


def test_model_attempt_backfill_skips_existing_attempt_keys():
    text = _read("0016_model_attempts.py")
    assert "existing_sequences AS" in text
    assert "existing_max_sequence + strict_sequence" in text
    assert "'at_' || md5(legacy.run_id || ':' || legacy.request_id) AS attempt_id" in text
    assert "NOT EXISTS (" in text
    assert "existing.id = lined.attempt_id" in text
    assert "existing.run_id = lined.run_id" in text
    assert "existing.run_request_sequence = lined.existing_max_sequence + lined.strict_sequence" in text
    assert "existing.request_id = lined.request_id" in text


def test_tool_result_and_thread_projection_migrations_are_chained():
    tool_results = _read("0017_tool_results.py")
    projection = _read("0018_thread_projection.py")
    assert 'down_revision = "runtime_0016_model_attempts"' in tool_results
    assert "agent_tool_result_blobs" in tool_results
    assert "utf8_bytes BIGINT" in tool_results
    assert "full_available BOOLEAN" in tool_results
    assert "applied_policy JSONB" in tool_results
    assert "fk_agent_tool_result_blob_run" in tool_results
    assert "ON DELETE CASCADE" in tool_results
    assert 'down_revision = "runtime_0017_tool_results"' in projection
    assert "agent_thread_context_ledgers" in projection
    assert "source_cursor BIGINT" in projection
    assert "projected_items JSONB NOT NULL DEFAULT '[]'::jsonb" in projection
    assert "storage_revision BIGINT" in projection
    assert "last_projected_run_id VARCHAR(64)" in projection
    assert "last_canary_candidate_run_id VARCHAR(64)" in projection
    assert "agent_thread_projection_rollout_cohorts" in projection
    assert "shadow_clean_pairs BIGINT" in projection
    assert "canary_clean_runs BIGINT" in projection
    assert "thread_id, model, transport" in projection
    canonical = _read("0019_canonical_prompt_history.py")
    assert 'down_revision = "runtime_0018_thread_projection"' in canonical
    assert "display_history_count BIGINT" in canonical
    assert "display_history_hash VARCHAR(64)" in canonical
    assert "expected_reset_count BIGINT" in canonical
    assert "unexpected_reset_count BIGINT" in canonical
    assert "version = 2" in canonical


def test_runtime_orm_exposes_consumed_contracts():
    from app.core.runtime_db import RUNTIME_SCHEMA_HEAD
    from app.runtime_models import (
        AgentModelAttemptAudit,
        AgentThreadContextLedger,
        AgentThreadProjectionRolloutCohort,
        AgentToolResultBlob,
    )

    # head 常量与迁移文件的一致性由 tests/test_schema_head_sync.py 统一守；这里不钉具体
    # revision（每加一条迁移就红一次），只确认本测试关心的迁移仍在链上、head 不落后于它。
    runtime_versions = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "runtime"
    chain = "\n".join(p.read_text(encoding="utf-8") for p in runtime_versions.glob("*.py"))
    assert 'revision = "runtime_0020_workflow_results"' in chain
    assert f'down_revision = "{RUNTIME_SCHEMA_HEAD}"' not in chain, "head 常量落后于迁移文件"
    assert AgentModelAttemptAudit.__table__.c.run_sequence.name == (
        "run_request_sequence"
    )
    assert AgentToolResultBlob.__tablename__ == "agent_tool_result_blobs"
    assert AgentThreadContextLedger.__tablename__ == "agent_thread_context_ledgers"
    assert AgentToolResultBlob.__table__.c.utf8_bytes is not None
    assert AgentToolResultBlob.__table__.c.chars is not None
    assert AgentThreadContextLedger.__table__.c.projected_items is not None
    assert AgentThreadContextLedger.__table__.c.storage_revision is not None
    assert AgentThreadContextLedger.__table__.c.display_history_hash is not None
    assert AgentThreadContextLedger.__table__.c.expected_reset_count is not None
    assert AgentThreadContextLedger.__table__.c.unexpected_reset_count is not None
    assert AgentThreadProjectionRolloutCohort.__tablename__ == (
        "agent_thread_projection_rollout_cohorts"
    )
