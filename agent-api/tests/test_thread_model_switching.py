"""v1.95 thread model settings stay separate from immutable Run models."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_thread_schema_keeps_next_and_last_run_models_separate():
    source = _source("app/models.py")
    assert "model = Column(String(255), nullable=True)" in source
    assert "last_run_model = Column(String(255), nullable=True)" in source


def test_acceptance_freezes_run_model_without_queue_snapshot_overwriting_next_setting():
    source = _source("app/services/agent_harness/orchestrator.py")
    assert 'thread_models.get("model")' in source
    assert 'previous_model = str(thread_models.get("last_run_model")' in source
    assert 'update_setting=not queue_dispatch' in source
    assert '"previous_model": previous_model or None' in source


def test_model_switch_note_and_direct_answer_share_preflight_compaction():
    source = _source("app/services/agent_harness/orchestrator.py")
    stream_source = source[source.index("async def stream_chat"):]
    compact_start = stream_source.index("# v1.95：直答也必须")
    compact_end = stream_source.index("# 上下文用量", compact_start)
    compact_block = stream_source[compact_start:compact_end]
    assert "if not direct_answer" not in compact_block
    assert "ensure_compacted" in compact_block
    assert "<model_switch>" in stream_source
    assert "prior_model != resolved_model" in stream_source


def test_run_started_exposes_frozen_model():
    source = _source("app/services/sse_protocol.py")
    assert "model: Optional[str]=None" in source
    assert "data['model'] = model" in source
