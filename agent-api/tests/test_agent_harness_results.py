from __future__ import annotations

import asyncio

from app.services.agent_harness import (
    ObservationCompactor,
    ObservationStatus,
    ResultSizePolicy,
    ToolObservation,
    ToolSpec,
)


class _Store:
    def __init__(self):
        self.values = {}

    async def put(self, *, run_id, call_id, content):
        handle = f"result:{run_id}:{call_id}"
        self.values[handle] = content
        return handle

    async def get(self, *, run_id, handle, offset, limit):
        assert handle.startswith(f"result:{run_id}:")
        return self.values[handle][offset:offset + limit]


def test_large_structured_result_is_externalized_and_recoverable():
    store = _Store()
    spec = ToolSpec(
        name="lookup",
        description="lookup",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        capability="lookup",
        result_size_policy=ResultSizePolicy(inline_chars=256),
    )
    observation = ToolObservation(
        call_id="c1",
        tool_name="lookup",
        status=ObservationStatus.SUCCEEDED,
        summary="found records",
        structured_data={"rows": ["x" * 100 for _ in range(10)]},
    )
    compacted = asyncio.run(ObservationCompactor().compact(
        observation, spec, run_id="r1", store=store,
    ))
    assert compacted.result_handle == "result:r1:c1"
    assert compacted.structured_data["truncated"] is True
    restored = asyncio.run(store.get(
        run_id="r1", handle=compacted.result_handle, offset=0, limit=10_000,
    ))
    assert "rows" in restored and len(restored) > 1_000
