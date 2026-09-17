"""Structured GoalContract for the main-chat Harness.

TurnDecision remains the coarse router. This contract is the structured goal that
Context Compiler, CompletionVerifier and alignment checkpoints consume. It is
persisted on RunState (no second fact source) and never selects a different loop.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field


_FILE_DELIVERABLE_RE = re.compile(
    r"(保存|存到|写入|交付|导出|输出).{0,12}(文件|我的文件|docx|pptx?|xlsx?|pdf|md)|"
    r"\.(?:md|txt|docx?|pptx?|xlsx?|pdf)\b|"
    r"(做|写|生成|制作|起草).{0,12}(PPT|ppt|演示|报告|文档|周报|方案)",
    re.I,
)
_REVISION_DELIVERABLE_RE = re.compile(
    r"(改|修改|修订|优化|调整|换.{0,6}色|精修).{0,16}(这份|这个|那个|该|原|已有)",
)
_LIGHT_RE = re.compile(r"^(你好|谢谢|在吗)|等于|一句话", re.I)
_HEAVY_RE = re.compile(r"(调研|研究|竞品|对照|PPT|演示文稿|多份|全面)", re.I)


class GoalContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=240)
    deliverable: str = Field(min_length=1, max_length=80)
    success_criteria: list[str] = Field(default_factory=list, max_length=4)
    forbidden: list[str] = Field(default_factory=list, max_length=6)
    budget_hint: str = Field(default="medium", pattern=r"^(light|medium|heavy)$")

    def to_state(self) -> dict[str, Any]:
        return self.model_dump()

    def prompt_block(self) -> str:
        criteria = "\n".join(f"- {item}" for item in self.success_criteria if item)
        forbidden = "\n".join(f"- {item}" for item in self.forbidden if item)
        parts = [
            "【本轮目标契约】",
            f"目标：{self.goal}",
            f"交付物：{self.deliverable}",
            f"规模：{self.budget_hint}",
        ]
        if criteria:
            parts.append("完成条件：\n" + criteria)
        if forbidden:
            parts.append("明确不做：\n" + forbidden)
        parts.append(
            "把本契约当作目标与验收事实。是否调用 update_plan、如何拆分工作以及何时调用工具，"
            "由模型根据当前目标、上下文和工具回执自行决定；完成条件未被证据满足前不要声称完成。"
        )
        return "\n".join(parts)


def seed_goal_contract(
    message: str,
    decision: Any = None,
    *,
    route: str = "",
    prior: Optional[GoalContract] = None,
) -> GoalContract:
    """Deterministic first contract. No extra model call.

    ``decision`` is a TurnDecision-like object (intent/revision/allow_create/plan_mode
    /research_profile). Regex routing stays in charge of tool visibility; this only
    structures the goal so a word-list miss becomes a wrong field, not a withheld tool.

    ``prior`` is the previous Run's contract. Bare resume ("继续") of an unfinished
    file task must inherit deliverable/criteria instead of collapsing to 对话答复.
    """
    text = str(message or "").strip() or "完成当前任务"
    revision = bool(getattr(decision, "revision", False))
    allow_create = bool(getattr(decision, "allow_create", True))
    plan_mode = bool(getattr(decision, "plan_mode", False))
    research = bool(getattr(decision, "research_profile", False))
    route = str(route or "")

    resume_file_prior = None
    if _prior_is_continuable_contract(prior) and _message_is_resume(text):
        if not _RESUME_NEW_CONSTRAINT_RE.search(text):
            return _copy_prior_as_continue(prior)
        resume_file_prior = prior

    if revision and not _FILE_DELIVERABLE_RE.search(text):
        deliverable = "修改既有文件"
    elif _FILE_DELIVERABLE_RE.search(text) or (allow_create and _looks_like_new_file(text)):
        deliverable = "文件"
    elif plan_mode:
        deliverable = "对话答复（计划报告）"
    else:
        deliverable = "对话答复"

    # Outside Research, save-to-files language is a new artifact even if a revision regex
    # flickered. Research exports stay user-triggered from the report card.
    explicit_save = bool(
        re.search(r"(保存|存|导出).{0,16}我的文件", text)
        and not _REVISION_DELIVERABLE_RE.search(text)
    )
    if explicit_save and not research:
        deliverable = "文件"
    # Research 默认交付是对话内报告卡。Calling that a generic "文件" makes
    # the model reach for write_file and creates a duplicate artifact below the report.
    if research:
        deliverable = "研究报告"

    goal = _one_line_goal(text)
    criteria = _success_criteria(deliverable, text, research=research)
    forbidden = _forbidden(text, deliverable=deliverable, revision=revision, allow_create=allow_create)
    if research:
        forbidden.append("不要 write_file 或为研究正文生成/保存 Word、HTML 文件；对话内蓝框报告就是交付")
    if plan_mode:
        forbidden.append("本轮不要写入或修改任何文件")
    budget = "heavy" if (research or plan_mode or _HEAVY_RE.search(text)) else (
        "light" if _LIGHT_RE.search(text) and len(text) < 40 else "medium"
    )
    if route == "direct_answer":
        budget = "light"
        deliverable = "对话答复"
    if resume_file_prior is not None and str(deliverable).startswith("对话答复"):
        deliverable = resume_file_prior.deliverable
        criteria = list(resume_file_prior.success_criteria)
        forbidden = list(resume_file_prior.forbidden)
        budget = resume_file_prior.budget_hint
    return GoalContract(
        goal=goal,
        deliverable=deliverable,
        success_criteria=criteria,
        forbidden=forbidden[:6],
        budget_hint=budget,
    )


_RESUME_NEW_CONSTRAINT_RE = re.compile(
    r"(改成|改配色|换成|加页|加一|不要|另外做|重新做|换题|页数|配色|主题改)",
)

_STEER_THEME_SWITCH_RE = re.compile(
    r"(换题|换主题|主题改|改成做|改做|不要做.{0,16}改|从.{0,20}改成|另外做|重新做一份)",
)

_STEER_CONSTRAINT_RE = re.compile(
    r"(改成|改配色|换成|加页|加一|不要|别|禁止|页数|配色)",
)


def _prior_is_file_contract(prior: Optional[GoalContract]) -> bool:
    if prior is None:
        return False
    deliverable = str(prior.deliverable or "")
    return deliverable.startswith("文件") or deliverable.startswith("修改既有")


def _prior_is_continuable_contract(prior: Optional[GoalContract]) -> bool:
    if prior is None:
        return False
    deliverable = str(prior.deliverable or "")
    return _prior_is_file_contract(prior) or deliverable.startswith("研究")


_UNFINISHED_RESUME_STATUSES = frozenset({
    "cancelled", "partial", "failed", "interrupted",
})


def inherit_agent_mode_for_resume(
    *,
    previous_mode: str = "",
    previous_status: str = "",
    requested_mode: str = "standard",
    message: str = "",
) -> str:
    """Keep Research across stop→继续 even if the prior Run already completed."""
    requested = str(requested_mode or "standard").strip().lower() or "standard"
    previous = str(previous_mode or "").strip().lower()
    if requested == "plan":
        return requested
    if previous == "research":
        unfinished = str(previous_status or "") in _UNFINISHED_RESUME_STATUSES
        if unfinished or requested == "research" or _message_is_resume(message):
            return "research"
    return requested


def _message_is_resume(text: str) -> bool:
    try:
        from app.services.chat.turn_context_builder import needs_resume_checkpoint
        return bool(needs_resume_checkpoint(text))
    except Exception:  # noqa: BLE001
        return False


def _copy_prior_as_continue(prior: GoalContract) -> GoalContract:
    return GoalContract(
        goal=_one_line_goal(f"继续完成：{prior.goal}"),
        deliverable=prior.deliverable,
        success_criteria=list(prior.success_criteria),
        forbidden=list(prior.forbidden),
        budget_hint=prior.budget_hint,
    )


async def load_prior_goal_contract(run_id: str) -> Optional[GoalContract]:
    if not str(run_id or "").strip():
        return None
    try:
        from app.services.agent_harness import run_store
        packed = await run_store.get_run_state(str(run_id).strip())
        state = dict((packed or {}).get("state") or {})
        return parse_goal_contract(state.get("goal_contract"))
    except Exception:  # noqa: BLE001
        return None


async def seed_goal_contract_for_resume(
    message: str,
    decision: Any = None,
    *,
    route: str = "",
    resume_source_run_id: str = "",
) -> GoalContract:
    prior = await load_prior_goal_contract(resume_source_run_id)
    return seed_goal_contract(message, decision, route=route, prior=prior)


def parse_goal_contract(raw: Any) -> Optional[GoalContract]:
    if not isinstance(raw, dict):
        return None
    try:
        return GoalContract.model_validate(raw)
    except Exception:  # noqa: BLE001
        return None


def patch_goal_contract_on_steer(
    message: str,
    prior: Optional[GoalContract],
) -> GoalContract:
    """Patch the live GoalContract in place. Steer is not a new Run.

    File/revision contracts must not collapse to「对话答复」. Short constraints
    merge into the existing goal; an explicit theme switch rewrites the goal
    sentence but keeps the deliverable type so alignment does not yank the
    model back to the old title.
    """
    text = str(message or "").strip()
    if prior is None:
        return seed_goal_contract(text)
    if not text:
        return prior

    fresh = seed_goal_contract(text, prior=prior)
    if _prior_is_file_contract(prior):
        if str(fresh.deliverable).startswith("对话答复"):
            deliverable = prior.deliverable
        elif str(fresh.deliverable).startswith(("文件", "修改既有")):
            deliverable = fresh.deliverable
        else:
            deliverable = prior.deliverable
    elif str(fresh.deliverable).startswith(("文件", "修改既有")):
        deliverable = fresh.deliverable
    else:
        deliverable = prior.deliverable

    if _STEER_THEME_SWITCH_RE.search(text):
        goal = _one_line_goal(text)
    elif _STEER_CONSTRAINT_RE.search(text):
        goal = _one_line_goal(f"{prior.goal}；{text}")
    else:
        goal = _one_line_goal(text)

    forbidden = list(prior.forbidden)
    extra = _forbidden(
        text,
        deliverable=deliverable,
        revision=_prior_is_file_contract(prior),
        allow_create=not _prior_is_file_contract(prior),
    )
    for item in extra:
        if item not in forbidden:
            forbidden.append(item)

    criteria = list(prior.success_criteria)
    if deliverable != prior.deliverable:
        criteria = _success_criteria(
            deliverable,
            text,
            research=str(deliverable).startswith("研究"),
        )

    return GoalContract(
        goal=goal,
        deliverable=deliverable,
        success_criteria=criteria[:4],
        forbidden=forbidden[:6],
        budget_hint=prior.budget_hint,
    )


def steer_plan_revision_notice(
    steps: Iterable[Any],
    *,
    execution_mode: bool = False,
    new_goal: str = "",
) -> str:
    """One-shot plan rewrite hint after a mid-run steer. Not a tool gate."""
    if execution_mode:
        return ""
    rows = [item for item in (steps or []) if isinstance(item, dict)]
    if not rows:
        return ""

    completed: list[str] = []
    in_progress: list[str] = []
    pending: list[str] = []
    for item in rows:
        status = str(item.get("status") or "pending").strip().lower()
        step_id = str(
            item.get("key") or item.get("step_id") or item.get("id") or item.get("step_key") or ""
        ).strip()
        title = str(item.get("title") or item.get("name") or "").strip() or "未命名步骤"
        evidence = str(
            item.get("reason") or item.get("status_reason") or item.get("evidence") or ""
        ).strip()
        label = f"{step_id} {title}".strip() if step_id else title
        if evidence:
            label = f"{label}（{evidence[:80]}）"
        if status in {"completed", "done"}:
            completed.append(label)
        elif status in {"in_progress", "running", "active", "doing", "ongoing"}:
            in_progress.append(label)
        elif status not in {"skipped", "invalidated", "cancelled"}:
            pending.append(label)

    def _join(items: list[str]) -> str:
        return "；".join(items[:8]) if items else "无"

    goal_line = f"新方向：「{_one_line_goal(new_goal)}」。" if str(new_goal or "").strip() else ""
    return (
        "（系统提示：用户刚调整了本轮方向。"
        f"{goal_line}"
        f"权威计划快照：已完成 {_join(completed)}；"
        f"进行中 {_join(in_progress)}；"
        f"待做 {_join(pending)}。"
        "这是上一轮的结构化事实，不是强制计划或工具顺序。模型可按需要调整计划、核对已有证据、"
        "调用工具或直接回答；工具可用性仍只由 ToolSpec、授权和真实资源范围决定。"
        "不要把本提示复述给用户。）"
    )


def plan_appears_off_track(goal: str, steps: Iterable[Any]) -> bool:
    """True when step titles share almost no content grams with the goal.

    Used by the alignment checkpoint. A single news-search title against a
    competitor-research goal, or an install-tutorial plan against a product
    selection goal, is the canonical positive case.
    """
    goal_grams = _content_grams(goal)
    if not goal_grams:
        return False
    titles = []
    for item in steps or []:
        if isinstance(item, dict):
            titles.append(str(item.get("title") or ""))
        else:
            titles.append(str(getattr(item, "title", "") or ""))
    if not titles:
        return False
    title_grams = set()
    for title in titles:
        title_grams |= _content_grams(title)
    overlap = goal_grams & title_grams
    # Alignment is a coarse wrong-task detector, not a lexical similarity
    # score. Artifact plans such as "design / pages / export / publish" are
    # valid even though they do not repeat every adjective in the user goal.
    # Requiring 50% overlap caused the same good PPT plan to be rewritten at
    # every checkpoint. Only zero shared content is strong enough to interrupt.
    return not overlap


def alignment_notice(
    contract: GoalContract,
    *,
    steps: Iterable[Any] = (),
    evidence_summary: str = "",
) -> str:
    drift = plan_appears_off_track(contract.goal, steps)
    criteria = "；".join(contract.success_criteria[:3]) or "对照目标完成交付"
    evidence = (evidence_summary or "尚无新的步骤证据").strip()[:160]
    drift_line = (
        "计划快照与目标文本缺少明显共同词，仅作为可能偏差的观测；模型自行判断是否调整计划。"
        if drift else
        "计划快照与目标存在共同事实；模型自行判断继续工作、核对证据或回答。"
    )
    return (
        "（系统提示：方向校正检查点。对照目标契约："
        f"目标「{contract.goal}」；交付物「{contract.deliverable}」；完成条件：{criteria}。"
        f"当前证据摘要：{evidence}。{drift_line}不要把本提示复述给用户。）"
    )


def _one_line_goal(text: str) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= 120:
        return compact
    return compact[:117] + "…"


def _looks_like_new_file(text: str) -> bool:
    return bool(re.search(
        r"(写|做|生成|制作|起草).{0,12}(一份|一个|篇)?.{0,8}"
        r"(报告|文档|PPT|ppt|演示|方案|周报|清单)",
        text,
    ))


def _success_criteria(deliverable: str, text: str, *, research: bool) -> list[str]:
    if deliverable.startswith("修改既有"):
        return [
            "在原文件上修改，不另起副本",
            "改动覆盖用户点名的内容",
            "修改结果已写入我的文件",
        ]
    if deliverable.startswith("文件"):
        items = [
            "产物已写入我的文件且可下载",
            "内容覆盖用户要求的主题与约束",
        ]
        if research or re.search(r"(调研|引用|来源|链接)", text):
            items.append("关键事实带来源或可核验出处")
        items.append("收尾说明真实保存结果，不把未落盘说成已交付")
        return items[:4]
    if "计划" in deliverable:
        return [
            "计划报告基于实际勘查，不凭空编造",
            "步骤可执行且覆盖用户目标",
            "等待用户确认后再动手",
        ]
    if deliverable.startswith("研究"):
        return [
            "研究计划覆盖用户目标的关键维度",
            "关键事实至少两个独立来源交叉验证",
            "对话正文给出带引用的研究报告",
        ]
    if research:
        return [
            "研究计划覆盖用户目标的关键维度",
            "关键事实至少两个独立来源交叉验证",
            "对话正文给出带引用的研究报告",
        ]
    return [
        "直接回答用户问题",
        "不确定处明确标注",
    ]


def _forbidden(
    text: str,
    *,
    deliverable: str,
    revision: bool,
    allow_create: bool,
) -> list[str]:
    items: list[str] = []
    if deliverable.startswith("修改既有") or (revision and not allow_create):
        items.append("不要另起一份新文件交差")
    if deliverable.startswith("文件") and not revision:
        items.append("不要把新建任务当成修订既有文件")
    if re.search(r"(不要|别|禁止).{0,8}(搜索|上网|联网)", text):
        items.append("不要联网搜索")
    if re.search(r"(不要|别|禁止).{0,8}(写文件|保存|交付文件)", text):
        items.append("不要写入文件")
    return items[:6]


_STOP_GRAMS = frozenset("的了吗呢是在和与或及对把被从到为了这个那一")


def _content_grams(text: str) -> set[str]:
    chars = re.sub(r"[\s\W_]+", "", str(text or "").lower())
    grams: set[str] = set()
    for token in re.findall(r"[a-z0-9]{3,}", chars):
        grams.add(token)
    han = re.findall(r"[\u4e00-\u9fff]+", str(text or ""))
    for run in han:
        if len(run) == 1 and run not in _STOP_GRAMS:
            grams.add(run)
        for i in range(len(run) - 1):
            gram = run[i:i + 2]
            if gram[0] in _STOP_GRAMS or gram[1] in _STOP_GRAMS:
                continue
            grams.add(gram)
    return grams
