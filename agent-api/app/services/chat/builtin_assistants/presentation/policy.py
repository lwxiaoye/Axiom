"""Presentation Assistant execution strategy.

Identity lives in definition.py.  This module owns ppt-studio selection, the
presentation tool denylist and the turn guard.  It does not select a second
execution loop.
"""

from .definition import (
    PRESENTATION_PRESET,
    PRESENTATION_THREAD_ORIGIN,
    is_presentation_preset,
)

PRESENTATION_SKILL_CANONICAL_ID = "ppt-studio"
PRESENTATION_FORBIDDEN_TOOL_NAMES = frozenset({
    "use_skill",
    "search_capabilities",
})

# ppt-studio SKILL.md 点名的工具面。首轮与续接都只开放这些：
# 不做能力发现或其他 Skill。
# glob 是读工程文件的配套能力；ask_user_choice 承接计划确认。
# download_url / browser / search_knowledge 不在 Skill 流程里，故不开放。
PRESENTATION_PINNED_TOOL_NAMES = frozenset({
    "read_file",
    "glob",
    "write_file",
    "edit_file",
    "bash",
    "update_plan",
    "search_web",
    "fetch_ppt_asset",
    "publish_ppt_artifact",
    "ask_user_choice",
})


def preset_from_thread_origin(origin: object) -> str:
    return PRESENTATION_PRESET if str(origin or "").strip() == PRESENTATION_THREAD_ORIGIN else ""


def resolve_ppt_studio_skill_id(records: list[dict]) -> str:
    """Resolve only ppt-studio from the authoritative enabled catalog.

    Another PPT-like Skill is not a valid fallback for this product preset.
    """
    for record in records or []:
        if not isinstance(record, dict) or record.get("enabled") not in (1, True, "1"):
            continue
        skill_id = str(record.get("skillId") or record.get("id") or "").strip()
        name = str(record.get("name") or "").strip()
        normalized_id = "".join(ch.lower() for ch in skill_id if ch.isalnum())
        normalized_name = "".join(ch.lower() for ch in name if ch.isalnum())
        if skill_id and "pptstudio" in {normalized_id, normalized_name}:
            return skill_id
    return ""


def without_presentation_forbidden_tools(tools: list) -> list:
    """Apply the presentation preset at the real Tool Registry boundary."""
    return [
        tool for tool in tools
        if str(getattr(tool, "name", "")) not in PRESENTATION_FORBIDDEN_TOOL_NAMES
    ]


def presentation_allowed_tools(tools: list) -> list:
    """Keep only the ppt-studio Skill tool surface."""
    return [
        tool for tool in tools
        if str(getattr(tool, "name", "")) in PRESENTATION_PINNED_TOOL_NAMES
    ]


def presentation_turn_guard() -> str:
    return (
        "【演示文稿助手固定约束】\n"
        "本会话始终使用主对话 AXIOM Agent Harness；ppt-studio 已由平台完成权威校验并自动加载。\n"
        "只处理演示文稿的构思、制作、修改、验证和交付。不要推荐或调用子智能体，"
        "不要发现、选择或调用其他 Skill，也不要要求用户手动选择 ppt-studio。\n"
        "search_web 和 fetch_ppt_asset 是本助手的默认能力：需要事实、人物、场馆或真实照片时，"
        "直接调用工具，不要把「要不要搜、搜什么、fetch_ppt_asset 能不能用」写成对用户可见的正文。\n"
        "依据 ppt-studio 说明直接推进制作、修改、验证和交付；不要切换到计划模式或深度研究。"
    )


def presentation_search_hint() -> str:
    return (
        "本助手默认具备联网搜索。需要事实核对或真实照片时直接调用 search_web；"
        "搜图查询须带「照片/图片」字样。search_web 返回的 [图N] 只是候选目录，"
        "再用 fetch_ppt_asset(url=\"图N\") 注入工程 media/ 并在页面引用。"
        "不要把工具选择过程写成对用户可见的正文。"
    )


__all__ = [
    "PRESENTATION_FORBIDDEN_TOOL_NAMES",
    "PRESENTATION_PINNED_TOOL_NAMES",
    "PRESENTATION_PRESET",
    "PRESENTATION_SKILL_CANONICAL_ID",
    "PRESENTATION_THREAD_ORIGIN",
    "is_presentation_preset",
    "presentation_allowed_tools",
    "presentation_search_hint",
    "presentation_turn_guard",
    "preset_from_thread_origin",
    "resolve_ppt_studio_skill_id",
    "without_presentation_forbidden_tools",
]
