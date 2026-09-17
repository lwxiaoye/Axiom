"""Per-turn tool scoping for the main chat harness.

All capabilities stay registered in code, but the model only receives the office tools that
match the current artifact type and intent.  This keeps deployment simple (one API process,
one existing sandbox fallback) without growing every tool-loop prompt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from app.services.skills.ppt_policy import ppt_intent_in_context


_EMPTY_TEMPLATE_WORDS = (
    "空白文档", "空白模板", "空白文件", "纯空白", "blank document",
    "blank template", "blank file", "空白word", "空白ppt", "空白excel", "空白xlsx",
)


_KIND_BY_SUFFIX = {
    ".xlsx": "workbook",
    ".xlsm": "workbook",
    ".docx": "document",
    ".pptx": "presentation",
    ".pdf": "pdf",
}


def _field(value, name: str):  # noqa: ANN001
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _attachment_names(attachments: Optional[Iterable]) -> list[str]:
    return [
        str(_field(item, "filename") or _field(item, "name") or "").strip()
        for item in (attachments or [])
        if str(_field(item, "filename") or _field(item, "name") or "").strip()
    ]


def _detect_kinds(message: str, attachments: Optional[Iterable], revision_target: Optional[dict]) -> set[str]:
    names = _attachment_names(attachments)
    if revision_target and revision_target.get("filename"):
        names.append(str(revision_target["filename"]))
    kinds = {
        _KIND_BY_SUFFIX[suffix]
        for name in names
        if (suffix := Path(name).suffix.lower()) in _KIND_BY_SUFFIX
    }
    text = str(message or "").lower()
    mentions = {
        "workbook": ("excel", "xlsx", "xlsm", "工作簿", "表格"),
        "document": ("word", "docx", "文档", "报告"),
        "presentation": ("ppt", "pptx", "演示文稿", "幻灯片"),
        "pdf": ("pdf",),
    }
    for kind, words in mentions.items():
        if any(word in text for word in words):
            kinds.add(kind)
    return kinds


@dataclass(frozen=True)
class ToolScope:
    artifact_kinds: frozenset[str] = field(default_factory=frozenset)
    office_tools: frozenset[str] = field(default_factory=frozenset)
    allow_blank_template: bool = False
    requires_ppt_skill: bool = False


def resolve_tool_scope(
    *,
    message: str,
    attachments: Optional[Iterable] = None,
    revision_target: Optional[dict] = None,
    turn_intent: str = "conversation",
    action_authority: str = "mutate",
    revision_mode: bool = False,
    # 最近几条用户消息（不含当轮）：与 turn_prepare 的技能预加载**同一口径**。
    # 只看当轮的话，「我想做个ppt」→ 助手反问 → 「主题是…10页…」这种澄清式对话里，
    # 真正干活那轮判 False，两处口径分叉（turn_prepare 已加载 PPT 技能、工具侧却当没有）。
    recent_user_messages: Optional[Iterable[str]] = None,
) -> ToolScope:
    """Return the smallest office capability set useful for this turn.

    2026-07-29：本函数只再算「这一轮是什么产物类型 / 要不要 PPT 技能 / 允不允许空白模板」。
    它曾经还算过两条「把通用执行器藏起来，把模型推向结构化 Office 工具」的门禁——
    Office 专用工具与旧 file_id 工具族全部退休后推无可推，`bash` 是唯一执行器，
    藏起来就等于该轮一个执行器都没有。
    """
    kinds = _detect_kinds(message, attachments, revision_target)
    text = str(message or "").lower()
    attachment_names = _attachment_names(attachments)
    if revision_target and revision_target.get("filename"):
        attachment_names.append(str(revision_target["filename"]))
    requires_ppt_skill = ppt_intent_in_context(message, attachment_names, recent_user_messages)
    has_existing = bool(revision_target and revision_target.get("file_id")) or bool(_attachment_names(attachments))
    asks_convert = any(word in text for word in ("转成pdf", "转换成pdf", "导出pdf", "另存为pdf", "convert to pdf"))
    asks_verify = any(word in text for word in ("验证", "检查", "能否打开", "校验", "verify"))

    allowed: set[str] = set()
    readonly = action_authority != "mutate"
    if "workbook" in kinds:
        allowed.add("inspect_workbook")
        if not readonly and has_existing:
            allowed.add("apply_excel_patch")
    if "document" in kinds:
        if not readonly:
            allowed.add("edit_docx" if has_existing or revision_mode else "build_docx")
    if "presentation" in kinds:
        if not readonly:
            allowed.add("apply_presentation_patch" if has_existing or revision_mode else "build_presentation")
    if kinds and (asks_verify or turn_intent in {"execute", "revise"}):
        allowed.add("verify_artifact")
    if not readonly and asks_convert and kinds - {"pdf"}:
        allowed.add("convert_to_pdf")

    return ToolScope(
        artifact_kinds=frozenset(kinds),
        office_tools=frozenset(allowed),
        allow_blank_template=any(word in text for word in _EMPTY_TEMPLATE_WORDS),
        requires_ppt_skill=requires_ppt_skill,
    )
