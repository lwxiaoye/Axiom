import pytest

from app.services.agent_harness.public_errors import GENERIC_RUN_FAILURE, ResearchSourcesUnavailable
from app.services.chat.history_trace_projection import decode_execution_trace_projection, encode_execution_trace_projection
from app.services.tasks import task_run_service as service
from tests.test_execution_trace_artifact_rows import _FakeSession, _event, _run_row


@pytest.mark.asyncio
@pytest.mark.parametrize("error,expected", [
    (ResearchSourcesUnavailable.public_message, ResearchSourcesUnavailable.public_message),
    ("ReadTimeout: private provider credential detail", GENERIC_RUN_FAILURE),
])
async def test_failure_reason_survives_history_and_terminal_projection(monkeypatch, error, expected):
    run = _run_row()
    run.status, run.error = "failed", error
    run.state = {"phase": "failed", "terminal_reason": error}
    events = [_event(1, "run.failed", {"message": error}),
              _event(2, "message.completed", {"message_id": 42, "text": "（任务执行失败，未生成回复）"})]
    monkeypatch.setattr(service, "runtime_session", lambda: lambda: _FakeSession([[run], events, []]))
    traces = await service.get_execution_traces_by_thread("t1")
    saved = decode_execution_trace_projection(encode_execution_trace_projection(traces[42]))
    assert saved["status"] == "failed"
    assert saved["error"] == expected
    assert "private provider" not in str(saved)
