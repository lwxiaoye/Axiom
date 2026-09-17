"""Bounded extension points for built-ins, not another execution loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.services.chat.types import TurnContext


@dataclass(frozen=True)
class BuiltinTurnInput:
    message: str
    token: str
    user_id: str
    thread_id: str
    run_id: str
    root_run_id: str
    budget: float


@dataclass(frozen=True)
class BuiltinTurnServices:
    get_catalog_records: Callable[[str], Awaitable[list]]
    fetch_trusted_skills: Callable[[list[str], str], Awaitable[list]]
    recall_memory: Callable[..., Awaitable[Any]]
    format_memory: Callable[[Any], str]
    personalization: Callable[[str], Awaitable[str]]
    lesson_block: Callable[[str, str, str], Awaitable[str]]
    selected_skill_metadata: Callable[[list, list], list[dict]]


@dataclass(frozen=True)
class BuiltinRuntimePolicy:
    preset: str
    prepare_request: Callable[[dict], Awaitable[None]]
    prepare_turn: Callable[[BuiltinTurnInput, BuiltinTurnServices], Awaitable[TurnContext]]
    turn_guard: Callable[[], str]
    filter_tools: Callable[[list], list]
    validate_tools: Callable[[list], list]
    pinned_tool_names: frozenset[str]
    build_allowed_tool_names: frozenset[str] | None = None
    image_delivery_mode: str | None = None
    official_domain_scope: bool = False
    allow_choice_tool: bool = True
    allow_plain_fallback: bool = True
    forced_resume_mode: str | None = None
    hide_selected_skill_references: bool = False
    validate_resume_skills: Callable[[list], None] | None = None
    search_hint: Callable[[], str] | None = None
    knowledge_prompt: Callable[[str, str], str] | None = None
    knowledge_source_label: str = ""
    accept_input: Callable[[dict], Awaitable[dict]] | None = None
    additional_tools: Callable[[Any], list] | None = None
    action_authority: str | None = None
    project_answer: Callable[[Any, str], Awaitable[str]] | None = None
    allow_memory_extraction: bool = True
    public_preamble_guidance: Callable[[dict], str] | None = None

    def bound_tools(self, tools: list) -> list:
        """Filter full assembly, then enforce the same boundary used on resume."""
        return self.validate_tools(self.filter_tools(tools))
