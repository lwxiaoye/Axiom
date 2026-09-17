"""initial_progress 不得投影为 durable execution_trace.preamble。"""
from __future__ import annotations

import inspect

from app.services.tasks import task_run_service


def test_get_execution_traces_skips_initial_progress_kind():
    src = inspect.getsource(task_run_service.get_execution_traces_by_thread)
    assert 'initial_progress' in src
    assert 'if str(data.get("kind") or "") == "initial_progress"' in src
    assert 'continue' in src
