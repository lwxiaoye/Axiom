"""Canonical event vocabulary shared by persistence, SSE and UI projection."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import EventEnvelope


@dataclass(frozen=True)
class EventDefinition:
    persisted: bool
    user_visible: bool
    terminal: bool = False
    required_data: tuple[str, ...] = ()


EVENT_CATALOG: dict[str, EventDefinition] = {
    "run.accepted": EventDefinition(True, True, required_data=("route",)),
    "run.started": EventDefinition(True, False),
    "run.phase.changed": EventDefinition(True, False),
    "message.delta": EventDefinition(False, True),
    "message.reasoning.delta": EventDefinition(False, True, required_data=("text",)),
    "message.reasoning.completed": EventDefinition(True, True),
    "message.completed": EventDefinition(True, True, required_data=("text",)),
    "message.commentary": EventDefinition(True, True, required_data=("text",)),
    # Provider-stream fallback is an ephemeral transport fact. It is visible in the current Run
    # timeline and replay buffer, but not part of the assistant answer or durable conversation.
    "model.connection": EventDefinition(False, True, required_data=("status",)),
    "message.user_saved": EventDefinition(True, False),
    "input.received": EventDefinition(True, False, required_data=("input_id",)),
    "input.applied": EventDefinition(True, True),
    "input.rejected": EventDefinition(True, True),
    "input.required": EventDefinition(True, True),
    "approval.required": EventDefinition(True, True),
    "input.accepted": EventDefinition(True, False),
    "plan.updated": EventDefinition(
        True, True, required_data=("goal_revision", "plan_version", "steps"),
    ),
    "plan.confirmation.required": EventDefinition(True, True),
    # call_id is emitted by the main Agent for exact concurrent pairing.  It stays optional in
    # protocol v1 so historical/research synthetic events remain replayable.
    "tool.started": EventDefinition(True, True, required_data=("name",)),
    "tool.progress": EventDefinition(False, True),
    "tool.completed": EventDefinition(True, True, required_data=("name",)),
    "tool.failed": EventDefinition(True, True, required_data=("name", "error")),
    "artifact.saved": EventDefinition(True, True),
    "progress.updated": EventDefinition(False, True),
    "capability.loaded": EventDefinition(True, True),
    "attachments.status": EventDefinition(True, True),
    "context.usage": EventDefinition(False, False),
    "context.compacted": EventDefinition(True, True),
    "context.compaction": EventDefinition(True, True),
    "citations": EventDefinition(True, True),
    "research.progress": EventDefinition(False, True),
    "research.team": EventDefinition(True, True, required_data=("team",)),
    "route.selected": EventDefinition(True, True),
    "clarification.required": EventDefinition(True, True),
    "recommendation": EventDefinition(True, True),
    "recommend_agents": EventDefinition(True, True),
    "memory.updated": EventDefinition(True, False),
    "subagent.preparing": EventDefinition(True, True, required_data=("subagent_id", "name")),
    "subagent.started": EventDefinition(True, True),
    "subagent.node": EventDefinition(True, True),
    "subagent.delta": EventDefinition(False, True),
    "subagent.reasoning": EventDefinition(False, True, required_data=("text",)),
    "subagent.reasoning.completed": EventDefinition(False, True),
    "subagent.review": EventDefinition(True, True),
    "subagent.completed": EventDefinition(True, True),
    "subagent.failed": EventDefinition(True, True),
    "run.completed": EventDefinition(True, True, terminal=True),
    "run.partial": EventDefinition(True, True, terminal=True),
    "run.failed": EventDefinition(True, True, terminal=True),
    "run.cancelled": EventDefinition(True, True, terminal=True),
}


def event_definition(event_type: str) -> EventDefinition:
    try:
        return EVENT_CATALOG[event_type]
    except KeyError as exc:
        raise ValueError(f"unknown Harness event type: {event_type}") from exc


def validate_event(event: EventEnvelope) -> EventEnvelope:
    definition = event_definition(event.type)
    missing = [name for name in definition.required_data if name not in event.data]
    if missing:
        raise ValueError(f"Harness event {event.type} is missing data fields: {missing}")
    return event
