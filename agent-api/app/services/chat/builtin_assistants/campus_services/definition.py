"""Campus product identity and catalog defaults; no execution dependencies."""

from app.services.chat.builtin_assistants.types import (
    BuiltinAppSpec,
    BuiltinAssistantDefinition,
    BuiltinAssistantIdentity,
)

CAMPUS_PRESET = "campus_services"
CAMPUS_APP_ID = "builtin:campus-services"
CAMPUS_THREAD_ORIGIN = "campus_services"
CAMPUS_IDENTITY: BuiltinAssistantIdentity = {
    "app_id": CAMPUS_APP_ID,
    "preset": CAMPUS_PRESET,
    "name": "校园百事通",
    "origin": CAMPUS_THREAD_ORIGIN,
    "ui_policy_key": "campus_readonly",
}
CAMPUS_DEFINITION = BuiltinAssistantDefinition(
    identity=CAMPUS_IDENTITY,
    catalog=BuiltinAppSpec(
        preset=CAMPUS_PRESET,
        name=CAMPUS_IDENTITY["name"],
        description="校园政策、办事流程与常见问题官方问答",
        icon="/agent-icons/builtin/campus-services.png",
        category="document_knowledge",
        category_label="文档与知识",
        route="/center/chat/campus",
        order_num=10,
    ),
)


def is_campus_preset(value: object) -> bool:
    return str(value or "").strip().lower() == CAMPUS_PRESET
