"""Harness acceptance must persist the resolved model for the worker."""
from pathlib import Path


def _src(rel: str) -> str:
    return Path(__file__).resolve().parents[1].joinpath(rel).read_text(encoding="utf-8")


def test_harness_accept_persists_resolved_model_into_pending():
    src = _src("app/services/agent_harness/orchestrator.py")
    accept = src[src.find("async def accept_harness_run"): src.find("async def start_resume_chat_run")]
    assert "_accept_model" in accept
    assert '"model": _accept_model' in accept
    assert "resolved_model" in accept


def test_worker_falls_back_to_run_model():
    src = _src("app/worker.py")
    assert "want_model" in src
    assert "get_run" in src
    assert '"model": resolved_model' in src


def test_worker_wakes_on_job_notify_with_poll_fallback():
    worker = _src("app/worker.py")
    store = _src("app/services/agent_harness/run_store.py")
    bus = _src("app/services/tasks/runtime_event_bus.py")
    assert "run_job_listener" in worker
    assert "wait_for_job" in worker
    assert "timeout=1.0" in worker
    assert "timeout=0.08" not in worker
    assert "agent_harness_jobs" in store
    assert "pg_notify" in store
    assert "JOB_CHANNEL" in bus
    assert "notify_job_local" in bus


def test_stream_quota_no_nonstream_retry():
    src = _src("app/services/agent_harness/model_driver.py")
    assert "额度/免费配额 403 不再降级非流式白烧第二次" in src
