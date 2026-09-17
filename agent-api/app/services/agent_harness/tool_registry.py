"""Production adapter from main-chat tool bodies to the canonical ToolSpec policy."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from .contracts import ToolRequest
from .dispatcher import ToolDispatcher, ToolRegistry
from .run_store import get_run_snapshot

if TYPE_CHECKING:
    from app.services.chat.tools.base import MainTool


_PRODUCTION_DISPATCHER = ToolDispatcher(ToolRegistry())


async def visible_main_tools(
    run_id: str,
    tools: Iterable["MainTool"],
    *,
    export_authorized: bool = False,
) -> list["MainTool"]:
    """Build the model-visible registry exclusively from ToolSpec metadata."""
    concrete_tools = list(tools)
    snapshot = await get_run_snapshot(run_id)
    if snapshot is None:
        return []
    visible = [
        tool for tool in concrete_tools
        if _PRODUCTION_DISPATCHER.authorize(
            tool.spec, snapshot, export_authorized=export_authorized,
        ).allowed
    ]
    # Last authorized instance wins if two builders briefly share a name.
    by_name: dict[str, "MainTool"] = {}
    for tool in visible:
        by_name[tool.name] = tool
    return list(by_name.values())


def authorize_main_tool(spec, run, *, export_authorized: bool = False):
    """Authorize a concrete tool at the last moment through the canonical Dispatcher."""
    return _PRODUCTION_DISPATCHER.authorize(
        spec,
        run,
        export_authorized=export_authorized,
    )


def production_tool_idempotency_key(
    run,
    *,
    call_id: str,
    name: str,
    arguments: dict | None = None,
) -> str:
    """Production idempotency key: run + goal_revision + plan_version + call."""
    return ToolDispatcher.idempotency_key(
        run,
        ToolRequest(
            call_id=str(call_id or "missing"),
            name=str(name or "tool"),
            arguments=dict(arguments or {}),
        ),
    )


def assert_tool_specs(tools: Iterable["MainTool"]) -> None:
    names: set[str] = set()
    for tool in tools:
        if tool.name in names:
            raise ValueError(f"duplicate Harness tool: {tool.name}")
        names.add(tool.name)
        if tool.spec.name != tool.name:
            raise ValueError(f"ToolSpec name mismatch: {tool.name}")
