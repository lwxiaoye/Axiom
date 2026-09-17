from __future__ import annotations

import asyncio

from app.services.agent_harness import HarnessKernel
from app.services.agent_harness import orchestrator


def test_worker_kernel_has_one_production_stream(monkeypatch):
    seen = []

    async def _stream_chat(**payload):
        seen.append(payload)
        yield "frame-1"
        yield "frame-2"

    monkeypatch.setattr(orchestrator.harness_orchestrator, "stream_chat", _stream_chat)

    async def _collect():
        return [frame async for frame in HarnessKernel.stream(run_id="run-1")]

    assert asyncio.run(_collect()) == ["frame-1", "frame-2"]
    assert seen == [{"run_id": "run-1"}]


def test_kernel_exposes_no_alternate_loop():
    assert not hasattr(HarnessKernel, "run")
