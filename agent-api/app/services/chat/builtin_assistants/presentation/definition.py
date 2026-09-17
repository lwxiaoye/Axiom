"""Presentation product identity and catalog defaults; no execution dependencies."""

from app.services.chat.builtin_assistants.types import (
    BuiltinAppSpec,
    BuiltinAssistantDefinition,
    BuiltinAssistantIdentity,
)

PRESENTATION_PRESET = "presentation"
PRESENTATION_APP_ID = "builtin:presentation"
PRESENTATION_THREAD_ORIGIN = "presentation"
PRESENTATION_IDENTITY: BuiltinAssistantIdentity = {
    "app_id": PRESENTATION_APP_ID,
    "preset": PRESENTATION_PRESET,
    "name": "演示文稿助手",
    "origin": PRESENTATION_THREAD_ORIGIN,
    "ui_policy_key": "presentation_authoring",
}
PRESENTATION_DEFINITION = BuiltinAssistantDefinition(
    identity=PRESENTATION_IDENTITY,
    catalog=BuiltinAppSpec(
        preset=PRESENTATION_PRESET,
        name=PRESENTATION_IDENTITY["name"],
        description="制作、优化并交付可编辑演示文稿",
        icon="/agent-icons/builtin/presentation-assistant.png",
        category="content_creation",
        category_label="内容创作",
        route="/center/chat/ppt",
        order_num=20,
    ),
)


def is_presentation_preset(value: object) -> bool:
    return str(value or "").strip().lower() == PRESENTATION_PRESET
