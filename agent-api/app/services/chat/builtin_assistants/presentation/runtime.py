"""Presentation-specific preparation and policy composed into the shared Harness."""

from fastapi import HTTPException

from app.services.agent_harness.public_errors import ConfigurationRunError
from app.services.chat.builtin_assistants.runtime_types import BuiltinRuntimePolicy
from .definition import PRESENTATION_PRESET
from .policy import (
    PRESENTATION_PINNED_TOOL_NAMES,
    PRESENTATION_SKILL_CANONICAL_ID,
    presentation_allowed_tools,
    presentation_search_hint,
    presentation_turn_guard,
)
from .prepare import prepare_turn


async def prepare_request(kwargs: dict) -> None:
    if kwargs.get("subagent_id") or kwargs.get("skill_ids") or kwargs.get("selected_skills"):
        raise HTTPException(
            status_code=422,
            detail="演示文稿助手固定使用 ppt-studio，不能引用子智能体或其他 Skill。",
        )
    # Only intent is pinned here; every Worker turn validates the live Skill catalog/ACL.
    kwargs["assistant_preset"] = PRESENTATION_PRESET
    kwargs["subagent_id"] = None
    kwargs["skill_ids"] = [PRESENTATION_SKILL_CANONICAL_ID]
    kwargs["selected_skills"] = [{
        "id": PRESENTATION_SKILL_CANONICAL_ID,
        "name": PRESENTATION_SKILL_CANONICAL_ID,
        "source": "system",
    }]


def validate_resume_skills(skills: list) -> None:
    # 续接时 ppt-studio 已从目录消失/说明为空：与首轮同款的配置性错误，直接失败并告知原因，
    # 不能让续接轮在 waiting_system 里空转。
    if not skills or not any(str(item.get("instructions") or "").strip() for item in skills):
        raise ConfigurationRunError(
            "演示文稿助手暂不可用：续接时 ppt-studio 已不在技能目录或说明为空，本轮已结束。"
            "请管理员确认内置技能已注册后重新发起。"
        )


PRESENTATION_RUNTIME_POLICY = BuiltinRuntimePolicy(
    preset=PRESENTATION_PRESET,
    prepare_request=prepare_request,
    prepare_turn=prepare_turn,
    turn_guard=presentation_turn_guard,
    filter_tools=presentation_allowed_tools,
    validate_tools=presentation_allowed_tools,
    pinned_tool_names=PRESENTATION_PINNED_TOOL_NAMES,
    forced_resume_mode="standard",
    hide_selected_skill_references=True,
    validate_resume_skills=validate_resume_skills,
    search_hint=presentation_search_hint,
)
