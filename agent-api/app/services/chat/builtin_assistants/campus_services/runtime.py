"""Campus-specific preparation and policy composed into the shared Harness."""

from fastapi import HTTPException

from app.services.chat.builtin_assistants.runtime_types import (
    BuiltinRuntimePolicy,
    BuiltinTurnInput,
    BuiltinTurnServices,
)
from app.services.chat.types import TurnContext
from .definition import CAMPUS_PRESET
from .policy import (
    CAMPUS_ALLOWED_TOOL_NAMES,
    campus_knowledge_prompt,
    campus_turn_guard,
    enforce_campus_tool_boundary,
    reject_campus_request_overrides,
    retain_campus_allowed_tools,
)


async def prepare_request(kwargs: dict) -> None:
    from .config_service import CampusConfigError
    from .runtime_service import resolve_published_snapshot

    kwargs["assistant_preset"] = CAMPUS_PRESET
    reject_campus_request_overrides(kwargs)
    tenant_id = str(getattr(kwargs.get("user_context"), "tenant_id", None) or "0")
    try:
        snapshot = await resolve_published_snapshot(tenant_id)
    except CampusConfigError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    kwargs["assistant_preset_snapshot"] = snapshot
    kwargs["knowledge_ids"] = list(snapshot.get("knowledge_ids") or [])
    kwargs["selected_knowledge"] = []
    kwargs["skill_ids"] = []
    kwargs["selected_skills"] = []
    kwargs["subagent_id"] = None
    # Attachments remain validated shared uploads; only references are restricted here.
    kwargs["file_ids"] = []
    kwargs["thread_ids"] = []
    kwargs["agent_mode"] = "standard"
    if snapshot.get("model_id"):
        kwargs["model"] = snapshot.get("model_id")


async def prepare_turn(
    _request: BuiltinTurnInput,
    _services: BuiltinTurnServices,
) -> TurnContext:
    return TurnContext(
        effective_subagent_id=None,
        route_info=None,
        clarify_options=[],
        agents=None,
        trusted_skills=[],
        selected_skill_records=[],
        effective_skill_ids=[],
        memory_block="",
        skill_catalog_block="",
        recommend_agent_ids=[],
        recommend_external=None,
    )


CAMPUS_RUNTIME_POLICY = BuiltinRuntimePolicy(
    preset=CAMPUS_PRESET,
    prepare_request=prepare_request,
    prepare_turn=prepare_turn,
    turn_guard=campus_turn_guard,
    filter_tools=retain_campus_allowed_tools,
    validate_tools=enforce_campus_tool_boundary,
    pinned_tool_names=CAMPUS_ALLOWED_TOOL_NAMES,
    build_allowed_tool_names=CAMPUS_ALLOWED_TOOL_NAMES,
    image_delivery_mode="chat_inline",
    official_domain_scope=True,
    allow_choice_tool=False,
    allow_plain_fallback=False,
    knowledge_prompt=campus_knowledge_prompt,
    knowledge_source_label="已审核知识库",
)
