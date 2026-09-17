"""Bind a tool call to exactly one Plan node. Not a DAG scheduler."""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from .contracts import PlanStepSnapshot, PlanStepStatus, ToolSpec


_ARTIFACT_DELIVERY_ACTION_RE = re.compile(
    r"(artifact\.saved|持久化|落库|我的文件|可下载|"
    r"(?:发布|交付|保存|写入).{0,16}(?:文件|产物|成品|pptx?|docx|xlsx|pdf|zip)|"
    r"(?:publish|deliver|persist|save).{0,24}(?:file|artifact|output|pptx?|docx|xlsx|pdf|zip))",
    re.I,
)
_INVESTIGATION_STEP_RE = re.compile(r"(调研|检索|收集|竞品|对照|查资料|搜索|分析现状)")
_ASSET_COLLECTION_STEP_RE = re.compile(
    r"(?:收集|下载|获取|搜集).{0,24}(?:照片|图片|素材|配图)|"
    r"(?:collect|download|gather).{0,32}(?:photos?|images?|assets?)",
    re.I,
)
_ARTIFACT_EXPORT_STEP_RE = re.compile(r"导出|渲染|\bexport\b|\brender\b", re.I)
_ARTIFACT_VALIDATE_STEP_RE = re.compile(r"校验|验证|检查|核验|验收|\bverify\b|\bvalidate\b|\bcheck\b", re.I)
_ARTIFACT_SOURCE_STEP_RE = re.compile(
    r"(?:写入|编写|制作|生成|填充|完善).{0,24}(?:页面|内容|源稿|演示文稿)|"
    r"(?:逐页|按页).{0,24}(?:内容|编写|写入)|嵌入.{0,8}(?:照片|图片)|"
    r"(?:write|author|create).{0,24}(?:slides?|pages?)",
    re.I,
)
_ARTIFACT_DESIGN_STEP_RE = re.compile(r"设计|版式|布局|页面结构|结构骨架|排版|\bdesign\b|\blayout\b", re.I)


_CONVERSATIONAL_STEP_RE = re.compile(
    r"^(理解需求|对话答复|澄清需求|确认需求|分析需求)$",
)
_ALLOWED_REQUIRE_TAGS = frozenset({
    "investigate",
    "productive",
    "download",
    "mutate",
    "revision_mutation",
    "artifact_producer",
    "delegation",
})
_DONE = frozenset({
    PlanStepStatus.COMPLETED,
    PlanStepStatus.SKIPPED,
    PlanStepStatus.INVALIDATED,
})
UNBOUND_MESSAGE = (
    "当前任务计划没有唯一进行中或唯一可开始的步骤。"
    "请先调用 update_plan，把正在做的那一步标为 in_progress。"
)
TYPE_MISMATCH_MESSAGE = (
    "当前步骤与这个工具类型不匹配。"
    "若要写文件：先 update_plan 把『生成/保存文件』那一步标为 in_progress"
    "（不要标 completed），然后用 bash 把最终文件写到 /workspace/files/。"
    "docx/pptx/xlsx 不要用 write_file。"
)


class PlanBindingError(Exception):
    """Fail-closed plan identity bind; do not execute the side-effecting tool."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.user_message = message
        super().__init__(message)


def is_conversational_empty_step(
    title: str,
    *,
    acceptance: Sequence[str] | None = None,
    requires: Sequence[str] | None = None,
) -> bool:
    """True when the row is a non-executable preamble with no typed work."""
    if any(str(item).strip() for item in (requires or ())):
        return False
    if any(str(item).strip() for item in (acceptance or ())):
        return False
    return bool(_CONVERSATIONAL_STEP_RE.match(str(title or "").strip()))


def infer_requires(title: str, detail: str = "", acceptance: Sequence[str] | None = None) -> tuple[str, ...]:
    """Default capability tags from user-facing step text when the model omitted requires."""
    text = "\n".join([
        str(title or ""),
        str(detail or ""),
        *[str(item) for item in (acceptance or ())],
    ])
    if _ARTIFACT_DELIVERY_ACTION_RE.search(text):
        return ("productive",)
    if _ASSET_COLLECTION_STEP_RE.search(text):
        # Search discovers candidates; the same step must also permit staging them.
        return ("investigate", "download")
    if _INVESTIGATION_STEP_RE.search(text):
        return ("investigate",)
    return ()


def infer_completion_stage(step: PlanStepSnapshot) -> str:
    """A conservative evidence class for existing free-text plans, not scheduling."""
    text = "\n".join([step.title, step.detail, *step.acceptance_criteria])
    if _ARTIFACT_DELIVERY_ACTION_RE.search(text):
        return "publish"
    if _ARTIFACT_EXPORT_STEP_RE.search(text) and _ARTIFACT_VALIDATE_STEP_RE.search(text):
        return "validation"
    for stage, pattern in (
        ("export", _ARTIFACT_EXPORT_STEP_RE),
        ("source", _ARTIFACT_SOURCE_STEP_RE),
        ("design", _ARTIFACT_DESIGN_STEP_RE),
        ("assets", _ASSET_COLLECTION_STEP_RE),
    ):
        if pattern.search(text):
            return stage
    return ""


def normalize_requires(raw: Any, *, inferred: tuple[str, ...] = ()) -> tuple[str, ...]:
    tags: list[str] = []
    if isinstance(raw, (list, tuple)):
        source = raw
    elif isinstance(raw, str) and raw.strip():
        source = [part.strip() for part in raw.split(",")]
    else:
        source = inferred
    for item in source:
        tag = str(item or "").strip()
        if tag in _ALLOWED_REQUIRE_TAGS and tag not in tags:
            tags.append(tag)
    return tuple(tags) or inferred


def normalize_depends_on(raw: Any, *, known_ids: Iterable[str] = ()) -> tuple[str, ...]:
    allowed = {str(item) for item in known_ids if str(item).strip()}
    values: list[str] = []
    if isinstance(raw, (list, tuple)):
        source = raw
    elif isinstance(raw, str) and raw.strip():
        source = [part.strip() for part in raw.split(",")]
    else:
        source = ()
    for item in source:
        step_id = str(item or "").strip()[:128]
        if not step_id or step_id in values:
            continue
        if allowed and step_id not in allowed:
            continue
        values.append(step_id)
    return tuple(values)


def step_is_ready(step: PlanStepSnapshot, steps: Sequence[PlanStepSnapshot]) -> bool:
    """Pending and unblocked. Linear plans: only the first open step is ready."""
    if step.status is not PlanStepStatus.PENDING:
        return False
    by_id = {item.step_id: item for item in steps}
    if step.depends_on:
        return all(
            (dep := by_id.get(dep_id)) is None or dep.status in _DONE
            for dep_id in step.depends_on
        )
    return not any(
        other.order < step.order and other.status in {
            PlanStepStatus.PENDING,
            PlanStepStatus.IN_PROGRESS,
        }
        for other in steps
    )


def enforce_linear_cursor(
    steps: Sequence[PlanStepSnapshot],
) -> tuple[PlanStepSnapshot, ...]:
    """Keep the unique in_progress cursor on the earliest unfinished linear step.

    Codex ``update_plan`` is a checklist, not a scheduler: array order is execution
    order, and there is at most one in_progress item. Models sometimes mark a later
    step active while an earlier one is still pending. That is allowed only when the
    later step is independently ready via ``depends_on``. Otherwise pull the cursor
    back so work proceeds from start to finish. This is not a DAG scheduler.
    """
    rows = list(steps)
    active = next((step for step in rows if step.status is PlanStepStatus.IN_PROGRESS), None)
    if active is None:
        return tuple(rows)
    probe = [
        step.model_copy(update={"status": PlanStepStatus.PENDING})
        if step.step_id == active.step_id
        else step
        for step in rows
    ]
    probe_active = next(step for step in probe if step.step_id == active.step_id)
    if step_is_ready(probe_active, probe):
        return tuple(rows)
    first_open = next(
        (
            step for step in rows
            if step.status in {PlanStepStatus.PENDING, PlanStepStatus.IN_PROGRESS}
        ),
        None,
    )
    if first_open is None or first_open.step_id == active.step_id:
        return tuple(rows)
    out: list[PlanStepSnapshot] = []
    for step in rows:
        if step.step_id == first_open.step_id:
            out.append(step.model_copy(update={"status": PlanStepStatus.IN_PROGRESS}))
        elif step.step_id == active.step_id:
            out.append(step.model_copy(update={"status": PlanStepStatus.PENDING}))
        else:
            out.append(step)
    return tuple(out)


def step_is_blocked(step: PlanStepSnapshot, steps: Sequence[PlanStepSnapshot]) -> bool:
    return step.status is PlanStepStatus.PENDING and not step_is_ready(step, steps)


def ready_steps(steps: Sequence[PlanStepSnapshot]) -> tuple[PlanStepSnapshot, ...]:
    return tuple(step for step in steps if step_is_ready(step, steps))


def snapshots_from_projection(rows: Sequence[Any]) -> tuple[PlanStepSnapshot, ...]:
    """Rebuild canonical steps from the compact LoopState / SSE projection."""
    out: list[PlanStepSnapshot] = []
    for index, raw in enumerate(rows or ()):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        status_raw = str(raw.get("status") or "pending").strip().lower()
        status = {
            "running": PlanStepStatus.IN_PROGRESS,
            "in_progress": PlanStepStatus.IN_PROGRESS,
            "completed": PlanStepStatus.COMPLETED,
            "skipped": PlanStepStatus.SKIPPED,
            "invalidated": PlanStepStatus.INVALIDATED,
        }.get(status_raw, PlanStepStatus.PENDING)
        acceptance = raw.get("acceptance_criteria")
        if not isinstance(acceptance, list):
            acc = str(raw.get("acceptance") or "").strip()
            acceptance = [acc] if acc else []
        out.append(PlanStepSnapshot(
            step_id=str(raw.get("key") or raw.get("step_id") or f"step-{index + 1}")[:128],
            order=index,
            title=title[:200],
            detail=str(raw.get("detail") or "")[:2000],
            status=status,
            required=bool(raw.get("required", True)),
            acceptance_criteria=[str(item) for item in acceptance if str(item).strip()],
            reason=str(raw.get("reason") or ""),
            depends_on=normalize_depends_on(raw.get("depends_on")),
            requires=normalize_requires(raw.get("requires")),
        ))
    return tuple(out)


def resolve_plan_step_id(
    steps: Sequence[PlanStepSnapshot],
    spec: ToolSpec,
) -> str:
    """Return the only legal node for this tool, or raise PlanBindingError."""
    if not steps:
        raise PlanBindingError("plan_step_unbound", UNBOUND_MESSAGE)
    in_progress = [step for step in steps if step.status is PlanStepStatus.IN_PROGRESS]
    if len(in_progress) > 1:
        raise PlanBindingError("plan_step_unbound", UNBOUND_MESSAGE)
    target = in_progress[0] if in_progress else None
    if target is None:
        ready = ready_steps(steps)
        if len(ready) != 1:
            raise PlanBindingError("plan_step_unbound", UNBOUND_MESSAGE)
        target = ready[0]
    required = set(target.requires or ())
    if required and required.isdisjoint(spec.semantic_tags):
        raise PlanBindingError("plan_step_type_mismatch", TYPE_MISMATCH_MESSAGE)
    return target.step_id


def _is_artifact_delivery_step(step: PlanStepSnapshot) -> bool:
    text = "\n".join([
        step.title,
        step.detail,
        *[str(item) for item in step.acceptance_criteria],
    ])
    return bool(_ARTIFACT_DELIVERY_ACTION_RE.search(text))


def bind_prepared_tool_calls(
    prepared_calls: Sequence[tuple],
    tool_map: dict,
    plan_rows: Sequence[Any],
) -> tuple[list[tuple], dict[int, str]]:
    """Attach identity binds; unbound non-control tools become arg_error (not executed)."""
    steps = snapshots_from_projection(plan_rows)
    bound: dict[int, str] = {}
    next_calls: list[tuple] = []
    for item in prepared_calls:
        pos, call, name, args, arg_error = item[:5]
        extra = item[5:]
        tool = tool_map.get(str(name or "")) if tool_map else None
        control = bool(tool and getattr(getattr(tool, "spec", None), "control_command", False))
        delivery = str(name or "") == "publish_ppt_artifact"
        if arg_error or control or not steps or tool is None:
            next_calls.append(item)
            continue
        if delivery:
            # Publishing must never be blocked by a stale/mismatched plan cursor, but a
            # uniquely active delivery row still needs the call-time identity so its durable
            # file receipt can complete the authoritative checklist automatically.
            try:
                step_id = resolve_plan_step_id(steps, tool.spec)
                target = next(step for step in steps if step.step_id == step_id)
                if _is_artifact_delivery_step(target):
                    bound[int(pos)] = step_id
            except (PlanBindingError, StopIteration):
                pass
            next_calls.append(item)
            continue
        if "update_plan" not in (tool_map or {}):
            # 工具面没有 update_plan 时，拦截只会逼模型去调一个不存在的工具。
            next_calls.append(item)
            continue
        try:
            step_id = resolve_plan_step_id(steps, tool.spec)
        except PlanBindingError as exc:
            next_calls.append((pos, call, name, args, exc.user_message, *extra))
            continue
        bound[int(pos)] = step_id
        next_calls.append(item)
    return next_calls, bound
