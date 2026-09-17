"""The sole production entry point for a main-chat Harness Run."""

from __future__ import annotations

from typing import Any, AsyncGenerator


class HarnessKernel:
    """Own the worker-to-orchestration boundary.

    The HTTP process only persists commands. The worker always enters here, and this
    entry always invokes the same model/tool loop. Profiles change data and policy;
    they never select another runtime or loop implementation.
    """

    @staticmethod
    async def stream(**run_input: Any) -> AsyncGenerator[str, None]:
        from .orchestrator import harness_orchestrator

        async for frame in harness_orchestrator.stream_chat(**run_input):
            yield frame
