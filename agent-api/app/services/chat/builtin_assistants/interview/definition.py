"""Stable interview identity and catalog defaults without runtime imports."""

from app.services.chat.builtin_assistants.types import BuiltinAppSpec, BuiltinAssistantDefinition

INTERVIEW_PRESET = "interview"
INTERVIEW_APP_ID = "builtin:interview"
INTERVIEW_THREAD_ORIGIN = "interview"
INTERVIEW_IDENTITY = {
    "app_id": INTERVIEW_APP_ID, "preset": INTERVIEW_PRESET, "name": "面试助手",
    "origin": INTERVIEW_THREAD_ORIGIN, "ui_policy_key": "interview_practice",
}
INTERVIEW_DEFINITION = BuiltinAssistantDefinition(
    identity=INTERVIEW_IDENTITY,
    catalog=BuiltinAppSpec(
        preset=INTERVIEW_PRESET, name="面试助手",
        description="结合简历与岗位 JD，进行文字模拟面试、逐题反馈与复盘",
        icon="", category="communication", category_label="沟通交互",
        route="/center/chat/interview", order_num=30,
    ),
)


def is_interview_preset(value: object) -> bool:
    return str(value or "").strip().lower() == INTERVIEW_PRESET
