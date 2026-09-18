"""主对话对外 facade(结构手术 2026-07-17 定稿)。

本文件只做三件事:①对外 API 签名(router/E2E 依赖,不可破);②入口准备
(Key/模型解析、Thread 保障);③stream_chat 编排骨架——守卫→建 Run→附件→
回合准备→(消歧|委派|工具循环|直答)→收尾,各阶段实现住在 app/services/chat/
(包 __init__ 有一眼地图)。Run 并发治理整体在 chat/run_hub.py。
"""
import asyncio
import httpx
import json
import re
import time
import uuid
import logging
from dataclasses import replace
from datetime import datetime
from contextlib import suppress
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import and_, delete as sa_delete, exists, select, func, or_, update as sa_update

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from app.core.model_endpoint import get_model_base_url
from app.core.config import settings
from app.core.database import async_session
from app.models import ChatMessage, ChatThread, live_chat_message_clause, visible_chat_message_clause
from app.services.platform.key_service import key_service
from app.services import sse_protocol
from app.services.agent_harness import model_driver
from app.services.agent_harness.public_commentary import generate_public_commentary
from app.services.agent_harness.responses_protocol import model_is_deepseek
from app.services.agent_harness.plan_execution import (
    exit_plan_mode_tool_env,
    persist_plan_execution_unlock,
    plan_execution_already_unlocked,
)
from app.services.agent_harness.public_errors import RUN_ACCEPT_FAILURE
from app.services.agents import subagent_service, router_service, capability_registry
from app.services.files import thread_attachment_service
from app.services.chat.history_trace_projection import decode_execution_trace_projection
from app.services.knowledge import embedding_service, vector_service, web_search_service, citation_service
from app.services.memory import memory_service, context_service, personalization_service
from app.services.platform import model_window
from app.services.tasks import task_run_service
from app.services.platform.token_estimator import estimate_tokens, estimate_messages, record_usage, calibration_factor

logger = logging.getLogger(__name__)
MAIN_CHAT_MODEL_ID = "gpt-5.5"

_PUBLIC_PREAMBLE_MAX_CHARS = 360


def _append_unbound_active_run_trace_projections(
    messages: List[Dict[str, Any]],
    run_traces: Dict[str, Dict[str, Any]],
) -> None:
    """Append the active execution timeline when no durable chat row owns it yet.

    A newly accepted Run persists its user row before the first assistant row exists.  Older
    rows did not carry ``run_id``, so merely building ``run_traces[run_id]`` left the complete
    snapshot unreachable by the frontend.  Emit one transient assistant-side projection at the
    end of history; once a durable user/assistant row owns the Run, that row remains the anchor
    and no synthetic item is added.
    """
    bound_run_ids = {
        str(message.get("run_id") or "")
        for message in messages
        if message.get("run_id")
    }
    for run_id, trace in run_traces.items():
        normalized_run_id = str(run_id or "")
        if not normalized_run_id or normalized_run_id in bound_run_ids:
            continue
        if str((trace or {}).get("status") or "") not in task_run_service.ACTIVE_RUN_STATUSES:
            continue
        messages.append({
            "role": "assistant",
            "content": "",
            "feedback": None,
            "status": str(trace.get("status") or "running"),
            "run_id": normalized_run_id,
            "agent_mode": trace.get("agent_mode"),
            "citations": None,
            "subagent_calls": None,
            "execution_trace": trace,
            "attachments": None,
        })
        bound_run_ids.add(normalized_run_id)


def _is_deepseek_model(model: str, user_key: Optional[str] = None) -> bool:
    """DeepSeek model aliases are all routed through the Responses main-agent transport."""
    from app.services.agents.agent_service import agent_service

    return model_is_deepseek(
        model,
        aliases=agent_service.model_transport_aliases(model, user_key),
    )


def _requires_reasoning_first_preamble(
    model: str,
    user_key: Optional[str] = None,
) -> bool:
    """Compatibility boundary for Responses models that do not emit commentary phase items.

    DeepSeek is Responses-capable by product contract.  The extra request is not a protocol
    fallback: it only supplies a public sentence before the provider's reasoning-first stream.
    Native commentary models must use their own item instead of paying for a duplicate call.
    """
    return _is_deepseek_model(model, user_key)


async def _generate_public_preamble(
    message: str,
    *,
    model: str,
    api_key: str,
    attachments: Optional[List[Any]] = None,
    plan_profile: bool = False,
    research_profile: bool = False,
    run_id: str = "",
    thread_id: str = "",
    root_run_id: str = "",
    extra_guidance: str = "",
) -> str:
    """Generate the fast, model-authored sentence shown before the main Thinking phase.

    DeepSeek's thinking stream is reasoning-first, so the main tool call cannot expose its
    content before reasoning has finished. This bounded non-thinking request supplies only
    the public preamble; it never chooses tools, claims results, or replaces the main call.
    """
    user_text = re.sub(r"\s+", " ", str(message or "")).strip()
    if (not user_text and not attachments) or not model or not api_key:
        return ""
    filenames = [
        re.sub(r"\s+", " ", str(_att_field(item, "filename") or "")).strip()
        for item in (attachments or [])
        if str(_att_field(item, "filename") or "").strip()
    ][:6]
    mode = "深度研究" if research_profile else "计划" if plan_profile else "执行"
    task_context = [
        f"用户请求：{user_text[:1200]}" if user_text else "用户仅提供了附件，未附文字说明；具体意图尚未明确。",
        f"任务形态：{mode}",
    ]
    if filenames:
        task_context.append("用户提供的文件：" + "、".join(filenames))
    if attachments:
        # 首句在附件预处理之前生成，只有元数据；不能把文件名当成已看过的内容。
        task_context.append(
            "附件边界：本次首句请求仅收到附件元数据，尚未读取文件正文或图片内容。"
            "只能说明准备查看、核对附件；不得根据文件名猜测画面、颜色、主体或文字，"
            "不得声称已经识别、读取完成或已有结论。"
        )
    style = (
        "请对用户说 1–2 句自然、具体的过程首句。"
        "不要用‘我先’‘我会先’‘现在’‘接下来’‘然后’起句，不要写成‘读取 X’"
        "这种单一动作标题，也不要连续罗列工具动作。不要以‘好的’‘收到’‘明白了’开头，"
        "句式应随任务自然变化，不要套用固定开场；不要复述用户原话，"
        "不要暴露私有推理或内部协议，不要声称尚未发生的读取、搜索、修改或交付已完成。"
        "只输出对用户说的这 1–2 句，不要标题、列表或引号。"
    )
    role = (
        str(extra_guidance or "").strip()
        or (
            "你是即将开始完成任务的资深合作者。点出这项任务真正的抓手或必须守住的约束，"
            "并把紧接着要处理的一组相关动作连起来。可以有判断和节奏感，但只描述马上开始的第一阶段。"
        )
    )
    prompt = role + "\n" + style + "\n\n" + "\n".join(task_context)
    text = await generate_public_commentary(
        model=model,
        api_key=api_key,
        developer_prompt=prompt,
        user_prompt="现在写这句公开开场。",
        max_output_tokens=180,
        max_chars=_PUBLIC_PREAMBLE_MAX_CHARS,
        run_id=str(run_id or ""),
        thread_id=str(thread_id or ""),
        root_run_id=str(root_run_id or ""),
        purpose="research_commentary" if research_profile else "public_preamble",
        purpose_detail="initial_preamble",
    )
    text = re.sub(r"^#{1,6}\s*", "", text).strip()
    # 这是独立、无工具、无 reasoning 的公开首句请求，不得复用工具轮 content 的
    # 内部话术过滤器。后者会把「Python 环境可用」「检查工作区路径」等正常任务词
    # 误判成 schema/运行时独白，造成模型已经在 2–3 秒内返回但页面仍然空白。
    # 公开边界只做运行时回执清洗；空响应才交给前端临时活动状态兜底。
    if not text:
        return ""
    return text


def _goal_next_action_text(
    message: str = "",
    *,
    web_search: bool = False,
    knowledge_ids: Optional[List[str]] = None,
    attachments: Optional[List[Any]] = None,
    plan_profile: bool = False,
    research_profile: bool = False,
    pure_qa: bool = False,
) -> str:
    """脚本化开场已废弃（用户拍板：按模型自己的想法说，不由规则代写「我先干嘛」）。

    保留函数签名供 accept/worker 调用点与单测 import 不炸；恒返回空串。
    用户可见的第一句过程/正文应由模型 delta / commentary / 工具步骤产出。
    系统只需注入真实「当前时间」（见 turn_context_builder._current_time_line）。
    """
    _ = (
        message, web_search, knowledge_ids, attachments,
        plan_profile, research_profile, pure_qa,
    )
    return ""


def _loads_attachments(raw: Optional[str]) -> Optional[list]:
    """attachments_json 反序列化（历史消息接口）：坏数据静默为 None，不影响整个会话加载。"""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) and data else None
    except Exception:  # noqa: BLE001
        return None


def _unavailable_selected_attachments(attachments: Optional[list]) -> list:
    """只识别“我的文件”实体已删除/过期；解析 partial/failed 仍走原降级提示，不误拦任务。"""
    return [
        a for a in (attachments or [])
        if _att_field(a, "file_id")
        and _att_field(a, "status") == "failed"
        and "不可用" in str(_att_field(a, "note") or _att_field(a, "text") or "")
    ]


def _candidate_snapshot(subagent_candidates: Optional[list]) -> list:
    """挂起游标里的候选快照（语义发现升级 §七）：只存重建工具目录所需的最小字段。
    快照只是恢复主模型工具上下文，**不是权限凭证**——恢复后真正调用子智能体时，
    runner 内部仍实时重查 MySQL 权限/应用状态/线上版本。"""
    snap = []
    for c in subagent_candidates or []:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        snap.append({
            "id": str(c.get("id")),
            "name": str(c.get("name") or "")[:64],
            "description": str(c.get("description") or "")[:200],
            "route_description": str(c.get("route_description") or "")[:200],
            "published_version": int(c.get("published_version") or 0),
            "icon": str(c.get("icon") or "")[:512],
        })
    return snap[: max(1, int(settings.SUBAGENT_DISCOVERY_TOP_K or 12))] if snap else []


def _is_explicit_cancel_choice(value: Any) -> bool:
    """识别交互卡中用户明确选择的取消项。

    这里只接受短、完整的取消指令，不做包含匹配：例如“取消后重新发起”不是终止指令。
    表单自由文本也不会走这条规则，调用方还必须确认当前交互类型是 userSelect。
    """
    if not isinstance(value, str):
        return False
    normalized = re.sub(r"[\s。.!！?？_-]+", "", value).lower()
    return bool(re.fullmatch(
        r"(?:取消|终止|停止)(?:本次|当前)?(?:申请|操作|流程|任务)?|cancel|abort|stop",
        normalized,
    ))


async def _resume_candidates(
    orchestration: dict,
    user_context,
    *,
    run_id: str = "",
    thread_id: str = "",
) -> list:
    """恢复轮候选重建（语义发现升级 §七）：优先用挂起时写入的候选快照，避免重新召回
    导致候选漂移（模型挂起前看到的清单与恢复后不一致）。快照缺失（存量挂起 Run）才
    重新发现；发现失败返回空（不注册 call_subagent，恢复流程不受影响）。"""
    orchestration = orchestration or {}
    snap = [c for c in (orchestration.get("subagent_candidates") or [])
            if isinstance(c, dict) and c.get("id")]
    if snap:
        return snap
    if not user_context:
        return []
    try:
        query = str(orchestration.get("goal") or "")
        if not query:
            msgs = orchestration.get("messages") or []
            query = next(
                (str(m.get("content") or "") for m in reversed(msgs)
                 if isinstance(m, dict) and m.get("role") == "user"
                 and isinstance(m.get("content"), str)),
                "",
            )
        return await capability_registry.discover_for_call(
            user=user_context, query=query,
            top_k=settings.SUBAGENT_DISCOVERY_TOP_K,
            audit_run_id=run_id,
            audit_thread_id=thread_id,
        )
    except Exception:  # noqa: BLE001
        logger.warning("恢复轮候选发现失败，本轮不注册 call_subagent", exc_info=True)
        return []


# ===== Phase A（实施说明 §2.3）：上下文准备层已原样搬迁至 chat/turn_context_builder =====
# 功能完全等价（§2.1）：此处 re-import 保持文件内全部调用点与既有行为零变化。
from app.services.chat import (
    execution_profile,
    main_tool_turn,
    run_hub,
    subagent_turn,
    turn_finalizer,
    turn_prepare,
)
from app.services.chat import plain_turn
from app.services.chat.types import TurnContext, TurnEnv, TurnOutcome
from app.services.chat.turn_decision import (
    REVISION_TARGET_UNRESOLVED_GUIDANCE,
    build_revision_candidate_guidance,
    anaphoric_target_reference,
    bare_control_message,
    decide_turn,
    write_denied_request,
)
from app.services.chat.turn_decision import named_files as _named_files_in_message
from app.services.chat.turn_context_builder import (# noqa: F401
    _CANCEL_WORDS,
    _CONTEXT_WINDOW,
    _SKILL_INSTRUCTIONS_MAX,
    _as_turn_content,
    _att_field,
    _attachments_degradation_note,
    _attachments_meta,
    merge_composer_reference_meta,
    _build_system_prompt,
    _clean_prompt_text,
    _history_hard_cap_tokens,
    _system_prompt_budget_tokens,
    _collect_knowledge_ids,
    _current_time_line,
    _estimate_prompt_tokens,
    _extract_readme,
    _fetch_skill_catalog_block,
    _fetch_trusted_skills,
    _format_skill_block,
    _image_sources,
    _is_cancel_intent,
    _is_context_length_error,
    _make_skill_packages_provider,
    _resolve_kb_tenant,
    rewrite_plan_guard_for_execution,
    _PLAN_EXECUTION_GUARD,
    _split_attachments,
    model_supports_vision,
    with_subagent_identity,
    build_resume_checkpoint,
    needs_resume_checkpoint,
)


_PPT_STYLE_REFERENCE_TURN_RE = re.compile(
    r"(参考|视觉语言|视觉\s*DNA|风格|调性|高级感|类似|像这|按这|照着|"
    r"style|reference|look\s*and\s*feel)",
    re.I,
)
_IMAGE_EDIT_TARGET_RE = re.compile(
    r"(修改|编辑|处理|裁剪|抠图|去水印|换背景|调色).{0,10}"
    r"(这张|该|上传|图片|照片)",
    re.I,
)
_PPT_CREATE_RE = re.compile(
    r"(制作|生成|创建|新建|做一份|做个|导出)[\s\S]{0,160}(pptx?|ppt|演示文稿|幻灯片)|"
    r"(pptx?|ppt|演示文稿|幻灯片)[\s\S]{0,160}(制作|生成|创建|新建|导出)",
    re.I,
)


def _attachment_is_reference_only(message: str, attachment: Any) -> bool:
    """新建 PPT 时的图片可以是风格参考，不是 RevisionTarget。"""
    text = str(message or "")
    if not (_PPT_CREATE_RE.search(text) and _PPT_STYLE_REFERENCE_TURN_RE.search(text)):
        return False
    if _IMAGE_EDIT_TARGET_RE.search(text):
        return False
    kind = str(_att_field(attachment, "kind") or "").lower()
    name = str(_att_field(attachment, "filename") or "").lower()
    return kind == "image" or name.endswith(
        (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
    )


def _has_revision_target_attachments(
    message: str, attachments: Optional[List[Any]],
) -> bool:
    return any(
        _att_field(item, "file_id") and not _attachment_is_reference_only(message, item)
        for item in (attachments or [])
    )


def _decide_turn_with_attachment_intent(
    message: str,
    attachments: Optional[List[Any]],
    *,
    active_run: bool = False,
):
    """参考图是新产物的输入，不是旧产物的修订目标。

    TurnDecision 的通用词表会把消息中的 ``PPT`` 视为 existing target，再把
    “不要复制参考图”里的动作词误认成修订。这里只在所有已选附件均已被
    严格判定为 PPT 风格参考时覆盖，不改动真正的 PPTX/图片编辑任务。
    """
    selected = list(attachments or [])
    decision = decide_turn(
        message,
        has_selected_files=_has_revision_target_attachments(message, selected),
        active_run=active_run,
    )
    if selected and all(_attachment_is_reference_only(message, item) for item in selected):
        return replace(
            decision,
            intent="execute",
            authority="mutate",
            reason_code="ppt_style_reference_create",
            revision=False,
            allow_create=True,
        )
    return decision




# 标题生成等 fire-and-forget 任务的强引用（B4 教训：裸 create_task 可能被 GC 静默丢弃，
# 表现为首轮会话标题偶发停在「新对话」且异常永不被取回）
_bg_title_tasks: set = set()


def _spawn_title_task(coro) -> None:
    t = asyncio.ensure_future(coro)
    _bg_title_tasks.add(t)
    t.add_done_callback(_bg_title_tasks.discard)


def _history_listable_thread_clause():
    """已保存用户消息的会话始终可找回，Run 成败只影响会话内状态。"""
    return exists(
        select(1).where(
            ChatMessage.thread_id == ChatThread.id,
            ChatMessage.role == "user",
            visible_chat_message_clause(),
            ChatMessage.content.isnot(None),
            ChatMessage.content != "",
        )
    )


def _is_plan_confirmation_payload(payload: Optional[dict], *, plan_mode: bool = False) -> bool:
    data = payload or {}
    return bool(
        plan_mode
        or data.get("kind") == "plan_confirmation"
        or data.get("revision_gate")
    )


def _plan_resume_transcript_text(value: Any) -> str:
    """Mirror the visible Plan-resume bubble; hidden skip controls stay out of history."""
    from app.services.tasks.plan_service import choice_skips_plan

    if choice_skips_plan(value):
        return ""
    if isinstance(value, str):
        return value.strip()[:20_000]
    if isinstance(value, (list, tuple)):
        return " ".join(
            str(item).strip() for item in value if str(item or "").strip()
        )[:20_000]
    return ""


async def _unlock_plan_execution_before_resume(
    run_id: str,
    resume_value: Any,
    run: Dict[str, Any],
) -> Dict[str, Any]:
    """Flip Run facts before the first SSE thread frame so the capsule cannot relight."""
    from app.services.agent_harness import run_store as unlock_run_store
    from app.services.tasks.plan_service import choice_approves_plan, choice_skips_plan

    if choice_skips_plan(resume_value):
        return run
    state = run.get("state") if isinstance(run.get("state"), dict) else {}
    tool_env = dict(((state.get("orchestration") or {}).get("tool_env") or {}))
    hung = bool(tool_env.get("plan_mode"))
    snap = None
    try:
        snap = await unlock_run_store.get_run_snapshot(run_id)
    except Exception:  # noqa: BLE001
        snap = None
    if not (
        (hung and choice_approves_plan(resume_value))
        or plan_execution_already_unlocked(snap, tool_env)
    ):
        return run
    try:
        await persist_plan_execution_unlock(run_id)
    except Exception:  # noqa: BLE001
        logger.warning("计划执行解锁失败 run=%s", run_id, exc_info=True)
    unlocked = dict(run)
    unlocked["agent_mode"] = "standard"
    merged = dict(state)
    merged["agent_mode"] = "standard"
    merged["capability_scope"] = "default"
    if str(merged.get("phase") or "") in {
        "", "planning", "waiting_confirmation", "plan_ready", "waiting_clarification",
    }:
        merged["phase"] = "executing"
    unlocked["state"] = merged
    return unlocked


async def _attach_plan_review_payload(
    run_id: str,
    payload: dict,
    *,
    revision_gate: bool = False,
) -> dict:
    """Attach the structured plan onto the confirmation card payload."""
    from app.services.agent_harness import run_store
    from app.services.tasks import plan_service

    out = dict(payload or {})
    out["kind"] = "plan_confirmation"
    if revision_gate:
        out["revision_gate"] = True
    try:
        current = await run_store.get_run_state(run_id)
        state = ((current or {}).get("state") or {})
        steps = await plan_service.get_current_plan(run_id)
        pending = (
            state.get("pending_plan_revision")
            if isinstance(state.get("pending_plan_revision"), dict)
            else None
        )
        draft = (pending or {}).get("steps") if revision_gate else None
        previous = (pending or {}).get("previous_steps") if revision_gate else None
        if draft:
            out["plan_steps"] = plan_service.compact_review_steps(draft)
            out["previous_steps"] = plan_service.compact_review_steps(previous or steps)
        elif steps:
            out["plan_steps"] = plan_service.compact_review_steps(steps)
        out["plan_version"] = int(
            (steps[0].get("plan_version") if steps else 0) or state.get("plan_version") or 0
        )
        approved = state.get("approved_plan_version")
        if approved is not None and str(approved) != "":
            out["approved_version"] = int(approved)
        contract = None
        if steps and isinstance(steps[0].get("goal_contract"), dict):
            contract = steps[0]["goal_contract"]
        elif isinstance(state.get("goal_contract"), dict):
            contract = state["goal_contract"]
        if contract:
            out["goal_contract"] = contract
    except Exception:  # noqa: BLE001
        logger.debug("plan review payload attach failed run=%s", run_id, exc_info=True)
    return out


class HarnessOrchestrator:
    def __init__(self) -> None:
        # Run 并发治理(泵/订阅/取消/僵尸/部分落库)整体在 chat/run_hub.RunHub;
        # 下面的容器引用与 hub 共享同一对象——既有测试/调用直接戳 svc._run_tasks 等仍然成立。
        self._hub = run_hub.RunHub()
        self._run_tasks = self._hub._run_tasks
        self._run_subscribers = self._hub._run_subscribers
        self._run_buffers = self._hub._run_buffers
        self._run_meta = self._hub._run_meta
        self._run_lock = self._hub._run_lock
        self._bg_persist_tasks = self._hub._bg_persist_tasks
        self._persist_tasks_by_run = self._hub._persist_tasks_by_run
        self._persist_tasks_by_thread = self._hub._persist_tasks_by_thread
    async def _resolve_model(self, model: Optional[str] = None, user_key: Optional[str] = None) -> str:
        from app.services.agents.agent_service import agent_service
        models = await agent_service.get_models(user_key=user_key)
        valid_ids = {m.id for m in models}
        if model:
            if model not in valid_ids:
                raise HTTPException(status_code=400, detail="无效的对话模型")
            return model
        for m in models:
            if m.is_default:
                return m.id
        return models[0].id if models else ""

    async def prepare_chat(self, user_id: str, model: Optional[str]) -> tuple[str, str]:
        """在响应开始前完成 Key 和模型校验，保证流式错误状态码正确。"""
        user_key = await key_service.get_user_key(user_id)
        if not user_key:
            raise HTTPException(status_code=403, detail="当前账号未分配模型 API Key，请联系管理员")
        resolved_model = await self._resolve_model(model, user_key=user_key)
        if not resolved_model:
            raise HTTPException(status_code=400, detail="当前账号没有可用的对话模型")
        return user_key, resolved_model

    async def prepare_resume_chat(self, user_id: str, run_id: str) -> tuple[str, str]:
        """HITL 续接的 Key/模型解析：优先沿用原 Run 发起时的模型（AgentRun.model）。

        续接是「继续同一轮未完成的回答」，语义上必须与暂停前同一模型——否则用户选的
        非默认模型会在提交表单后被静默换成账号默认模型。仅当原模型已下线（校验 400）
        时才退回默认模型继续，好过让续接直接失败。
        """
        run = await task_run_service.get_run(run_id, user_id)
        run_model = str((run or {}).get("model") or "") or None
        try:
            return await self.prepare_chat(user_id, run_model)
        except HTTPException as e:
            if not (run_model and e.status_code == 400):
                raise
            return await self.prepare_chat(user_id, None)

    def _create_llm(self, model: str, api_key: str) -> ChatOpenAI:
        return ChatOpenAI(
            model=model,
            base_url=get_model_base_url(),
            api_key=api_key,
            streaming=True,
            stream_usage=True,  # 请求流式回传真实 usage（§13 用真值校准上下文用量指示）
            # Harness owns retries and exposes their state.  Hidden SDK retries would multiply
            # physical requests and make root-Run usage attribution incomplete.
            max_retries=0,
            reasoning_effort="medium" if model.strip().lower() == MAIN_CHAT_MODEL_ID else None,
        )

    async def _ensure_thread(
        self, thread_id: Optional[str], user_id: str, *, origin: Optional[str] = None,
        workspace_folder_id: Optional[str] = None,
    ) -> str:
        from app.services.files.work_folders import require_folder
        from app.services.files.user_file_service import UserFileError

        async with async_session() as session:
            if workspace_folder_id and origin:
                raise HTTPException(status_code=422, detail="工作文件夹仅用于普通主对话")
            if workspace_folder_id:
                try:
                    await require_folder(session, user_id, workspace_folder_id)
                except UserFileError as exc:
                    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
            if thread_id:
                existing = await session.get(ChatThread, thread_id)
                if existing and existing.user_id == user_id:
                    bound_folder = getattr(existing, "workspace_folder_id", None)
                    if workspace_folder_id and bound_folder != workspace_folder_id:
                        raise HTTPException(status_code=409, detail="切换工作文件夹请新建对话")
                    if bound_folder:
                        try:
                            await require_folder(session, user_id, bound_folder)
                        except UserFileError as exc:
                            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
                    requested_origin = str(origin or "").strip()
                    existing_origin = str(existing.origin or "").strip()
                    if requested_origin and existing_origin and requested_origin != existing_origin:
                        raise HTTPException(
                            status_code=409,
                            detail="会话预设不能切换，请新建对应助手会话。",
                        )
                    if requested_origin and not existing_origin:
                        raise HTTPException(
                            status_code=409,
                            detail="普通主对话不能切换为内置助手，请新建会话。",
                        )
                    return thread_id

            new_id = f"thread_{user_id}_{uuid.uuid4().hex[:12]}"
            session.add(ChatThread(
                id=new_id, user_id=user_id, origin=origin,
                workspace_folder_id=workspace_folder_id,
            ))
            await session.commit()
            return new_id

    async def get_thread_model_setting(self, user_id: str, thread_id: str) -> Dict[str, Any]:
        """Return the sticky next-Run model and the most recently accepted Run model."""
        async with async_session() as session:
            thread = await session.get(ChatThread, thread_id)
            if not thread or thread.user_id != user_id:
                raise HTTPException(status_code=404, detail="会话不存在")
            from app.services.chat.builtin_assistants.registry import preset_from_thread_origin
            from app.services.files.work_folders import require_folder
            from app.services.files.user_file_service import UserFileError
            folder = None
            if getattr(thread, "workspace_folder_id", None):
                try:
                    folder = await require_folder(session, user_id, thread.workspace_folder_id)
                except UserFileError:
                    folder = {"id": thread.workspace_folder_id, "name": "文件夹已删除", "unavailable": True}
            return {
                "model": str(thread.model or ""),
                "last_run_model": str(thread.last_run_model or ""),
                "assistant_preset": preset_from_thread_origin(thread.origin),
                "workspace_folder": folder,
            }

    async def set_thread_model(self, user_id: str, thread_id: str, model: str) -> str:
        """Persist the model for subsequent Runs without mutating any active Run."""
        async with async_session() as session:
            thread = await session.get(ChatThread, thread_id)
            if not thread or thread.user_id != user_id:
                raise HTTPException(status_code=404, detail="会话不存在")
        _key, resolved = await self.prepare_chat(user_id, model)
        async with async_session() as session:
            thread = await session.get(ChatThread, thread_id)
            if not thread or thread.user_id != user_id:
                raise HTTPException(status_code=404, detail="会话不存在")
            thread.model = resolved
            await session.commit()
        return resolved

    async def _record_thread_run_model(
        self, user_id: str, thread_id: str, model: str, *, update_setting: bool,
    ) -> None:
        """Record an accepted Run model; queue snapshots must not replace the next setting."""
        async with async_session() as session:
            thread = await session.get(ChatThread, thread_id)
            if not thread or thread.user_id != user_id:
                return
            thread.last_run_model = model
            if update_setting:
                thread.model = model
            await session.commit()

    async def accept_harness_run(self, **kwargs) -> Dict[str, str]:
        """Persist a Harness command and enqueue it without model/file preparation.

        This is the HTTP acceptance boundary.  Once it returns, the user message, Run, accepted
        event and recoverable Worker input all exist in durable storage.  The API process never
        owns the execution coroutine for this path.
        """
        from app.core.runtime_db import runtime_enabled
        from app.services.agent_harness import run_store

        if not runtime_enabled():
            raise HTTPException(status_code=503, detail="Agent Runtime 状态服务未启用")

        user_id = str(kwargs["user_id"])
        from app.services.chat.tools.client_location import normalize_client_network_context
        client_network_context = normalize_client_network_context(
            kwargs.pop("client_network_context", None),
        )
        from app.services.chat.builtin_assistants.registry import (
            is_supported_preset,
            origin_for_preset,
        )
        from app.services.chat.builtin_assistants.runtime import get_builtin_runtime_policy
        requested_preset = str(kwargs.get("assistant_preset") or "").strip().lower()
        if requested_preset and not is_supported_preset(requested_preset):
            raise HTTPException(status_code=422, detail="assistant_preset 不合法")
        access_checked = bool(kwargs.pop("_builtin_access_checked", False))
        if requested_preset and not access_checked and kwargs.get("user_context") is not None:
            # Non-HTTP probes/scripts can call this acceptance boundary directly.  They must not
            # bypass the same system-app entitlement enforced by the router.
            from app.services.chat.builtin_app_access import require_builtin_app_access
            await require_builtin_app_access(kwargs["user_context"], requested_preset)
            access_checked = True
        client_request_id = str(kwargs.get("client_request_id") or "") or None
        if client_request_id:
            try:
                existing = await task_run_service.get_run_by_client_request(
                    user_id, client_request_id,
                )
            except task_run_service.RunLookupUnavailable as exc:
                raise HTTPException(status_code=503, detail="运行状态服务暂时不可用") from exc
            if existing and existing.get("thread_id"):
                return {"thread_id": str(existing["thread_id"]), "run_id": str(existing["id"])}

        thread_id = await self._ensure_thread(
            kwargs.get("thread_id"),
            user_id,
            workspace_folder_id=kwargs.pop("workspace_folder_id", None),
            origin=(
                origin_for_preset(requested_preset)
                or kwargs.get("thread_origin")
            ),
        )
        thread_models = await self.get_thread_model_setting(user_id, thread_id)
        assistant_preset = (
            str(thread_models.get("assistant_preset") or "").strip().lower()
            or requested_preset
        )
        if assistant_preset and not access_checked and kwargs.get("user_context") is not None:
            # Existing built-in Thread + omitted request preset is still a built-in execution.
            # Resolve the authoritative origin first, then enforce the same current entitlement.
            from app.services.chat.builtin_app_access import require_builtin_app_access
            await require_builtin_app_access(kwargs["user_context"], assistant_preset)
        runtime_policy = get_builtin_runtime_policy(assistant_preset)
        # Built-in assistants have explicit allowlisted tools and cannot consume this context.
        # Do not retain an encrypted address for a Run that has no location capability.
        if runtime_policy is not None:
            client_network_context = None
        kwargs["thread_id"] = thread_id
        kwargs["assistant_preset"] = assistant_preset
        if kwargs.get("interview_input") and assistant_preset != "interview":
            raise HTTPException(status_code=422, detail="面试操作只能用于面试助手会话")
        if runtime_policy:
            await runtime_policy.prepare_request(kwargs)
        run_id = uuid.uuid4().hex
        message = str(kwargs.get("message") or "")
        skill_ids = list(kwargs.get("skill_ids") or [])
        knowledge_ids = list(kwargs.get("knowledge_ids") or [])
        selected_knowledge = list(kwargs.get("selected_knowledge") or [])
        attachments = list(kwargs.get("attachments") or [])
        web_search = bool(kwargs.get("web_search"))
        inherited_profile_id = None
        inherited_image_requirement = None
        resume_source_run_id = str(kwargs.get("resume_source_run_id") or "").strip()
        requested_resume_source_run_id = resume_source_run_id
        _accept_model = (
            str(kwargs.get("model") or "").strip()
            or str(thread_models.get("model") or "").strip()
            or str(kwargs.get("resolved_model") or "").strip()
            or None
        )
        previous_model = str(thread_models.get("last_run_model") or "").strip()
        prepare_task = asyncio.create_task(self.prepare_chat(user_id, _accept_model))
        queue_dispatch = bool(kwargs.get("queue_item_id"))
        resume_source = None
        if needs_resume_checkpoint(message) or queue_dispatch or resume_source_run_id:
            resume_source = await run_store.load_latest_finalized_execution_profile_source(
                thread_id=thread_id,
                user_id=user_id,
                exclude_run_id=run_id,
                preferred_run_id=requested_resume_source_run_id,
            )
            if resume_source:
                resume_source_run_id = str(resume_source.get("run_id") or "").strip()
                previous_profile = resume_source.get("execution_profile") or {}
                inherited_profile_id = str(previous_profile.get("id") or "") or None
                previous_qa = previous_profile.get("qa_contract") or {}
                if isinstance(previous_qa, dict):
                    inherited_image_requirement = previous_qa.get("image_requirement")
            elif requested_resume_source_run_id:
                # Explicit continuation sources are ownership/policy validated above. A
                # sensitive-word terminal Run must not re-enter a later queued or clean turn.
                resume_source_run_id = ""
            # Resume skill recovery is owned by the worker (resume_source_run_id in pending).
        agent_mode = str(kwargs.get("agent_mode") or "standard").strip().lower()
        if resume_source_run_id:
            from app.services.agent_harness.goal_contract import inherit_agent_mode_for_resume
            agent_mode = inherit_agent_mode_for_resume(
                previous_mode=str((resume_source or {}).get("agent_mode") or ""),
                previous_status=str((resume_source or {}).get("status") or ""),
                requested_mode=agent_mode,
                message=message,
            )
        if runtime_policy and runtime_policy.forced_resume_mode:
            agent_mode = runtime_policy.forced_resume_mode
            kwargs["agent_mode"] = agent_mode
        from app.services.agent_harness.profiles import get_profile
        try:
            profile = get_profile(agent_mode)
        except ValueError:
            prepare_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await prepare_task
            raise HTTPException(status_code=422, detail="agent_mode 不合法")
        decision = _decide_turn_with_attachment_intent(
            message, attachments, active_run=False,
        )
        route = decision.route(
            has_explicit_resources=bool(
                skill_ids or knowledge_ids or selected_knowledge or attachments or web_search
                or kwargs.get("file_ids") or kwargs.get("thread_ids")
                or resume_source_run_id
            ),
            message=message,
        )
        if profile.requires_initial_plan_confirmation:
            route = "planning"
        elif profile.auto_research_plan:
            route = "agent"
        if runtime_policy:
            route = "agent"

        try:
            # ：受理必须落库**解析后的**最终模型。
            # 根因：pending 过滤了 resolved_model；worker 见 model 为空就 prepare_chat(None)
            # 落到账号 is_default（实测 qwen3.7-max 免费额度 403），而 V1 路径有显式 deepseek。
            try:
                _k, _resolved = await prepare_task
                _ = _k
                if _resolved:
                    _accept_model = _resolved
            except HTTPException:
                # 无 key / 非法模型：仍继续用原字符串让 worker 再报清晰错；create_run 不阻断
                pass
            created = await task_run_service.create_run(
                run_id=run_id,
                thread_id=thread_id,
                user_id=user_id,
                kind="chat",
                model=_accept_model,
                goal=message[:200],
                agent_mode=agent_mode,
                client_request_id=client_request_id,
                status="created",
                claim_owner=False,
            )
        except task_run_service.DuplicateRequestConflict as dup:
            return {"thread_id": dup.thread_id, "run_id": dup.run_id}
        except task_run_service.ActiveRunConflict as exc:
            raise HTTPException(
                status_code=409, detail="当前会话仍在执行，请继续引导、排队或先停止当前任务。",
            ) from exc
        if not created:
            raise HTTPException(status_code=503, detail="运行状态存储暂时不可用")
        if _accept_model:
            await self._record_thread_run_model(
                user_id, thread_id, _accept_model, update_setting=not queue_dispatch,
            )

        user_message_id: Optional[int] = None
        try:
            await run_store.initialize_run_state(
                run_id,
                agent_mode=agent_mode,
                decision_route=route,
                capability_scope=(
                    "default" if runtime_policy and runtime_policy.action_authority == "mutate"
                    else "planning" if route == "planning"
                    else "inspect" if decision.authority == "inspect"
                    else "default"
                ),
                client_network_context=client_network_context,
            )
            frozen_profile_evidence = execution_profile.freeze_profile_evidence(
                message=message,
                skill_ids=skill_ids,
                selected_skills=list(kwargs.get("selected_skills") or []),
                attachments=attachments,
                inherited_profile_id=inherited_profile_id,
                inherited_image_requirement=inherited_image_requirement,
            )
            if not await run_store.store_execution_profile_evidence(
                run_id, frozen_profile_evidence,
            ):
                raise RuntimeError("执行 Profile 证据快照写入失败")
            # 请求级技能清单持久化到 run.state：终态任务快照与「继续」轮据此恢复技能上下文
            #（skill_ids 是一次性语义，前端发送后清空，快照是唯一还能找回它的地方）
            if skill_ids or assistant_preset:
                try:
                    state_patch = {
                        "assistant_preset": assistant_preset or None,
                    }
                    if skill_ids:
                        state_patch["skill_ids"] = list(skill_ids)
                    if kwargs.get("assistant_preset_snapshot"):
                        state_patch["assistant_preset_snapshot"] = kwargs.get("assistant_preset_snapshot")
                    await task_run_service.save_run_state(run_id, state_patch)
                except Exception:  # noqa: BLE001
                    logger.debug("assistant_preset 持久化失败（不阻断）run=%s", run_id, exc_info=True)
            try:
                from app.services.agent_harness.goal_contract import seed_goal_contract_for_resume
                _contract = await seed_goal_contract_for_resume(
                    message, decision, route=route,
                    resume_source_run_id=resume_source_run_id,
                )
                await run_store.patch_run_state(
                    run_id, {"goal_contract": _contract.to_state()},
                )
            except Exception:  # noqa: BLE001
                logger.debug("goal_contract 持久化失败（不阻断）run=%s", run_id, exc_info=True)
            if not kwargs.get("regenerate"):
                atts_meta = merge_composer_reference_meta(
                    attachments,
                    selected_skills=(
                        None if runtime_policy and runtime_policy.hide_selected_skill_references
                        else kwargs.get("selected_skills")
                    ),
                    selected_knowledge=kwargs.get("selected_knowledge"),
                    subagent_name=str(kwargs.get("subagent_name") or ""),
                    web_search=bool(kwargs.get("web_search")),
                )
                async with async_session() as session:
                    row = ChatMessage(
                        thread_id=thread_id,
                        role="user",
                        content=message,
                        # 用户输入是本 Run 的持久起点。助手行尚未落库时，
                        # 历史接口仍能把完整 execution_trace 精确绑回这一轮。
                        run_id=run_id,
                        attachments_json=(
                            json.dumps(atts_meta, ensure_ascii=False) if atts_meta else None
                        ),
                    )
                    session.add(row)
                    thread = await session.get(ChatThread, thread_id)
                    if thread:
                        if not thread.title:
                            thread.title = message[:50]
                        thread.updated_at = func.now()
                    await session.commit()
                    user_message_id = row.id

            if runtime_policy and runtime_policy.accept_input:
                await runtime_policy.accept_input({
                    "user_id": user_id, "thread_id": thread_id, "run_id": run_id,
                    "interview_input": kwargs.get("interview_input"),
                    "message": message, "attachments": attachments,
                })

            pending = {
                key: value for key, value in kwargs.items()
                if key not in {"newapi_key", "resolved_model", "token", "thread_origin"}
            }
            pending.update({
                "thread_id": thread_id,
                "run_id": run_id,
                # Worker recovers resume skills from this source; accept does not wait on snapshot I/O.
                "skill_ids": list(skill_ids),
                "resume_source_run_id": resume_source_run_id,
                "protocol": sse_protocol.HARNESS,
                "precreated_run": True,
                "precreated_user_message_id": user_message_id,
                "initial_sequence": 1,
                # 显式写入 model：pending 过滤掉了 resolved_model，必须把最终模型回填到 model
                "model": _accept_model,
                "previous_model": previous_model or None,
            })
            context = kwargs.get("user_context")
            if context is not None:
                pending["user_context"] = (
                    context.model_dump() if hasattr(context, "model_dump") else dict(context)
                )
            if not await run_store.store_pending_input(
                run_id, pending, access_token=str(kwargs.get("token") or ""),
            ):
                raise RuntimeError("Worker 输入快照写入失败")

            channel = sse_protocol.SSEChannel(sse_protocol.HARNESS, thread_id, run_id)
            await task_run_service.record_sse_payload(run_id, channel.run_accepted(route))
            # ：受理即下发可见的「接下来干什么」。
            # 根因：只写 run.accepted 后入队，worker 冷启或排队时 UI 长时间只剩
            # 「已运行 Xs · 正在思考」空白；用户要的是立刻知道下一步。
            # ：受理阶段**禁止**投影临时任务板。
            # 旧行为会先固定闪 3 步，再被真 update_plan/工具 provisional 覆盖，用户体感假同步；
            # 且 accept 投影的计划不在 LoopState，工具推进/终态 settle 接不住，任务板会假跑。
            # 任务协作只在真 update_plan，或首个多步工具动手后的 provisional 出现。
            try:
                direct_answer = route == "direct_answer"
                _next = _goal_next_action_text(
                    message,
                    web_search=web_search,
                    knowledge_ids=knowledge_ids,
                    attachments=attachments,
                    plan_profile=profile.requires_initial_plan_confirmation,
                    research_profile=profile.auto_research_plan,
                    pure_qa=direct_answer,
                )
                if str(_next or "").strip():
                    await task_run_service.record_sse_payload(
                        run_id, channel.message_commentary(str(_next).strip()),
                    )
            except task_run_service.EventPersistenceError:
                raise
            except Exception:  # noqa: BLE001
                logger.debug("accept early next-action failed run=%s", run_id, exc_info=True)
            if not await run_store.enqueue_job(run_id, wake_reason="accepted"):
                raise RuntimeError("Worker Job 入队失败")
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, HTTPException) and 400 <= exc.status_code < 500:
                await task_run_service.finalize_run(run_id, "failed", error=str(exc.detail))
                raise
            logger.exception("Run 受理失败 run=%s: %s", run_id, exc)
            # Acceptance infrastructure failures are recoverable external faults.  Keep the
            # same Run and its pending input so the Worker can retry after the dependency is
            # back; this path must not manufacture a terminal failed Run before the model or
            # Completion Verifier has seen the facts.
            recovered = await task_run_service.recover_run_after_fault(
                run_id,
                reason=f"accept:{type(exc).__name__}:{str(exc)[:180]}",
                backoff_seconds=0,
            )
            if recovered:
                await task_run_service.append_run_event(
                    run_id, "run.phase.changed", {"phase": "waiting_system"},
                )
            raise HTTPException(status_code=503, detail=RUN_ACCEPT_FAILURE) from exc
        return {"thread_id": thread_id, "run_id": run_id, "model": _accept_model or ""}

    async def start_resume_chat_run(self, *, user_id: str, user_context, run_id: str,
                                    resume_value: Any, resume_id: Optional[str] = None,
                                    token: str = "", newapi_key: Optional[str] = None,
                                    resolved_model: Optional[str] = None,
                                    protocol: str = sse_protocol.HARNESS) -> Dict[str, Any]:
        """HITL 恢复进后台 Run(时序与排水窗口在 run_hub.launch_resume)。"""
        return await self._hub.launch_resume(
            user_id=user_id, run_id=run_id, protocol=protocol,
            source_factory=lambda: self.resume_chat(
                user_id=user_id, user_context=user_context, run_id=run_id,
                resume_value=resume_value, resume_id=resume_id, token=token,
                newapi_key=newapi_key, resolved_model=resolved_model, protocol=protocol,
            ),
        )

    async def subscribe_run(self, **kwargs) -> AsyncGenerator[str, None]:
        async for payload in self._hub.subscribe_run(**kwargs):
            yield payload

    async def cancel_chat_run(self, user_id: str, run_id: str) -> Dict[str, str]:
        return await self._hub.cancel_chat_run(user_id, run_id)

    async def get_run_state(self, user_id: str, run_id: str) -> Dict[str, Any]:
        return await self._hub.get_run_state(user_id, run_id)

    async def _resolve_active_run(self, thread_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        return await self._hub._resolve_active_run(thread_id, user_id)

    async def _resolve_active_runs(
        self,
        thread_ids: List[str],
        user_id: str,
    ) -> Dict[str, Dict[str, Any]]:
        return await self._hub._resolve_active_runs(thread_ids, user_id)

    async def _is_zombie_run(self, run: Dict[str, Any]) -> bool:
        return await self._hub._is_zombie_run(run)

    def _spawn_bg(self, coro) -> None:
        self._hub._spawn_bg(coro)

    def _spawn_partial_persist(self, run_id: str, thread_id: str, content: str,
                               status: str = "cancelled") -> None:
        self._hub._spawn_partial_persist(run_id, thread_id, content, status=status)

    async def _await_pending_partial_persist(self, thread_id: str) -> None:
        await self._hub._await_pending_partial_persist(thread_id)
    async def get_thread_active_run(self, user_id: str, thread_id: str) -> Optional[Dict[str, Any]]:
        run = await self._resolve_active_run(thread_id, user_id)
        if not run:
            return run
        from app.services.agent_harness.plan_store import get_plan_snapshot
        plan = await get_plan_snapshot(str(run["id"]))
        run["plan"] = plan.model_dump(mode="json") if plan else None
        state = run.get("state") if isinstance(run.get("state"), dict) else {}
        contract = state.get("goal_contract")
        if isinstance(contract, dict):
            run["goal_contract"] = contract
        if state.get("approved_plan_version") is not None:
            run["approved_plan_version"] = state.get("approved_plan_version")
            if isinstance(run.get("plan"), dict):
                run["plan"] = {
                    **run["plan"],
                    "approved_version": state.get("approved_plan_version"),
                }
        return run

    # ===== Phase A（§2.3）：收尾持久化辅助已原样搬迁至 chat/turn_finalizer =====
    # 同名方法委托：调用点与行为零变化（§2.1 等价约束）。
    async def _drop_last_assistant(self, session, thread_id: str) -> None:
        await turn_finalizer.drop_last_assistant(session, thread_id)

    async def _persist_partial_assistant(
        self, thread_id: str, content: str, run_id: Optional[str] = None,
    ) -> Optional[int]:
        return await turn_finalizer.persist_partial_assistant(thread_id, content, run_id=run_id)

    async def _recent_history(self, thread_id: str, limit: int = 6) -> List[Dict[str, str]]:
        return await turn_finalizer.recent_history(thread_id, limit)

    async def _generate_title(
        self, thread_id: str, first_message: str, model: str, api_key: str,
        attachments: Optional[List[Any]] = None,
    ) -> None:
        await turn_finalizer.generate_title(thread_id, first_message, model, api_key, attachments=attachments)


    async def stream_chat(
        self,
        user_id: str,
        message: str,
        thread_id: Optional[str] = None,
        model: Optional[str] = None,
        skill_ids: Optional[List[str]] = None,
        selected_skills: Optional[List[Any]] = None,
        knowledge_ids: Optional[List[str]] = None,
        selected_knowledge: Optional[List[Any]] = None,
        user_context=None,
        newapi_key: Optional[str] = None,
        resolved_model: Optional[str] = None,
        regenerate: bool = False,
        subagent_id: Optional[str] = None,
        token: str = "",
        web_search: bool = False,
        agent_mode: str = "standard",
        assistant_preset: str = "",
        assistant_preset_snapshot: Optional[Dict[str, Any]] = None,
        interview_input: Optional[Dict[str, Any]] = None,
        attachments: Optional[List[Any]] = None,
        truncate_from_message_id: Optional[int] = None,
        queue_item_id: Optional[str] = None,
        queue_lease_token: Optional[str] = None,
        protocol: str = sse_protocol.HARNESS,
        run_id: Optional[str] = None,
        client_request_id: Optional[str] = None,
        precreated_run: bool = False,
        precreated_user_message_id: Optional[int] = None,
        initial_sequence: int = 0,
        execution_profile: Optional[Dict[str, Any]] = None,
        resume_source_run_id: str = "",
        previous_model: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        if not newapi_key or not resolved_model:
            newapi_key, resolved_model = await self.prepare_chat(user_id, model)
        from app.services.chat.builtin_assistants.registry import is_campus_preset, origin_for_preset
        from app.services.chat.builtin_assistants.runtime import get_builtin_runtime_policy
        from app.services.chat.builtin_assistants.presentation.policy import (
            PRESENTATION_SKILL_CANONICAL_ID,
            is_presentation_preset,
        )
        thread_id = await self._ensure_thread(
            thread_id,
            user_id,
            origin=origin_for_preset(assistant_preset) or None,
        )
        if not assistant_preset:
            thread_settings = await self.get_thread_model_setting(user_id, thread_id)
            assistant_preset = str(thread_settings.get("assistant_preset") or "")
        presentation_mode = is_presentation_preset(assistant_preset)
        campus_mode = is_campus_preset(assistant_preset)
        runtime_policy = get_builtin_runtime_policy(assistant_preset)
        if campus_mode:
            from app.services.chat.builtin_assistants.campus_services.runtime_service import snapshot_from_state
            snapshot = snapshot_from_state(assistant_preset_snapshot)
            if snapshot is None and run_id:
                try:
                    from app.services.agent_harness import run_store as _campus_run_store
                    packed = await _campus_run_store.get_run_state(str(run_id))
                    snapshot = snapshot_from_state(
                        ((packed or {}).get("state") or {}).get("assistant_preset_snapshot")
                    )
                    if snapshot:
                        assistant_preset_snapshot = snapshot
                except Exception:  # noqa: BLE001
                    snapshot = None
            if snapshot:
                knowledge_ids = list(snapshot.get("knowledge_ids") or [])
                selected_knowledge = []
                resolved_model = str(snapshot.get("model_id") or resolved_model or "")
                agent_mode = "standard"
            skill_ids = []
            selected_skills = []
            subagent_id = None
            # 上传附件已在受理入口校验；预设只收敛工具权限，不截断共享视觉处理链。
        if presentation_mode:
            subagent_id = None
            skill_ids = [PRESENTATION_SKILL_CANONICAL_ID]
            selected_skills = [{
                "id": PRESENTATION_SKILL_CANONICAL_ID,
                "name": PRESENTATION_SKILL_CANONICAL_ID,
                "source": "system",
            }]
        loop_resume_messages = None
        loop_resume_world_state: dict[str, Any] = {}
        loop_resume_keep_plan = False
        if precreated_run and run_id:
            try:
                from app.services.agent_harness import run_store as _ckpt_store
                _packed = await _ckpt_store.get_run_state(str(run_id))
                _st = ((_packed or {}).get("state") or {})
                if str(_st.get("interactive_type") or "") == "task_recovery":
                    _ckpt_msgs = _ckpt_store.loop_checkpoint_messages(_st)
                    if _ckpt_msgs:
                        loop_resume_messages = _ckpt_store.with_recovery_observation(_ckpt_msgs)
                        loop_resume_world_state = dict(
                            ((_st.get("loop_checkpoint") or {}).get("world_state") or {})
                        )
                        agent_mode = str(_st.get("agent_mode") or agent_mode or "standard")
                        loop_resume_keep_plan = str(_st.get("capability_scope") or "") == "planning"
            except Exception:  # noqa: BLE001
                loop_resume_messages = None
                loop_resume_world_state = {}
                loop_resume_keep_plan = False
        # 当前上传与后续“这份文件”必须沿用同一 file_id。没有本轮附件时，只在消息明显
        # 指向已有文件/改动时取本会话最近附件；普通寒暄不会被旧附件误路由成文件任务。
        current_file_targets = [
            {
                "file_id": str(_att_field(att, "file_id") or ""),
                "filename": str(_att_field(att, "filename") or ""),
                "source": "current_attachment",
            }
            for att in (attachments or [])
            if _att_field(att, "file_id") and not _attachment_is_reference_only(message, att)
        ]
        logger.info(
            "attachment.routing total=%s revision_targets=%s reference_only=%s names=%s",
            len(attachments or []),
            len(current_file_targets),
            [
                _attachment_is_reference_only(message, att)
                for att in (attachments or [])
            ],
            [str(_att_field(att, "filename") or "") for att in (attachments or [])],
        )
        prior_file_targets: list[dict] = []
        # 取「会话里最近的附件」当修订目标，必须要求消息真的**指向先前那个东西**
        # （2026-07-29 深扫 P0，数据破坏级）：原判据含裸词「表格|文档|报告|改|换|颜色」，
        # 于是"帮我写一份整改报告"在有过任意附件的会话里就会把**那个无关附件**取来当目标，
        # has_selected_files 随之为真 → 判定层 existing_target 恒真 → 提示词命令模型
        # 「同 file_id 写回」→ 用户的既有文件被无关内容覆盖，且现实校验被一并短路。
        # 改成只认回指证据（"刚才那份"/"这个文件"/"第 3 页"）与点名文件；类名词一律不算。
        references_file_context = bool(
            anaphoric_target_reference(message)
            or _named_files_in_message(message)
        )
        if not current_file_targets and references_file_context:
            prior_file_targets = await thread_attachment_service.recent_file_targets(
                thread_id=thread_id, user_id=user_id, limit=3,
            )
        contextual_file_targets = current_file_targets or prior_file_targets
        has_selected_files = bool(contextual_file_targets)
        # Profile 只选择策略和初始阶段，三种模式共用这条执行路径。
        turn_decision = _decide_turn_with_attachment_intent(
            message, attachments, active_run=False,
        )
        profile_mode = str(agent_mode or "standard").strip().lower()
        from app.services.agent_harness.profiles import get_profile
        try:
            profile = get_profile(profile_mode)
        except ValueError:
            raise HTTPException(status_code=422, detail="agent_mode 不合法")
        harness_enforced = True
        is_plan_profile = profile.requires_initial_plan_confirmation
        is_research_profile = profile.auto_research_plan
        auto_plan_profile = False
        # 2026-07-27 用户拍板：**不要自动路由**。
        #
        # 原先这里跑 `router.decide()` —— 按复杂度猜：看到「做一份 PPT」就替用户决定走任务
        # 模式，代价是凭空多一轮需求澄清 + 一份没人要的计划报告 + 一次等待确认（前摇几十秒）。
        # 用户要的是 PPT，不是一份关于怎么做 PPT 的报告。
        #
        # 现在只剩两条入口：① 用户在 composer 的 + 菜单里点开「计划模式」（is_plan_profile）；
        # ② 用户**话里明确说**要先出计划（wants_plan_mode，判据刻意收得很窄）。
        # 「听话」保留，「替用户做决定」去掉。
        # 开关开着 ≠ 每句话都要一份计划报告（2026-07-28）。计划模式的正常终点是
        # waiting_user（挂在确认卡上），前端 settleTaskMode 只在**真终态**才拨回开关，
        # 所以开关会一直亮着。此时用户打「取消」「继续」这类控制语，decide_turn 会判成
        # authority="none"（不是要干活），却仍被 is_plan_profile 无条件改写成
        # 「请给我一份完整的计划报告」——用户只是想停下来，却又收到一份计划。
        # auto 那条路本来就有 authority 守卫（见上面的 if），显式开关这条补齐同一道。
        # 判据从 authority 换成 bare_control_message（2026-07-29）：authority 回答的是
        # "这轮给不给写工具"，不该兼职回答"这是不是控制语"。「继续」在无进行中 Run 时
        # 需要写权限（扣押轮的出口），一旦它变成 mutate，靠 authority 认控制语的这道守卫
        # 就静默失效——用户打一句「继续」会收到一份完整计划报告。
        if loop_resume_messages and not loop_resume_keep_plan:
            is_plan_profile = False
        if is_plan_profile and (
            bare_control_message(message)
            or turn_decision.authority not in ("inspect", "mutate")
        ):
            logger.info(
                "计划模式开关开着，但本轮是控制类消息（authority=%s）：按普通轮处理，不出计划报告",
                turn_decision.authority)
            is_plan_profile = False
        if is_plan_profile:
            from app.services.tasks.plan_service import choice_approves_plan as _approves_existing_plan
            if _approves_existing_plan(message):
                logger.info("计划模式开关开着，但用户要求执行已有计划：按执行轮处理，不再出新计划")
                is_plan_profile = False
        wants_plan = is_plan_profile or auto_plan_profile

        # 计划模式**走主循环，不进 graph**（2026-07-27 用户拍板「现在就不需要有 dag 了」/
        # 「跑最原始的 loop 已经够用了」）。
        #
        # 做法：把授权压成 inspect —— Harness 的铁律是「工具集合本身即授权边界」，所以
        # build_tools 这一轮只给只读工具，模型**物理上**改不了任何东西，不靠 prompt 求它别动手。
        # 它用只读工具勘查完，把计划报告作为这一轮的**回答**流式吐出来；用户确认后的下一轮
        # 才拿到写工具。
        #
        # 顺带解决了「路由到任务模式思考太久」：graph 路径要先阻塞跑完 investigate（≤4 轮）
        # + plan_graph（1~2 轮）才出第一个字，最坏 95s 全静默；计划变成正文之后第一个 token
        # 立刻就到。也顺带消灭了「整图被拒 → 规划未通过校验 → 回退普通执行」这一整类失败
        # ——模型写不出"不合法的散文"。
        # 2026-07-27：graph runtime 已删除，计划模式**只有**走主循环这一条路。
        # 原先这里按 retired graph switch 在「主循环」与「graph 执行」之间二选一。
        plan_in_main_loop = wants_plan and not is_research_profile
        if is_research_profile:
            # 研究与计划模式互斥。Research 是唯一允许走第二内核的 Profile：
            # 先 seed_goal_contract，再由 research.kernel 强制分主题检索/深读，
            # 合成仍复用 model_driver / 同一套工具与 SSE。Standard/Plan 不得进这条路。
            research_authority = "inspect" if turn_decision.authority == "inspect" else "mutate"
            turn_decision = replace(
                turn_decision,
                authority=research_authority,
                plan_mode=False,
                research_profile=True,
            )
            web_search = True
            try:
                from app.services.agent_harness.research.engine import seed_research_state
                await seed_research_state(run_id, message)
            except Exception:  # noqa: BLE001
                pass
        elif plan_in_main_loop:
            turn_decision = replace(turn_decision, authority="inspect", plan_mode=True)
        # Deep Research 是用户显式选中的产品功能，不应随 Harness 的
        # 部署开关一起失效：即使旧环境关闭 Harness，仍要注入研究约束并
        # 保留用户显式的只读边界。
        decision_enforced = True
        # 计划模式的唯一事实源（turn_decision.plan_mode 只在上面那个 elif 里置位）。
        # 不随 decision_enforced 兜底：它是用户显式选中的产品功能，不该跟着 Harness 开关
        # 一起失效——同 Deep Research 的口径。下游只有一个消费者：绝不静默降级的守卫。
        plan_mode = bool(turn_decision.plan_mode)
        # 而**边界与提示词也必须跟着 plan_mode 走**（2026-07-28 修）：此前
        # action_authority / turn_guard_prompt 只看 decision_enforced，于是
        # retired harness switch 不是 "on" 时，用户显式点开的计划模式既拿不到 inspect 只读边界
        # （build_tools 照给写工具，模型物理上就能动手），也拿不到计划提示词——直接按普通
        # 执行轮跑掉，用户点了个没有任何效果的开关。默认是 "on" 所以当前不发作，但这正是
        # 「改一个开关就静默破功」的耦合。口径与 is_research_profile 完全一致。
        # 用户显式的「只读 / 不要改任何文件」同样不能随部署开关失效（2026-07-29 修）。
        # 它是**用户本人的要求**，不是 Harness 的一项特性，而原先这里只看 decision_enforced：
        # retired harness switch 不是 "on" 且非计划/研究轮时，decide_turn 正确判出的
        # authority="inspect" 被下面那句 `else "mutate"` 无条件改写回去，turn_guard_prompt
        # 也是空串 —— 写工具全在手，用户那句「不要修改任何文件」在物理边界上一点痕迹都没留。
        # 这比没有这个功能更糟：用户以为自己把门关上了。同源案例见上面 plan_mode 那段注释。
        # 判据复用 turn_decision 的同一对正则（write_denied_request），不另立第三份词表。
        explicit_write_denied = write_denied_request(message)
        decision_applied = decision_enforced or plan_mode or explicit_write_denied
        from app.services.files import work_folders
        selected_work_folder = await work_folders.folder_for_thread(user_id, thread_id)
        has_explicit_resources = bool(
            skill_ids or knowledge_ids or selected_knowledge or attachments or web_search or selected_work_folder
        )
        turn_route = turn_decision.route(
            has_explicit_resources=has_explicit_resources, message=message,
        )
        if plan_mode:
            turn_route = "planning"
        elif is_research_profile:
            turn_route = "agent"
        elif runtime_policy:
            turn_route = "agent"
        logger.info(
            "harness.turn_decision intent=%s authority=%s revision=%s allow_create=%s "
            "explicit_task=%s explicit_research=%s auto_task=%s mode=%s",
            turn_decision.intent,
            turn_decision.authority,
            turn_decision.revision,
            turn_decision.allow_create,
            is_plan_profile,
            is_research_profile,
            auto_plan_profile,
            profile_mode,
        )

        revision_target: Optional[dict] = None
        revision_file_candidates: list = []  # multi-artifact disambiguation
        # 守卫用 decision_enforced 而不是 harness_enforced（2026-07-29 修）：772 行专门引入
        # decision_enforced 就是为了「Deep Research 不随部署开关失效」，这一整块却漏了它。
        # 后果是一个自相矛盾的状态：开关 off + 显式开深度研究 + 修订形态消息时，TurnEnv 里
        # revision_mode=True / allow_create=False（那两行取的是 decision_enforced，为真，
        # 写工具照样被摘），但解析与下面的现实校验整段不跑 → revision_target 恒 None →
        # 必然卡在「目标未解析」那条扣押路径上，且连"降级为新建"的出口都走不到。
        if decision_enforced and turn_decision.revision and not turn_decision.allow_create:
            if contextual_file_targets:
                # 名字匹配闸（2026-07-29 对抗审计 P0）：消息**点名了文件**时，拿来顶上的
                # 附件必须名字对得上。此前这条分支无条件取 contextual_file_targets[0]，
                # 而 recent_file_targets 是按时间取的（docstring 自己写着"不按文件名回查"）
                # ——于是「帮我更新 summary.json 里的字段」会把会话里最近那个无关附件
                # （如「2026年度财务预算.xlsx」）当成修订目标，提示词命令模型"同 file_id 写回"，
                # 用户的既有文件被静默覆盖。**这正是本文件上方注释声称已修掉的附件劫持形态：
                # 我堵了裸词那个入口，又用 named_files 新开了一个。**
                # 用户在 composer 里勾选（current_file_targets）是显式选择，不受此闸约束。
                _named = _named_files_in_message(message)
                if _named and not current_file_targets:
                    _lowered = {n.strip().lower() for n in _named}
                    _matched = [
                        t for t in contextual_file_targets
                        if str(t.get("filename") or "").strip().lower() in _lowered
                    ]
                    if _matched:
                        revision_target = _matched[0]
                    else:
                        logger.info(
                            "点名的文件 %s 与会话最近附件都对不上，不拿附件顶替修订目标", _named)
                else:
                    revision_target = contextual_file_targets[0]
            # 与判定层共用同一条判据（2026-07-29 修）：原先这里是一份**独立维护的窄词表**
            # （只有刚才/刚刚/之前/上次/原文件/这份/那个文件/该文件 + PPT/报告/文档/表格），
            # 而 turn_decision 侧的 _EXISTING_TARGET_RE / anaphoric_target_reference 还收了
            # 「原来|已有|现有|这个|这篇|那份|上面那|你之前做的|第N页/行/段」和点名文件名。
            # 单向漂移的形态是最难发现的一类：turn_decision 已判 revise 并扣了写工具，
            # 这里却因为词表窄而根本不去解析目标 → revision_target 恒 None → 本可自动选中的
            # 修订被硬生生打断成一张确认卡（实测「把原来的方案优化一下」在只有一份产物的
            # 会话里就是这样被打断的）。
            # references_file_context 就是 700 行算好的那条证据（回指 or 点名文件），
            # 与 revision_lock_justified 的取证口径同源；类名词一律不再当证据——没有证据时
            # 下面的现实校验会把这一轮降级成新建，本来也不该去解析什么目标。
            if revision_target is None and references_file_context:
                try:
                    from app.services.files import user_file_service
                    generated = (
                        await work_folders.list_folder_files(user_id, selected_work_folder["id"])
                        if selected_work_folder else
                        await user_file_service.list_generated_files(user_id, thread_id, limit=3)
                    )
                    if generated:
                        # 多产物会话不能因为用户泛称“报告/文档”就盲改最新文件。优先按
                        # 文件名（含去扩展名）唯一命中；只有明确说“刚才/最新/最后那个”
                        # 或当前会话确实只有一个产物时，才安全选择最新产物。否则保持
                        # revision_target=None，让工具层只读并请用户点选目标。
                        filename_matches = []
                        message_lower = message.lower()
                        for item in generated:
                            filename = str(item.get("filename") or "").strip()
                            stem = filename.rsplit(".", 1)[0].strip()
                            if (
                                filename and filename.lower() in message_lower
                            ) or (
                                len(stem) >= 2 and stem.lower() in message_lower
                            ):
                                filename_matches.append(item)
                        explicit_latest = any(
                            hint in message
                            for hint in ("刚才", "刚刚", "最新", "最后那个", "上一个")
                        )
                        chosen = (
                            filename_matches[0]
                            if len(filename_matches) == 1
                            else generated[0]
                            if len(generated) == 1 or explicit_latest
                            else None
                        )
                        if selected_work_folder:
                            chosen = work_folders.choose_revision_file(generated, message)
                        if chosen is None and len(generated) >= 2:
                            revision_file_candidates = [
                                {
                                    "file_id": str(item.get("id") or ""),
                                    "filename": str(item.get("filename") or ""),
                                }
                                for item in generated
                                if str(item.get("filename") or "").strip()
                            ][:8]
                        if chosen:
                            revision_target = {
                                "file_id": str(chosen.get("id") or ""),
                                "filename": str(chosen.get("filename") or ""),
                                "source": (
                                    "filename_match"
                                    if len(filename_matches) == 1
                                    else "latest_thread_artifact"
                                ),
                                "created_at": chosen.get("createdAt"),
                            }
                except Exception as revision_target_error:  # noqa: BLE001
                    logger.warning(
                        "RevisionTarget 解析失败（模型仍可 glob/read_file 定位）: %s",
                        revision_target_error,
                    )
            if revision_target and revision_target.get("file_id"):
                try:
                    from app.services.files import user_file_service
                    revision_target.update(
                        await user_file_service.get_revision_target_snapshot(
                            user_id,
                            str(revision_target["file_id"]),
                        )
                    )
                    original_run_id = str(
                        revision_target.get("original_run_id") or ""
                    )
                    if original_run_id:
                        async with async_session() as revision_session:
                            source_message_id = (
                                await revision_session.execute(
                                    select(ChatMessage.id)
                                    .where(
                                        ChatMessage.thread_id == thread_id,
                                        ChatMessage.role == "assistant",
                                        ChatMessage.run_id == original_run_id,
                                    )
                                    .order_by(ChatMessage.id.desc())
                                    .limit(1)
                                )
                            ).scalar_one_or_none()
                        revision_target["source_message_id"] = source_message_id
                except Exception as snapshot_error:  # noqa: BLE001
                    # 解析到的目标若已不存在/越权，不能把它继续作为写入授权。清空后工具层
                    # 只保留 list/read，模型会要求用户重新选择目标。
                    logger.warning("RevisionTarget 快照失败，撤销写入目标: %s", snapshot_error)
                    revision_target = None
            if revision_target is None:
                # 现实校验（2026-07-29 深扫后换判据）：目标一个都没解析出来时，
                # 「禁止新建」这条硬约束必须有**指代既有产物的证据**才站得住——
                # 勾了文件 / 明确回指（"刚才那份"、"原文件"、"第 3 页"）/ 点名了一个
                # 真实存在的文件。三者都没有 ⇒ 判定是词表误判的产物（"帮我写一份整改
                # 报告"这类纯新建请求实测 12/15 会被判成修订），按新建放行写工具。
                #
                # 为什么判据必须换：此前四次修补都在给词表加负向排除（"建议""我的文件"
                # "换路"…），而根因是 _EXISTING_TARGET_RE 收录了「文档|报告|表格」这些
                # **新交付物的类名**，补丁补不完。扣押写工具的代价已被真机证明极高
                # （11 分钟任务失败、模型绕路吐错格式还宣称完成），所以这条硬约束从
                # "词表说是修订就扣" 改成 "有证据才扣"。
                # 证据成立而目标仍解析不到（如会话里多份同类产物、用户说"那份报告"）
                # 才保留扣押 + 确认卡链路——那正是它该在的场景。
                try:
                    from app.services.chat.turn_decision import (
                        named_files,
                        revision_lock_justified,
                    )
                    from app.services.files import user_file_service
                    _existing_names: set = set()
                    # 只有「消息点名了文件、且没有更便宜的证据」时才需要问文件区：
                    # 勾选/回指两条证据是纯文本判断，命中就不必查库。
                    # 查库带 3s 超时——它跑在首个 SSE 帧之前，不能让一次慢查询变成静默卡顿。
                    #
                    # 走 existing_filenames（**纯只读、按名字定向**）而不是
                    # list_files(user_id, "__all__")：后者第一件事是 _purge_expired，
                    # 真删磁盘文件 + DELETE 行——"只读取证"带破坏性副作用，且被上面这个
                    # 3s 超时取消时 unlink 已发生而事务回滚，会留下幽灵行
                    # （2026-07-29 对抗审计）。取证只需要名字，不需要 stat 每个文件。
                    _named_in_msg = named_files(message)
                    if (not has_selected_files
                            and not anaphoric_target_reference(message)
                            and _named_in_msg):
                        if selected_work_folder:
                            folder_rows = await work_folders.list_folder_files(user_id, selected_work_folder["id"])
                            _existing_names = {str(row["filename"]) for row in folder_rows}
                        else:
                            _existing_names = await asyncio.wait_for(
                                user_file_service.existing_filenames(user_id, _named_in_msg),
                                timeout=3.0,
                            )
                    lock_ok = revision_lock_justified(
                        message,
                        has_selected_files=has_selected_files,
                        existing_names=_existing_names,
                    )
                except Exception as reality_error:  # noqa: BLE001
                    # 校验本身失败/超时：按**放行**处理。扣押是高代价动作，不能建立在
                    # 一次失败的查询上（原实现失败时保持扣押，等于让 DB 抖动去掐掉用户任务）。
                    logger.warning("Revision 证据校验失败（按新建放行）: %s", reality_error)
                    lock_ok = has_selected_files
                if not lock_ok:
                    logger.info(
                        "Revision 无指代证据（未勾文件/无回指/未点名已存在文件），"
                        "修订判定降级为新建（原 reason=%s）", turn_decision.reason_code)
                    turn_decision = replace(
                        turn_decision, revision=False, allow_create=True,
                        reason_code=f"{turn_decision.reason_code}+no_target_evidence",
                    )

        # 队列派发轮（§10.6 P0 端到端不丢）：建 Run 之前先原子绑定租约——status=dispatching
        # + lease_token + user_id 全匹配才继续；旧租约（已回收重派）/重复请求一律在此拒绝，
        # 消息不会被重复写进会话。绑定成功后无论后续哪一环断掉，回收逻辑都能依据
        # dispatched_run_id 判定「Run 已建→删除不重派 / Run 未建→回收重派」。
        run_id = run_id or uuid.uuid4().hex
        is_queue_dispatch = bool(queue_item_id and queue_lease_token)
        if is_queue_dispatch:
            from app.services.tasks import thread_queue_service
            bound = await thread_queue_service.bind_dispatch(
                item_id=queue_item_id, lease_token=queue_lease_token or "",
                user_id=user_id, run_id=run_id,
            )
            if not bound:
                channel0 = sse_protocol.SSEChannel(protocol, thread_id, run_id)
                yield channel0.run_failed("该排队消息已在其他窗口派发或已被回收，本次不再重复发送。")
                yield channel0.done()
                return

        # R0 活动任务优先（§7.1/§8.1/不变量 1）：Thread 有挂起（waiting_*）的 Task Run 时，
        # 聊天框新消息不重新做意图识别、不新建 run——否则"请假好了嘛"会被再路由成新请假任务。
        # 表单/选项的补全走上方卡片（/chat/resume）；此处只处理"取消"或提示先完成。
        # 显式 subagent_id 也不豁免（2026-07-14 二轮评审）：豁免会允许在普通任务仍在跑时
        # 提交带 subagent_id 的请求、同一 Thread 并发第二个 Run 写线性历史。消歧卡点选
        # 重发本就不需要绕行——waiting_clarification 不在 get_active_run 的活动集合里。
        if not precreated_run and not regenerate and user_context:
            active = await self._resolve_active_run(thread_id, user_id)
            if active and is_queue_dispatch:
                # 队列派发轮撞上活动 Run（pop 与请求到达之间新起了 Run 的窄窗口）：不把排队
                # 消息当 resume 输入、也不落任何消息——立即释放租约放回队列（顺序保留），
                # 下次终态再自动派发。不丢、不重。
                from app.services.tasks import thread_queue_service
                await thread_queue_service.release_dispatch(
                    item_id=queue_item_id or "", run_id=run_id)
                channel0 = sse_protocol.SSEChannel(protocol, thread_id, run_id)
                yield channel0.run_failed("当前会话已有任务在进行，排队消息将在其结束后自动发送。")
                yield channel0.done()
                return
            if active and active.get("status") == "running":
                # 拒绝提示用本轮临时 run_id、不发 run.started（P0 2026-07-17，对齐下方
                # ActiveRunConflict 分支）：若用 active["id"] 发 run.failed，前端会把真实
                # 运行中的 Run 标失败并清活动记录。v1 run.failed 信封自带 thread/run id，
                # 前端只标红本轮气泡、不触碰 activeRuns。
                notice0 = sse_protocol.SSEChannel(protocol, thread_id, run_id)
                yield notice0.run_failed("当前会话仍在生成，请等待完成或先停止当前任务。")
                yield notice0.done()
                return
            if active and active.get("status") in task_run_service.WAITING_RUN_STATUSES:
                run_full = await task_run_service.get_run(active["id"], user_id)
                astate = (run_full or {}).get("state") or {}
                if True:
                    # ask_user_choice 消歧挂起 + 用户直接打字：输入本身就是对澄清问题的自由回答
                    # （选项只是快捷方式），转交 resume 续接同一循环；「取消」意图仍优先放弃任务。
                    # 用户消息由 resume 前置持久化（下方 s0 只服务提示/取消分支）。
                    if not _is_cancel_intent(message) and run_full and astate.get("ask_user"):
                        async with async_session() as s0:
                            s0.add(ChatMessage(
                                thread_id=thread_id,
                                role="user",
                                content=message,
                                run_id=active["id"],
                            ))
                            await s0.commit()
                        async for payload in self.resume_chat(
                            user_id=user_id, user_context=user_context, run_id=active["id"],
                            resume_value=message,
                            resume_id=active.get("resume_token"),
                            token=token, protocol=protocol,
                        ):
                            yield payload
                        return
                    async with async_session() as s0:
                        s0.add(ChatMessage(
                            thread_id=thread_id,
                            role="user",
                            content=message,
                            run_id=active["id"],
                        ))
                        await s0.commit()
                    if _is_cancel_intent(message):
                        note = "已取消当前进行中的任务。有什么可以帮你的？"
                        last_sequence = await task_run_service.get_last_event_sequence(
                            active["id"], user_id,
                        )
                        orig = sse_protocol.SSEChannel(
                            protocol,
                            thread_id,
                            active["id"],
                            start_sequence=last_sequence,
                        )
                        async with async_session() as s1:
                            row0 = ChatMessage(thread_id=thread_id, role="assistant", content=note,
                                               status="completed")
                            s1.add(row0)
                            await s1.commit()
                            mid0 = row0.id
                        yield orig.message_delta(note)
                        yield orig.message_completed(note, mid0)
                        from app.services.chat.turn_finalizer import finalize_terminal
                        async for terminal_frame in finalize_terminal(
                            orig,
                            active["id"],
                            {"run_disposition": "cancelled"},
                            None,
                            note,
                            note,
                        ):
                            yield terminal_frame
                        yield orig.done()
                        return
                    # 提示分支（P0 2026-07-17）：**绝不能完成原等待 Run**——此前对
                    # active["id"] 发 run.completed，前端误清活动任务/关任务模式/派发队列，
                    # 后端 Run 却仍在 waiting_*。改用本轮临时 run_id 的独立通道且不发
                    # run.started（不注册活动 Run），原 Run 状态原样保留。
                    note = "当前 Run 正在等待输入，请在当前交互卡中提交，或取消当前 Run。"
                    async with async_session() as s1:
                        row0 = ChatMessage(thread_id=thread_id, role="assistant", content=note,
                                           status="completed")
                        s1.add(row0)
                        await s1.commit()
                        mid0 = row0.id
                    notice = sse_protocol.SSEChannel(protocol, thread_id, run_id)
                    yield notice.message_delta(note)
                    yield notice.message_completed(note, mid0)
                    yield notice.run_failed(note)
                    yield notice.done()
                    return

        # run.started 尽早发（体验修复 2026-07-13）：执行卡的显示依赖前端收到 run.started 落 runStartedAt；
        # 而其后的会话附件重检索 / 自动路由 / 智能体检索 / 记忆召回 / 同步压缩都可能各花数秒——期间前端
        # 只有一行「正在思考」裸块、看不到执行卡与步骤。故在所有重活之前先把 run.started 打出去，让执行卡
        # 立即出现并进入「正在思考」态，随后的准备/工具步骤再逐一填进去。
        run_id = run_id or uuid.uuid4().hex
        channel = sse_protocol.SSEChannel(
            protocol, thread_id, run_id, start_sequence=initial_sequence,
        )

        # Dynamic use_skill facts belong to the Run, not to a worker/process closure.  For an
        # explicit "continue" load the single finalized source Run; for a worker retry load the
        # same Run.  IDs are only candidates here—turn preparation still rechecks auth-api ACL before
        # injecting any Skill instructions or package.
        _recovery_skill_state: list[dict] = []
        _explicit_skill_ids = [
            str(item or "").strip() for item in (skill_ids or []) if str(item or "").strip()
        ]
        try:
            from app.services.chat.turn_context_builder import (
                get_persisted_skill_state,
                skill_ids_for_recovery,
            )
            _skill_state_source_run = str(resume_source_run_id or run_id or "").strip()
            _recovery_skill_state = await get_persisted_skill_state(_skill_state_source_run)
            _recovery_ids = skill_ids_for_recovery(_recovery_skill_state)
            skill_ids = list(dict.fromkeys(_explicit_skill_ids + _recovery_ids))
        except Exception:  # noqa: BLE001
            # A missing state is not permission to invent a Skill; keep only the explicit IDs.
            skill_ids = list(dict.fromkeys(_explicit_skill_ids))

        # Run 记录在重活之前落库（2026-07-14），身兼两职：
        #  ① 防切走丢恢复入口——原先排在附件/路由/记忆/压缩之后，任务头几秒 PG 查不到 Run，
        #     用户此时切走再切回会误清本地运行态，后台仍在跑的任务失去恢复入口；
        #  ② R0 原子占位（三轮评审）——agent_runs 有活动态部分唯一索引，同一 Thread 已有活动
        #     Run 时这里冲突即 ActiveRunConflict，堵住 get_active_run 预检与本 INSERT 之间的
        #     TOCTOU 竞态。regenerate 与显式 subagent_id 都经此占位，无豁免；waiting_clarification
        #     不在活动集，消歧点选重发不受阻。放在 yield thread() 之前，冲突时不发多余的 run.started。
        # kind/subagent_id 先按主对话占位，自动路由命中后由 set_run_route 补写。
        # PG 未配置时 create_run 返回 None（无原子保护，退回仅预检）——「不阻断对话」优先。
        # regenerate 跳过了上面的 R0 预检，这里补一次僵尸清理：进程重启遗留的 stale running
        # 行会占着唯一索引，若不先清，合法的 regenerate 会被原子占位误判为"仍在生成"。
        if regenerate and not precreated_run:
            await self._resolve_active_run(thread_id, user_id)
        try:
            if precreated_run:
                created_run_id = run_id
            else:
                created_run_id = await task_run_service.create_run(
                    run_id=run_id, thread_id=thread_id, user_id=user_id,
                    kind="chat", model=resolved_model, goal=(message or "")[:200],
                    agent_mode=profile_mode,
                    client_request_id=client_request_id,
                )
            if created_run_id:
                try:
                    from app.services.agent_harness import run_store
                    await run_store.initialize_run_state(
                        run_id,
                        agent_mode=profile.mode.value,
                        decision_route=turn_route,
                        capability_scope=(
                            "default" if runtime_policy and runtime_policy.action_authority == "mutate"
                            else "planning" if plan_mode
                            else "inspect" if turn_decision.authority == "inspect"
                            else "revision_pending" if (
                                turn_decision.revision and not turn_decision.allow_create
                                and not revision_target
                            )
                            else "revision" if (
                                turn_decision.revision and not turn_decision.allow_create
                            )
                            else "default"
                        ),
                    )
                    await task_run_service.save_run_state(run_id, {
                        **({"skill_ids": list(skill_ids or [])} if (skill_ids or not resume_source_run_id) else {}),
                        "decision_route": turn_route,
                        "capability_scope": (
                            "default" if runtime_policy and runtime_policy.action_authority == "mutate"
                            else "planning" if plan_mode
                            else "inspect" if turn_decision.authority == "inspect"
                            else "revision_pending" if (
                                turn_decision.revision and not turn_decision.allow_create
                                and not revision_target
                            )
                            else "revision" if (
                                turn_decision.revision and not turn_decision.allow_create
                            )
                            else "default"
                        ),
                        "turn_decision": {
                            "intent": turn_decision.intent,
                            "authority": turn_decision.authority,
                            "reason_code": turn_decision.reason_code,
                            "revision": turn_decision.revision,
                            "allow_create": turn_decision.allow_create,
                            "auto_task": auto_plan_profile,
                            "research_profile": is_research_profile,
                        },
                        "revision_target": revision_target,
                        "parent_run_id": (
                            (revision_target or {}).get("original_run_id")
                            if revision_target else None
                        ),
                        "target_snapshot": revision_target,
                        "latest_requirements_version": 1,
                    })
                    try:
                        from app.services.agent_harness.goal_contract import seed_goal_contract_for_resume
                        _contract = await seed_goal_contract_for_resume(
                            message or "", turn_decision, route=turn_route,
                            resume_source_run_id=resume_source_run_id,
                        )
                        await task_run_service.save_run_state(
                            run_id, {"goal_contract": _contract.to_state()},
                        )
                    except Exception:  # noqa: BLE001
                        logger.debug("goal_contract 持久化失败（不阻断）run=%s", run_id, exc_info=True)
                except Exception as decision_state_error:  # noqa: BLE001
                    logger.warning("TurnDecision 状态记录失败（不阻断执行）: %s", decision_state_error)
            # Runtime 已配置却写入失败时不能继续执行：否则幂等握手/活动 Run/恢复入口全失效；
            # 队列项还可能在后面的 finish_dispatch 被删除，形成“消息消失但 Run 无记录”。
            from app.core.runtime_db import runtime_enabled
            if runtime_enabled() and not created_run_id:
                if is_queue_dispatch:
                    from app.services.tasks import thread_queue_service
                    await thread_queue_service.release_dispatch(
                        item_id=queue_item_id or "", run_id=run_id)
                yield channel.run_failed("运行状态存储暂时不可用，本次未开始执行，请稍后重试。")
                yield channel.done()
                return
        except task_run_service.DuplicateRequestConflict as dup:
            # 幂等键撞赢家（N-02 TOCTOU 兜底）：本次是同一发送的并发重复请求，赢家 Run 已在
            # 执行。静默收尾、不发 run.failed（会污染赢家轮的视觉/前端 activeRuns）；
            # 客户端凭 GET /chat/requests/{client_request_id}/run 发现赢家后订阅续接。
            logger.info("重复请求撞幂等键，静默退出: run=%s winner=%s", run_id, dup.run_id)
            yield channel.done()
            return
        except task_run_service.ActiveRunConflict:
            # 冲突＝该 Thread 已有真实活动 Run（本次是并发重复请求）。**不**发 run.started：
            # 否则前端会把这个被拒绝的新 run_id 注册成该线程的活动 Run，覆盖真实运行 Run 的本地
            # 记录，随后 run.failed 收尾又把它清掉——真实 Run 的「运行中」态与订阅被一并抹掉。
            # v1 run.failed 信封自带 thread_id/run_id，前端只标红本轮气泡、不触碰 activeRuns。
            if is_queue_dispatch:
                # 队列派发轮被原子占位拒绝：Run 未建成，立即释放租约放回队列（不等 90s 回收）
                from app.services.tasks import thread_queue_service
                await thread_queue_service.release_dispatch(
                    item_id=queue_item_id or "", run_id=run_id)
            yield channel.run_failed("当前会话仍在生成，请等待完成或先停止当前任务。")
            yield channel.done()
            return

        # 队列派发轮：Run 已成功持久化——服务端原子确认删除队列项（§10.6 P0，取代前端提前
        # confirm）。此调用即使失败也无害：回收逻辑看到 dispatched_run_id 对应的 Run 已存在，
        # 同样删除不重派。
        if is_queue_dispatch:
            from app.services.tasks import thread_queue_service
            await thread_queue_service.finish_dispatch(
                item_id=queue_item_id or "", run_id=run_id)

        # An explicit resume has a new Run id.  Copy only the structured Skill identities into
        # that Run so a later Worker/segment does not depend on the source process or snapshot.
        if _recovery_skill_state and resume_source_run_id:
            try:
                from app.services.chat.turn_context_builder import persist_skill_state
                for _skill_fact in _recovery_skill_state:
                    await persist_skill_state(run_id, _skill_fact)
            except Exception:  # noqa: BLE001
                logger.warning("恢复 Skill 事实复制失败 run=%s", run_id, exc_info=True)

        # separates acceptance from execution start. A client can subscribe immediately after
        # POST returns and render a durable acknowledgement even while preflight is still running.
        if not precreated_run:
            yield channel.run_accepted(turn_route)
        try:
            from app.services.agent_harness import run_store
            _harness_state = await run_store.get_run_state(run_id)
            _harness_mode = str(
                (((_harness_state or {}).get("state") or {}).get("agent_mode") or "standard")
            )
            if _harness_mode == "standard":
                await run_store.patch_run_state(run_id, {}, phase="executing")
                yield channel.run_phase_changed("executing")
        except Exception:  # noqa: BLE001
            logger.warning("executing checkpoint failed run=%s", run_id, exc_info=True)
        # v3.0：续做轮 run.started 附 resume_meta（「已从上次进度继续」提示数据，查询失败静默）
        _resume_meta: Optional[dict] = None
        _resume_plan: list[dict[str, Any]] = []
        try:
            _rs_msg = str(message or "").strip()
            if _rs_msg:
                from app.services.chat.turn_context_builder import needs_resume_checkpoint as _need_resume_cp
                if _need_resume_cp(_rs_msg) or resume_source_run_id:
                    from app.services.tasks import snapshot_service
                    _rs_snap = (
                        await snapshot_service.get_task_snapshot(
                            resume_source_run_id,
                            thread_id=thread_id,
                            user_id=user_id,
                        )
                        if resume_source_run_id
                        else await snapshot_service.get_latest_task_snapshot(thread_id)
                    )
                    if _rs_snap:
                        _rs_sum = _rs_snap.get("summary") or {}
                        _resume_meta = {
                            "snapshot": True,
                            "skill_ids": list(_rs_sum.get("skill_ids") or [])[:20],
                            # 只统计 artifact.saved + file_id 的精确回执。
                            # 最近文件清单是用户级定位索引，不是当前任务交付证据。
                            "artifact_count": len(_rs_sum.get("artifact_receipts") or []),
                        }
                    from app.services.tasks import plan_service
                    _receipt_rows = [
                        receipt for receipt in ((_rs_snap or {}).get("summary") or {}).get(
                            "artifact_receipts"
                        ) or []
                        if isinstance(receipt, dict)
                    ]
                    _source_run_id = (
                        str(resume_source_run_id or "").strip()
                        or str((_rs_snap or {}).get("run_id") or "").strip()
                    )
                    _resume_plan = await plan_service.inherit_latest_thread_plan(
                        run_id,
                        thread_id,
                        source_run_id=_source_run_id,
                        require_complete=bool(_receipt_rows),
                    )
        except Exception:  # noqa: BLE001
            _resume_meta = None
            _resume_plan = []
        yield channel.thread(
            agent_mode=profile_mode, resume_meta=_resume_meta, model=resolved_model,
        )
        if _resume_plan:
            yield channel.task_plan_updated(_resume_plan)
        # DeepSeek 的主调用先流 reasoning，后流 content/tool_calls；同一调用无法让
        # 模型首句出现在 Thinking 前。因此先跑一个有界的同模型非思考请求，
        # 它只生成公开 preamble，不选工具、不声称结果，也不替代主任务调用。
        # 普通问答在路由层已确定为 direct_answer：必须在附件召回、Skill 目录、记忆向量
        # 与自动路由之前短路。否则虽然最后没有工具调用，简单问题仍会为无关预取付出等待。
        direct_answer = turn_route == "direct_answer"
        public_preamble = ""
        preamble_guidance = ""
        if runtime_policy and runtime_policy.public_preamble_guidance:
            preamble_guidance = str(runtime_policy.public_preamble_guidance({
                "message": message,
                "interview_input": interview_input or {},
            }) or "").strip()
        if (
            not direct_answer
            and not loop_resume_messages
            # Domain-projected final answers stay on the committed receipt, but interview
            # still needs the same fast first sentence as the main agent.
            and (not (runtime_policy and runtime_policy.project_answer) or preamble_guidance)
            and _requires_reasoning_first_preamble(resolved_model, newapi_key)
        ):
            public_preamble = await _generate_public_preamble(
                message,
                model=resolved_model,
                api_key=newapi_key,
                attachments=attachments,
                plan_profile=wants_plan,
                research_profile=is_research_profile,
                run_id=run_id,
                thread_id=thread_id,
                extra_guidance=preamble_guidance,
            )
            if public_preamble:
                yield channel.message_commentary(public_preamble)

        # 上一轮「停止生成」的部分内容落库若仍未提交，先等它收敛（P1-4；位置修正
        # 2026-07-26）：必须排在本轮**任何写入之前**——此前它排在下面的用户消息提前
        # commit 之后，叠加 cancel_chat_run 超时仍谎报 cancelled，前端拿到「已停止」
        # 立刻发下一条，迟到的部分回复 created_at 反而晚于新用户消息（顺序错乱且污染
        # 下一轮上下文）；截断归档同理（按 id 归档跑在前，迟到的新行 id 更大会漏删）。
        # 与非流式 chat() 的同一纪律对齐。
        await self._await_pending_partial_persist(thread_id)

        # 编辑重发截断（F2/ADR-042 线性覆盖）放在 create_run 之后、**一切读历史的决策之前**
        # （四轮复审修订）：自动路由/外部推荐/压缩全都读会话历史，截断若晚于它们，路由与消歧
        # 决策仍会被用户已编辑掉的旧分支影响。顺序：先删 PG 摘要（strict——失败即中止，此刻
        # 消息未删无不一致，且 create_run 已落库、异常能正常收敛为 run.failed），再独立短
        # session 删 MySQL 消息并提交；其后的 ensure_compacted 基于截断后 transcript 重建摘要。
        if truncate_from_message_id:
            await context_service.delete_for_thread(thread_id, strict=True)
            async with async_session() as _trunc_session:
                # 被截断的用户消息 id：级联清理其绑定的会话附件（§5 删消息即清理该附件），
                # 否则编辑重发归档旧消息后，其附件仍作为会话资产被后续轮读到，语义不一致
                _cut_ids = (await _trunc_session.execute(
                    select(ChatMessage.id)
                    .where(ChatMessage.thread_id == thread_id)
                    .where(ChatMessage.id >= int(truncate_from_message_id))
                    .where(
                        ChatMessage.id != int(precreated_user_message_id)
                        if precreated_user_message_id is not None else True
                    )
                    .where(ChatMessage.role == "user")
                )).scalars().all()
                # P1 版本化（2026-07-17）：物理 DELETE 改软归档 status='archived'——死分支
                # 不再出现在任何读取（live/visible 过滤都排除），但数据与其执行轨迹留档。
                # 线性覆盖语义（F2/ADR-042）对用户不变，只是「覆盖」不再等于「销毁」。
                await _trunc_session.execute(
                    sa_update(ChatMessage)
                    .where(ChatMessage.thread_id == thread_id)
                    .where(ChatMessage.id >= int(truncate_from_message_id))
                    .where(
                        ChatMessage.id != int(precreated_user_message_id)
                        if precreated_user_message_id is not None else True
                    )
                    .values(status="archived")
                )
                await _trunc_session.commit()
            if _cut_ids:
                await thread_attachment_service.delete_for_messages(thread_id, list(_cut_ids))

        # 会话附件：Thread 级持久 + 每轮按当轮问题重检索（ADR-041 v1.18）；
        # 片段只进模型输入 model_input，不写进落库的用户消息 content
        # 多模态图文直传：当前轮模型命中 VISION_MODEL_KEYWORDS 时图片以 image_url 直传；
        # 子智能体同样按其自身模型判断。纯文本模型由平台视觉/OCR 服务代读，文档走文本注入。
        vision = model_supports_vision(resolved_model)
        if vision and attachments:
            from app.services.files import user_file_service as _ufs_vision
            attachments = await _ufs_vision.restore_vision_image_attachments(user_id, attachments)
        image_urls, text_atts = _split_attachments(attachments, vision)
        if not vision and text_atts:
            try:
                from app.services.files import user_file_service as _ufs_ocr
                text_atts = await _ufs_ocr.ensure_text_model_image_ocr(
                    user_id, text_atts, newapi_key=newapi_key,
                    audit_context={"run_id": run_id, "thread_id": thread_id},
                )
                # 识别后的状态也进入统一附件提示与持久元数据，不能沿用上传时的 status=ok。
                attachments = text_atts
            except Exception:  # noqa: BLE001
                logger.info("text-model image OCR enrich skipped", exc_info=True)
        page_guard = ""
        if vision and attachments:
            try:
                from app.services.files import user_file_service as _ufs_pages
                page_urls, page_guard = await _ufs_pages.document_page_data_urls(
                    user_id, attachments,
                )
                image_urls.extend(page_urls)
            except Exception:  # noqa: BLE001
                logger.info("document page vision inject skipped", exc_info=True)
                page_guard = ""
        file_context = ""
        if not direct_answer:
            file_context = await thread_attachment_service.build_file_context(
                thread_id=thread_id, user_id=user_id, query=message, attachments=text_atts,
                audit_context={"run_id": run_id, "thread_id": thread_id},
            )
        model_input = f"{message}\n\n{file_context}" if file_context else message
        if page_guard:
            model_input = f"{model_input}\n\n{page_guard}"
        ppt_style_reference_block = ""
        try:
            from app.services.skills.ppt_style_reference import analyze_ppt_style_reference
            ppt_style_reference_block = await asyncio.wait_for(
                analyze_ppt_style_reference(
                    message,
                    attachments,
                    newapi_key=newapi_key,
                    audit_context={"run_id": run_id, "thread_id": thread_id},
                ),
                timeout=float(settings.PPT_STYLE_REFERENCE_TIMEOUT_SECONDS),
            )
        except Exception:  # noqa: BLE001
            logger.info("PPT 参考图 DNA 预处理超时/失败，降级原附件上下文")
        if ppt_style_reference_block:
            model_input = f"{model_input}\n\n{ppt_style_reference_block}"
        # 附件读取置信度（P0 附件生命周期）：结构化 status 判定降级——事件下发前端提示条，
        # 并硬性要求模型在回答中如实说明未读全（此前失败占位文本混在正文里，用户无从感知）
        atts_meta = merge_composer_reference_meta(
            attachments,
            selected_skills=selected_skills,
            selected_knowledge=selected_knowledge,
            web_search=bool(web_search),
        )
        degraded_atts = [a for a in atts_meta if a.get("status") in ("partial", "failed")]
        unavailable_selected_atts = _unavailable_selected_attachments(attachments)
        if degraded_atts:
            degraded_payload = channel.attachments_status(degraded_atts)
            if degraded_payload:
                yield degraded_payload
            model_input = f"{model_input}\n\n{_attachments_degradation_note(degraded_atts)}"

        # 断点现场：裸「继续」或明确续做控制语时，注入当前会话工作区/显式选中范围内的
        # 产物清单，防止模型无视历史从零重做（只改 model_input，不改落库的用户消息原文）。
        _msg_s = str(message or "").strip()
        _user_wants_resume = False
        try:
            from app.services.chat.turn_context_builder import (
                build_resume_checkpoint as _resume_cp,
                needs_resume_checkpoint as _need_resume,
            )
            _user_wants_resume = bool(_need_resume(_msg_s) or resume_source_run_id)
            if _user_wants_resume:
                _cp = await _resume_cp(
                    user_id,
                    thread_id=thread_id,
                    source_run_id=resume_source_run_id,
                    selected_file_ids=[
                        str(_att_field(item, "file_id") or "").strip()
                        for item in (attachments or [])
                        if str(_att_field(item, "file_id") or "").strip()
                    ],
                    revision_target=revision_target,
                )
                if _cp:
                    model_input = f"{model_input}\n\n{_cp}"
                # v3.0 技能夹缝修复：resume 轮从任务快照取上一段技能清单重新注入。
                # 技能是一次性语义（前端发送后清空），不补回则新轮无 SKILL.md、沙箱无技能
                # 文件，模型被「禁止 use_skill 整包重载」硬闸夹住。合并后由
                # effective_ppt_skill_ids 去重、_fetch_trusted_skills 实时回源校验
                #（停用/失权技能自动不注入，安全）。
                try:
                    from app.services.tasks import snapshot_service
                    _snap_skills = await snapshot_service.resolve_resume_skill_ids(
                        user_id,
                        thread_id,
                        source_run_id=resume_source_run_id,
                    )
                    if _snap_skills:
                        skill_ids = list(dict.fromkeys(
                            [str(s) for s in (skill_ids or [])] + _snap_skills
                        ))
                        try:
                            await task_run_service.save_run_state(
                                run_id, {"skill_ids": list(skill_ids)},
                            )
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        # 输入框 / 「我的文件」选中的附件先写入会话工作区，再 hydrate。
        # 否则 Pull 看不到本轮刚上传的照片；有活沙箱时只 overlay 素材，不新建会话。
        try:
            from app.services.agent_harness import workspace_service as _ws_ingest
            await _ws_ingest.ingest_user_files_into_workspace(
                user_id=str(user_id or ""),
                thread_id=str(thread_id or ""),
                attachments=attachments,
                run_id=str(run_id or ""),
            )
        except Exception:  # noqa: BLE001
            logger.warning("workspace ingest of turn files failed run=%s", run_id, exc_info=True)
        # 断电重续：同一 Run 被僵尸收回时 pending 仍是原任务句，不是「继续」。
        # 只要本 Run 已有检查点就必须灌回新沙箱，不能关在 needs_resume 后面。
        try:
            from app.services.agent_harness.artifact_checkpoint import (
                attach_checkpoint_to_profile,
                hydrate_ppt_staging,
            )
            _ckpt_meta, _ = await hydrate_ppt_staging(
                run_id=str(run_id or ""),
                user_id=str(user_id or ""),
                thread_id=str(thread_id or ""),
                resume_source_run_id=str(resume_source_run_id or ""),
                user_wants_resume=_user_wants_resume,
                execution_profile=execution_profile,
            )
            if _ckpt_meta:
                execution_profile = attach_checkpoint_to_profile(
                    execution_profile, _ckpt_meta,
                )
        except Exception:  # noqa: BLE001
            pass
        model_input_content = _as_turn_content(model_input, image_urls)

        # ── 用户消息提前落库（刷新丢消息修复，2026-07-17）────────────────────────────
        # 此前用户消息要等下面一串「重活」（向量召回/内外部路由/记忆召回/上下文压缩，都是
        # LLM/embedding 调用，长会话压缩尤其慢、可达数十秒）**全部做完**才在末尾 commit；而 Run
        # 记录是重活之前就落库的（2026-07-14）。刷新恰好落在这段窗口时：Run 能恢复出「正在分析
        # 需求」时间线、用户消息却还没进 DB → 前端从 DB 重建时把它「吞」了（用户报告）。改为在
        # 重活之前用短会话先 commit 用户消息。compaction 只压「最近保留窗口之外」的旧消息
        # （context_service keep_n），不会折叠这条最新消息，故提前 commit 安全。regenerate（只重答、
        # 无新用户消息）不走此路，仍由下方 _drop_last_assistant 处理。
        early_user_message_id: Optional[int] = precreated_user_message_id
        if not regenerate and early_user_message_id is None:
            async with async_session() as _um_session:
                _um_row = ChatMessage(
                    thread_id=thread_id, role="user", content=message, run_id=run_id,
                    attachments_json=json.dumps(atts_meta, ensure_ascii=False) if atts_meta else None,
                )
                _um_session.add(_um_row)
                _um_thread = await _um_session.get(ChatThread, thread_id)
                if _um_thread:
                    _um_thread.updated_at = func.now()
                await _um_session.commit()
                early_user_message_id = _um_row.id
            # 会话附件资产随消息即刻持久（与原轮末口径一致：sha256 去重、内部吞错不抛）
            if attachments and early_user_message_id is not None:
                await thread_attachment_service.persist_for_message(
                    thread_id=thread_id, user_id=user_id,
                    message_id=early_user_message_id, attachments=attachments,
                )
            # 用户消息 id 回传（F2 配套）：前端据此给本地消息补 dbId（编辑重发的截断点）
            yield channel.user_message_saved(early_user_message_id)
        elif early_user_message_id is not None:
            if attachments:
                await thread_attachment_service.persist_for_message(
                    thread_id=thread_id, user_id=user_id,
                    message_id=early_user_message_id, attachments=attachments,
                )
            yield channel.user_message_saved(early_user_message_id)

        # Agent 才执行路由/预取/推荐。直答只保留必要的会话文本，不加载 Skill、记忆或
        # 候选智能体，避免普通问题被这些非必要依赖阻塞。
        prep = TurnContext() if direct_answer else await turn_prepare.prepare_turn(
            message=message, user_context=user_context, subagent_id=subagent_id,
            knowledge_ids=knowledge_ids, selected_knowledge=selected_knowledge,
            web_search=web_search, image_urls=image_urls, resolved_model=resolved_model,
            newapi_key=newapi_key, skill_ids=skill_ids, token=token,
            user_id=user_id, thread_id=thread_id,
            attachment_names=[str(_att_field(a, "filename") or "") for a in (attachments or [])],
            run_id=run_id,
            assistant_preset=assistant_preset,
        )
        if presentation_mode:
            _explicit_skill_ids = list(prep.effective_skill_ids)
            # 后续 HITL/计划确认快照必须记录 ACL 目录解析出的真实 Skill ID；受理阶段的
            # canonical `ppt-studio` 只是不可变意图，不能拿它冒充租户侧记录 ID。
            skill_ids = list(prep.effective_skill_ids)
        effective_subagent_id = prep.effective_subagent_id
        route_info = prep.route_info
        clarify_options = prep.clarify_options
        agents = prep.agents
        trusted_skills = prep.trusted_skills
        selected_skill_records = prep.selected_skill_records
        memory_block = prep.memory_block
        skill_catalog_block = prep.skill_catalog_block

        # Persist explicit selection facts with higher priority than a prior model/use_skill fact.
        # 这里只使用本轮 ACL 目录元数据；选择不等于已读取 SKILL.md 或已挂载技能包。
        try:
            from app.services.chat.turn_context_builder import (
                format_skill_recovery_observation,
                persist_skill_state,
                skill_state_record_from_skill,
            )
            selected_by_id = {
                str(item.get("id") or "").strip(): item
                for item in (selected_skill_records or [])
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            }
            for _explicit_id in _explicit_skill_ids:
                _explicit_skill = selected_by_id.get(_explicit_id) or {
                    "id": _explicit_id,
                    "name": _explicit_id,
                }
                _explicit_fact = skill_state_record_from_skill(
                    _explicit_skill,
                    selection_source="explicit",
                    selection_method="explicit",
                    status="selected" if _explicit_id in selected_by_id else "acl_unavailable",
                    retryable=_explicit_id not in selected_by_id,
                    last_error=(
                        "本轮未通过权威 ACL/目录校验"
                        if _explicit_id not in selected_by_id else ""
                    ),
                )
                if _explicit_fact:
                    await persist_skill_state(run_id, _explicit_fact)
            _skill_observation = format_skill_recovery_observation(
                _recovery_skill_state, trusted_skills,
            )
            if _skill_observation:
                model_input = f"{model_input}\n\n{_skill_observation}"
                model_input_content = _as_turn_content(model_input, image_urls)
        except Exception:  # noqa: BLE001
            logger.warning("Skill 恢复 observation/事实保存失败 run=%s", run_id, exc_info=True)

        # 发送前同步压缩（§13）：放在 DB session 块**外**——压缩触发时是同步 LLM 调用，
        # 不占用外层 MySQL 连接期间干等（高并发下防连接池耗尽）；已带硬超时降级。
        # 编辑重发时截断已在上方（create_run 后）完成，此处压缩基于截断后的 transcript。
        did_compact = False
        # v1.95：直答也必须按“本轮实际模型”的窗口做发送前预检。否则从大窗口切到
        # 小窗口后，direct_answer 会绕过 compact，随后才在 hard cap 兜底里丢旧原文。
        from app.services.platform import model_window as _mw
        _pre_tokens = 0
        try:
            _pre_rows = await context_service._segment_estimate(thread_id, resolved_model)
            _pre_tokens = int(_pre_rows or 0)
        except Exception:  # noqa: BLE001
            _pre_tokens = 0
        _window = _mw.resolve_window(resolved_model)
        _trigger = _mw.compact_trigger(_window)
        _compact_started = time.monotonic() if _pre_tokens >= _trigger else 0.0
        if _pre_tokens >= _trigger:
            yield channel.context_compaction("started", tokens_before=_pre_tokens)
        did_compact = await context_service.ensure_compacted(
            thread_id, resolved_model, newapi_key, blocking=_pre_tokens >= _trigger,
            audit_run_id=run_id,
        )
        if _pre_tokens >= _trigger:
            _compact_seconds = max(1, int(round(time.monotonic() - _compact_started)))
            if did_compact or context_service.is_background_compacting(thread_id):
                yield channel.context_compaction(
                    "completed", seconds=_compact_seconds, tokens_before=_pre_tokens,
                )
            else:
                yield channel.context_compaction(
                    "failed", seconds=_compact_seconds, tokens_before=_pre_tokens,
                )
        # 注：_await_pending_partial_persist 已上移到本轮首个写入之前（见 run.started 之后）。

        async with async_session() as session:
            thread = await session.get(ChatThread, thread_id)
            if not thread or thread.user_id != user_id:
                raise HTTPException(status_code=404, detail="会话不存在")

            is_first_turn = not thread.title
            if is_first_turn:
                thread.title = message[:50]

            # channel + run.started + create_run 都已在上方（重活之前）完成；这里只在
            # 显式 @ / 自动路由命中时把占位的 kind/subagent_id 补写成委派态。
            if effective_subagent_id:
                await task_run_service.set_run_route(
                    run_id, kind="subagent", subagent_id=effective_subagent_id
                )
            # 上一轮抽取的新记忆本轮首帧下发 chip（§14 Phase 2；SSE 已关流故延迟到次轮）
            _pending_mem = memory_service.pop_pending(user_id)
            if _pending_mem:
                yield channel.memory_updated(_pending_mem)
            # 自动路由命中：先告知前端"已转交 XX 智能体"（显式 @ 不发，用户已知）
            if route_info:
                yield channel.route_selected(route_info["subagent_id"], route_info["name"])

            # R5 消歧：匹配到多个候选，呈现选择卡并结束本轮（用户点选后按显式 subagent_id 再发起）
            if clarify_options and not effective_subagent_id:
                prompt_text = "匹配到多个可用智能体，请选择要使用的："
                # 用户消息通常已在「重活」前提前落库（early_user_message_id）；仅未提前落库的极端
                # 分支才在此补插（消歧路径 AUTO_ROUTE 默认关，dormant）
                if early_user_message_id is None:
                    session.add(ChatMessage(
                        thread_id=thread_id, role="user", content=message, run_id=run_id,
                        # 附件元数据快照（对齐正常发送路径，P2-11）：消歧分支若不写这个字段，
                        # 历史回放该轮会丢失附件卡（AUTO_ROUTE_ENABLED 打开后才会命中此分支）
                        attachments_json=json.dumps(atts_meta, ensure_ascii=False) if atts_meta else None,
                    ))
                assistant_row = ChatMessage(thread_id=thread_id, role="assistant", content=prompt_text,
                                            run_id=run_id, status="completed")
                session.add(assistant_row)
                thread.updated_at = func.now()
                await session.commit()
                # 原轮一次性上下文随事件回放（P2a）：技能 id + 附件正文（丢弃 image_url data URL，
                # 只留 filename/kind/text）。⚠️ 诚实说明（三轮评审 P2 边界）：这不是"引用"，而是
                # 附件**正文副本**——每条≤20k、≤10 条，故超限是**截断复用**而非精确复用；且 v1 事件
                # 随后会写进 agent_run_events，附件正文由此进入 Runtime PG 事件留存（单事件上限
                # ~200KB）。更理想是存受鉴权的附件 ID/快照 ID，重发时按 ID 重新解析——留待后续。
                # 现状取舍：消歧路径 dormant（AUTO_ROUTE 默认关），正文回放简单可用、体量可控。
                clarify_atts = [
                    {
                        "filename": _att_field(a, "filename"),
                        "kind": _att_field(a, "kind"),
                        "text": _att_field(a, "text")[:20000],
                    }
                    for a in (attachments or [])[:10]
                    if _att_field(a, "text")
                ]
                await task_run_service.set_clarifying(run_id)
                from app.services.agent_harness import run_store
                phase_updated = await run_store.patch_run_state(
                    run_id, {}, phase="waiting_clarification",
                )
                if phase_updated is None:
                    raise RuntimeError("无法持久化等待澄清状态")
                yield channel.clarification(
                    clarify_options, prompt_text,
                    skill_ids=list(skill_ids or []), attachments=clarify_atts,
                )
                yield channel.message_completed(prompt_text, assistant_row.id)
                yield channel.run_phase_changed("waiting_clarification")
                yield channel.done()
                return

            # （编辑重发截断已提前到本函数开头、发送前压缩之前执行——见 ensure_compacted 上方）

            # 用户消息与附件资产已在「重活」之前提前落库（early_user_message_id，见上方 F2
            # 回执亦已下发）。生成期间绝不能持有 ChatThread/ChatMessage 行锁：运行中插话需要
            # 并发 INSERT 同一 thread 的消息，首轮标题 UPDATE 或 regenerate 软标记若跨 LLM
            # 调用保持未提交，会让 /instruct 等到 MySQL 1205 后失败。标题在此用短事务提交；
            # regenerate 的旧回答只在新回答已经生成后，与新回答同一短事务原子 supersede。
            await session.commit()

            # 计划模式不能在用户明确选中的源文件已经删除/过期时继续规划：拿不到源文件的
            # 计划只能是凭空写的，而计划报告正是这一轮的交付物。普通对话仍走降级提示
            # （attachments_status + 模型输入里的降级说明）；计划模式在消息已持久化后进入
            # waiting_user，等待用户重新选择/上传，不把缺少用户资源伪装成任务失败。
            # 判据原为恒 False 的 is_plan_profile（graph 任务模式删除后的残留），整条闸从未生效；
            # 现接回计划模式——它就是当年那个显式入口的继任者。
            if plan_mode and unavailable_selected_atts:
                names = "、".join(
                    str(_att_field(a, "filename") or _att_field(a, "file_id") or "文件")
                    for a in unavailable_selected_atts[:5]
                )
                stop_text = (
                    f"选中的文件（{names}）已不存在或过期，我没有继续执行，避免基于缺失文件生成错误结果。"
                    "请在“我的文件”中重新选择，或重新上传后再启动计划模式。"
                )
                assistant_row = ChatMessage(thread_id=thread_id, role="assistant", content=stop_text,
                                            run_id=run_id, status="completed")
                session.add(assistant_row)
                thread.updated_at = func.now()
                await session.commit()
                from app.services.agent_harness import run_store
                await task_run_service.set_waiting(run_id, "waiting_user")
                await run_store.patch_run_state(
                    run_id,
                    {
                        "completion_observation": {
                            "kind": "user_input_required",
                            "reason_codes": ["selected_file_unavailable"],
                            "message": stop_text[:1000],
                            "unmet_conditions": ["重新选择或上传源文件"],
                        },
                    },
                    phase="waiting_user",
                )
                yield channel.message_completed(stop_text, assistant_row.id)
                yield channel.run_phase_changed("waiting_user")
                yield channel.done()
                return

            history_rows = (
                await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.thread_id == thread_id)
                    .where(live_chat_message_clause())  # P1 版本化：旧版/死分支不进上下文
                    .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
                )
            ).scalars().all()
            if regenerate:
                # 旧回答尚未在数据库里 supersede（必须等新回答生成成功），但重生成的模型
                # 上下文不能继续看到它；只在内存快照里摘掉最后一条存活助手消息。
                for index in range(len(history_rows) - 1, -1, -1):
                    if history_rows[index].role == "assistant":
                        history_rows = history_rows[:index] + history_rows[index + 1:]
                        break

            # 上下文预算（§13）：取（可能刚在 session 块外压缩过的）摘要 + 保留全部未覆盖原文
            # （不静默丢弃；仅超硬上限才应急裁并告警）
            summary = await context_service.get_summary(thread_id)
            ctx_window = model_window.resolve_window(resolved_model)
            # 硬上限须先扣掉本轮 system_prompt 预估（P0-2）：否则历史合法占满硬上限之上还要
            # 再叠加技能全文/目录/记忆等开销，总量很容易顶穿模型真实窗口
            _sys_prompt_tokens = _system_prompt_budget_tokens(
                agents, trusted_skills, selected_knowledge, knowledge_ids, memory_block,
                skill_catalog_block, summary,
            )
            prompt_rows, summary_block, _ctx_dropped = context_service.apply_context_budget(
                history_rows, summary,
                hard_cap_tokens=_history_hard_cap_tokens(ctx_window, _sys_prompt_tokens),
            )
            if did_compact:
                yield channel.context_compacted("Context compacted")

            # 上下文用量（§13）：分子尽量算全（历史+摘要+记忆+技能+推荐目录+附件+图片），分母用真实窗口；
            # 模型回传 usage 后于收尾用真实 prompt_tokens 校准
            raw_est_tokens = _estimate_prompt_tokens(
                prompt_rows=prompt_rows, summary_block=summary_block, file_context=file_context,
                memory_block=memory_block, trusted_skills=trusted_skills, agents=agents,
                image_count=len(image_urls),
            )
            # 真值校准（§13）：过往轮回传的 usage 已回灌为 per-model 因子，修估算的系统性偏差
            est_tokens = int(raw_est_tokens * calibration_factor(resolved_model))
            yield channel.context_usage(
                est_tokens, ctx_window, round(min(est_tokens / ctx_window, 1.0), 3),
                kind="estimate",
            )
            # 上面的历史 SELECT 会隐式开启一个只读事务。即便没有行锁，也不应在数十秒的
            # 模型/工具执行期间占住 MySQL 连接与一致性快照；回合执行从干净事务边界开始，
            # 最终消息再由 turn_finalizer 开一个短写事务提交。
            await session.commit()

            # ==== 回合分派(Phase 2c 骨架化):以下三个回合体各自安家 ====
            # 整轮委派(subagent_turn.run_dispatch_turn)/工具循环主路径
            # (main_tool_turn.run_agent_turn)/直答回退(plain_turn)。回合体内部
            # 逻辑自本方法原样搬迁;session/事务边界仍由本骨架持有,行为零变化。
            goal_contract_prompt = ""
            if loop_resume_messages:
                try:
                    from app.services.agent_harness.goal_contract import parse_goal_contract
                    from app.services.agent_harness import run_store as _gc_store
                    _packed = await _gc_store.get_run_state(run_id)
                    _gc = parse_goal_contract(
                        ((_packed or {}).get("state") or {}).get("goal_contract")
                    )
                    if _gc is not None:
                        goal_contract_prompt = _gc.prompt_block()
                except Exception:  # noqa: BLE001
                    goal_contract_prompt = ""
            elif decision_applied and turn_route in {"agent", "planning"}:
                try:
                    from app.services.agent_harness.goal_contract import seed_goal_contract
                    from app.services.agent_harness.goal_contract import load_prior_goal_contract
                    _prior_contract = await load_prior_goal_contract(resume_source_run_id)
                    goal_contract_prompt = seed_goal_contract(
                        message or "", turn_decision, route=turn_route,
                        prior=_prior_contract,
                    ).prompt_block()
                except Exception:  # noqa: BLE001
                    goal_contract_prompt = ""
            research_stage_prompt = ""
            if is_research_profile:
                try:
                    from app.services.agent_harness.research.engine import research_prompt_suffix
                    research_stage_prompt = await research_prompt_suffix(run_id)
                except Exception:  # noqa: BLE001
                    research_stage_prompt = ""
            prior_model = str(previous_model or "").strip()
            model_switch_prompt = (
                "<model_switch>本轮已从模型 " + prior_model + " 切换到 " + resolved_model
                + "。继续沿用同一会话中已确认的事实、用户约束、GoalContract、计划、"
                "工具与权限契约；不要假装这是新对话，也不要声称当前 Run 中途换过模型。"
                "</model_switch>"
                if prior_model and prior_model != resolved_model else ""
            )
            env = TurnEnv(
                session=session, thread=thread, channel=channel, run_id=run_id,
                thread_id=thread_id, user_id=user_id, user_context=user_context,
                token=token, newapi_key=newapi_key, resolved_model=resolved_model,
                message=message, model_input=model_input,
                execution_profile=execution_profile,
                quality_brief=(
                    f"{message}\n\n{ppt_style_reference_block}"
                    if ppt_style_reference_block else message
                ),
                model_input_content=model_input_content, attachments=attachments,
                text_atts=text_atts, image_urls=image_urls, atts_meta=atts_meta,
                web_search=web_search, plan_mode=plan_mode,
                research_profile=bool(is_research_profile),
                assistant_preset=assistant_preset,
                assistant_preset_snapshot=assistant_preset_snapshot if isinstance(assistant_preset_snapshot, dict) else None,
                skill_ids=skill_ids,
                task_auto_execute=bool(auto_plan_profile),
                turn_intent=turn_decision.intent if decision_applied else "execute",
                route=turn_route,
                # 只读边界是**物理**边界（build_tools 据此只给只读工具）——计划模式的
                # 「先规划，不动手」全靠它，不能随 Harness 开关兜底成 mutate。
                action_authority=(
                    runtime_policy.action_authority
                    if runtime_policy and runtime_policy.action_authority
                    else turn_decision.authority if decision_applied else "mutate"
                ),
                revision_mode=turn_decision.revision if decision_enforced else False,
                allow_create=turn_decision.allow_create if decision_enforced else True,
                revision_target=revision_target if decision_enforced else None,
                revision_file_candidates=(
                    list(revision_file_candidates)
                    if decision_enforced and not revision_target else []
                ),
                resume_source_run_id=str(resume_source_run_id or ""),
                turn_guard_prompt="\n".join(filter(None, [
                    "\n".join(filter(None, [
                        "" if loop_resume_messages else turn_decision.prompt_block(),
                        goal_contract_prompt,
                        research_stage_prompt,
                        (
                            "本轮 RevisionTarget 已解析为："
                            f"file_id={revision_target.get('file_id')}，"
                            f"文件名《{revision_target.get('filename')}》。"
                            "必须先读取它，再保持同一 file_id/同名写回。"
                            if revision_target else ""
                        ),
                        (
                            # 目标未解析：写工具物理未开放；候选文件名硬塞进确认卡约束。
                            '\n'.join(filter(None, [
                                REVISION_TARGET_UNRESOLVED_GUIDANCE,
                                build_revision_candidate_guidance(revision_file_candidates),
                            ]))
                            if (turn_decision.revision and not turn_decision.allow_create
                                and not revision_target) else ""
                        ),
                    ])) if decision_applied else "",
                    model_switch_prompt,
                    runtime_policy.turn_guard() if runtime_policy else "",
                    (
                        "【本轮已公开的过程首句】" + public_preamble
                        + "\n这句已经显示给用户；后续 commentary 不要复述它，"
                        "只在新发现、阶段切换或路线变化时更新。"
                        if public_preamble else ""
                    ),
                ])),
                public_preamble=public_preamble,
                knowledge_ids=knowledge_ids, selected_knowledge=selected_knowledge,
                regenerate=regenerate, is_first_turn=is_first_turn, prep=prep,
                initial_messages=loop_resume_messages,
                checkpoint_world_state=loop_resume_world_state,
                summary=summary, summary_block=summary_block, prompt_rows=prompt_rows,
                history_rows=history_rows, raw_est_tokens=raw_est_tokens,
                ctx_window=ctx_window, spawn_bg=self._spawn_bg,
                spawn_partial_persist=self._spawn_partial_persist,
                suspend_orchestration=self._suspend_orchestration,
            )
            if effective_subagent_id and user_context:
                async for payload in subagent_turn.run_dispatch_turn(env):
                    yield payload
                return

            if is_research_profile:
                from app.services.agent_harness.research.kernel import run as run_research_turn
                async for payload in run_research_turn(env):
                    yield payload
            else:
                async for payload in main_tool_turn.run_agent_turn(env):
                    yield payload
            if campus_mode and env.fallback_plain:
                raise RuntimeError("校园百事通不能回退到无守卫的普通问答")
            if not env.fallback_plain:
                return

            async for payload in plain_turn.stream_llm_round(env):
                yield payload

        async for payload in plain_turn.finish_plain_turn(env):
            yield payload

    # ---- call_subagent 编排辅助（ADR-046/开发计划 Phase 1-2）----

    # ===== Phase A（§2.3）：工具循环回合/子智能体回合已原样搬迁至 =====
    # chat/main_tool_turn 与 chat/subagent_turn；同名方法委托，SSE 事件名/顺序/载荷零变化。
    _trace_to_steps = staticmethod(main_tool_turn.trace_to_steps)
    _strip_images_for_state = staticmethod(main_tool_turn.strip_images_for_state)

    def _map_tool_loop_events(self, channel, ev_iter, sub_names: dict, out: dict,
                              tool_meta: Optional[dict] = None, subagent_icons: Optional[dict] = None,
                              research_profile: bool = False):
        return main_tool_turn.map_tool_loop_events(
            channel, ev_iter, sub_names, out, tool_meta, subagent_icons=subagent_icons,
            research_profile=research_profile,
        )

    def _make_subagent_runner(self, *, user_context, token: str, newapi_key: str,
                              resolved_model: str, histories=None, thread_id: str = "",
                              turn_attachments=None, run_id: str = ""):
        return subagent_turn.make_subagent_runner(
            user_context=user_context, token=token, newapi_key=newapi_key,
            resolved_model=resolved_model, histories=histories, thread_id=thread_id,
            turn_attachments=turn_attachments, run_id=run_id,
        )

    def _make_subagent_stream_runner(self, *, user_context, token: str, newapi_key: str,
                                     resolved_model: str, histories=None, thread_id: str = "",
                                     turn_attachments=None, run_id: str = ""):
        return subagent_turn.make_subagent_stream_runner(
            user_context=user_context, token=token, newapi_key=newapi_key,
            resolved_model=resolved_model, histories=histories, thread_id=thread_id,
            turn_attachments=turn_attachments, run_id=run_id,
        )

    async def _suspend_orchestration(self, run_id: str, suspended: dict, rounds: int,
                                     kb_ids, web_enabled: bool, answer_prefix: str = "",
                                     subagent_candidates: Optional[list] = None,
                                     tool_env: Optional[dict] = None):
        """持久化编排挂起游标（开发计划 Phase 2）：置 Run waiting + 存 messages/pending id/
        已出正文/工具重建参数。返回 (挂起引导语, input_required 载荷)。

        subagent_candidates：本轮语义发现的候选快照，随游标持久化；恢复时优先用它
        重建 call_subagent 工具目录，避免候选漂移。

        tool_env：挂起时那一轮**构建工具集用的上下文**（授权/修订/技能/原始诉求），见
        `_tool_env_snapshot`。不存这份快照，续接轮就只能拿 build_tools 的默认值重建工具集
        ——那等于 action_authority 默认回到 "mutate"、技能包 provider 变 None：
        ①「只分析别动手」/计划模式压出来的只读边界在 HITL 边界上凭空失效（Harness
          铁律是「工具集合本身即授权边界」，不能靠 prompt 补）；
        ②@ 选中的技能脚本不再挂进 /workspace/skills/，而系统提示词还在命令模型照
          SKILL.md 用 bash 执行——「先出计划再做 PPT」这条主用例整条断掉。
        对所有 ask_user_choice 挂起通用，不只计划模式。"""
        cand_snapshot = _candidate_snapshot(subagent_candidates)
        # 挂起态只有统一的 subagent 交互载荷；旧开发数据不再兼容。
        pend = suspended.get("subagent") or {}
        interactive = pend.get("interactive") or {}
        resume_token = pend.get("resume_id")
        sid = str((suspended.get("args") or {}).get("subagent_id") or "") or str(pend.get("subagent_id") or "")
        waiting_status = "waiting_confirmation" if bool((tool_env or {}).get("plan_mode")) else "waiting_user"
        extra_pending = dict(suspended.get("pending_input") or {})
        revision_gate = bool(extra_pending.get("revision_gate"))
        if revision_gate:
            waiting_status = "waiting_confirmation"
        await task_run_service.set_waiting(run_id, waiting_status, resume_token=resume_token)
        from app.services.agent_harness import run_store as harness_run_store
        pending_input = {
            "kind": "plan_confirmation" if waiting_status == "waiting_confirmation" else "clarification",
            "resume_id": resume_token,
            **extra_pending,
        }
        try:
            current_plan = None
            from app.services.tasks import plan_service as _plan_service
            current_plan = await _plan_service.get_current_plan(run_id)
            if current_plan:
                pending_input["plan_version"] = int(current_plan[0].get("plan_version") or 0)
        except Exception:  # noqa: BLE001
            logger.debug("plan_version snapshot on hang failed run=%s", run_id, exc_info=True)
        await harness_run_store.patch_run_state(
            run_id,
            {"pending_input": pending_input},
            phase=("waiting_confirmation" if waiting_status == "waiting_confirmation" else "waiting_clarification"),
        )
        # 2026-07-27 graph runtime 删除后，挂起游标只剩 tool_loop 一种
        # （原先 suspended 里带 "dag" 键时走图快照续跑，那条路径已随运行时一起下线）。
        orchestration = {
            "mode": "tool_loop",
            "messages": self._strip_images_for_state(suspended.get("messages") or []),
            "world_state": dict(suspended.get("world_state") or {}),
            "pending_tool_call_id": suspended.get("pending_tool_call_id"),
            "answer_so_far": answer_prefix + str(suspended.get("answer_so_far") or ""),
            "kb_ids": list(kb_ids or []),
            "web_enabled": bool(web_enabled),
            "subagent_candidates": cand_snapshot,
            "tool_env": dict(tool_env or {}),
        }
        await task_run_service.save_run_state(run_id, {
            "subagent_id": sid,
            "resume_id": resume_token,
            "interactive_type": interactive.get("type"),
            # ask_user_choice 消歧挂起（非子智能体）：resume 时用户选择直接回灌循环
            "ask_user": bool(pend.get("ask_user")),
            "subagent_rounds": rounds,
            "orchestration": orchestration,
        })
        try:
            packed = await harness_run_store.get_run_state(run_id)
            state = ((packed or {}).get("state") or {})
            await harness_run_store.persist_loop_checkpoint(
                run_id,
                messages=orchestration.get("messages") or [],
                world_state=(
                    orchestration.get("world_state")
                    or ((state.get("loop_checkpoint") or {}).get("world_state") or {})
                ),
                goal_revision=int(state.get("goal_revision") or 0),
                plan_version=int(state.get("plan_version") or 0),
            )
        except Exception:  # noqa: BLE001
            logger.debug("HITL loop_checkpoint persist skipped run=%s", run_id, exc_info=True)
        # 挂起即侧写任务快照（best-effort）：带待确认决策与技能清单，后续「继续」据此恢复
        try:
            from app.services.tasks import snapshot_service
            _snap_skills = list((tool_env or {}).get("skill_ids") or [])
            _pends: list = []
            _params = interactive.get("params")
            if isinstance(_params, list):
                for _p in _params:
                    if isinstance(_p, dict):
                        _text = str(
                            _p.get("label") or _p.get("value") or _p.get("text") or ""
                        ).strip()
                        if _text:
                            _pends.append(_text[:60])
            snapshot_service.spawn_snapshot_task(
                snapshot_service.save_hitl_snapshot(
                    run_id=run_id, skill_ids=_snap_skills, pending_decisions=_pends,
                )
            )
        except Exception:  # noqa: BLE001
            pass
        partial = pend.get("text") or "请在下方补全信息后提交。"
        payload = with_subagent_identity(
            {
                "run_id": run_id, "resume_id": resume_token,
                "type": interactive.get("type"), "params": interactive.get("params"),
            },
            subagent_id=sid, subagent_name=pend.get("subagent_name"),
        )
        # 消歧提问（ask_user_choice）标记随卡片下发：前端据此知道「直接打字也是回答」，
        # 用户发新消息时把旧卡收起（后端会把打字转交 resume，令牌一次性）
        if pend.get("ask_user"):
            payload["ask_user"] = True
        if waiting_status == "waiting_confirmation":
            payload = await _attach_plan_review_payload(
                run_id, payload, revision_gate=revision_gate,
            )
        return partial, payload

    async def _resume_orchestration(
        self, *, channel, run_id: str, thread_id: str, user_id: str, user_context,
        token: str, newapi_key: str, resolved_model: str,
        state: dict, rounds: int, result: dict,
        resume_value: Any = None,
    ) -> AsyncGenerator[str, None]:
        """编排续接（开发计划 Phase 2）：把 resume 后的子智能体结果回灌工具循环游标，主模型继续。

        - 子智能体再次 needs_input：游标不动，仅换令牌、轮次 +1（上限仍由 resume_chat 前置检查）；
        - failed：不直接失败 Run——错误文本回灌模型（编排语义：模型可重试/换人/如实告知）；
        - 循环再次挂起：更新游标（answer_so_far 前缀累加）；
        - 完成：挂起前正文 + 续接正文合并落库。
        """
        orchestration = state.get("orchestration") or {}
        messages = list(orchestration.get("messages") or [])
        resume_world_state = dict(
            orchestration.get("world_state")
            or ((state.get("loop_checkpoint") or {}).get("world_state") or {})
        )
        if not messages:
            try:
                from app.services.agent_harness import run_store as _ckpt_store
                packed = await _ckpt_store.get_run_state(run_id)
                checkpoint_state = (packed or {}).get("state") or {}
                messages = _ckpt_store.loop_checkpoint_messages(checkpoint_state)
                if not resume_world_state:
                    resume_world_state = dict(
                        (
                            (checkpoint_state.get("loop_checkpoint") or {}).get(
                                "world_state"
                            )
                            or {}
                        )
                    )
            except Exception:  # noqa: BLE001
                messages = []
        pending_id = str(orchestration.get("pending_tool_call_id") or "pending")
        answer_prefix = str(orchestration.get("answer_so_far") or "")
        kb_ids = list(orchestration.get("kb_ids") or [])
        web_enabled = bool(orchestration.get("web_enabled"))

        if result.get("status") == "needs_input":
            interactive = result.get("interactive") or {}
            new_resume = result.get("resume_id")
            await task_run_service.set_waiting(run_id, "waiting_user", resume_token=new_resume)
            await task_run_service.save_run_state(run_id, {
                "subagent_id": state.get("subagent_id"),
                "resume_id": new_resume, "interactive_type": interactive.get("type"),
                "subagent_rounds": rounds + 1,
                "orchestration": orchestration,
            })
            yield channel.message_delta(result.get("text") or "请在下方补全信息后提交。")
            yield channel.input_required(with_subagent_identity(
                {
                    "run_id": run_id, "resume_id": new_resume,
                    "type": interactive.get("type"), "params": interactive.get("params"),
                },
                subagent_id=result.get("subagent_id") or state.get("subagent_id"),
                subagent_name=result.get("subagent_name"),
            ))
            yield channel.done()
            return

        sub_id = str(result.get("subagent_id") or state.get("subagent_id") or "")
        sub_name = str(result.get("subagent_name") or "")
        is_ask_user = bool(state.get("ask_user"))
        if result.get("status") == "failed":
            fed = f"子智能体「{sub_name or sub_id}」执行失败：{result.get('text') or '未知原因'}"
            if not is_ask_user:
                yield channel.subagent_failed(sub_id, sub_name, fed)
        else:
            fed = str(result.get("text") or "（子智能体无输出）")
            # ask_user_choice 消歧续接没有子智能体：不发 subagent.* chip（否则前端出一个
            # 名为「子智能体」的幽灵 chip）
            if not is_ask_user:
                yield channel.subagent_completed(sub_id, sub_name, fed)

        # 用户在子工作流的选择卡里明确取消，是不可重试的合法终态。此前取消结果被压成
        # 普通 tool 文本交给主模型解释，真机上模型把“已取消、未提交”误判成“子智能体
        # 没产出”，随即重新 call_subagent，直接违背用户选择。取消语义必须由运行时收口，
        # 不能继续交给概率模型决定是否重试。
        if result.get("user_cancelled"):
            yield channel.message_delta(fed)
            cancelled_out = TurnOutcome(
                answer=fed,
                streamed_any=True,
                run_disposition="cancelled",
            )

            def _cancelled_steps(mid: int) -> list:
                return [] if is_ask_user else [{
                    "type": "subagent", "content": fed[:1000],
                    "meta": {
                        "name": "call_subagent", "status": "cancelled",
                        "subagent_id": sub_id, "subagent_name": sub_name,
                        "message_id": mid,
                    },
                }]

            full_cancel_text = (answer_prefix + fed).strip() or "已取消。"
            async for payload in turn_finalizer.finalize_resume_turn(
                channel=channel, thread_id=thread_id, run_id=run_id,
                out=cancelled_out, approval_sink=[], citation_sink=[], image_sink=[],
                sub_names={sub_id: sub_name} if sub_id else {}, spawn_bg=self._spawn_bg,
                full_text=full_cancel_text, failed_error_text=full_cancel_text,
                steps_answer_text=fed, extra_steps_factory=_cancelled_steps,
            ):
                yield payload
            return
        tool_env = dict(orchestration.get("tool_env") or {})
        from app.services.chat.builtin_assistants.runtime import (
            builtin_tool_build_options,
            get_builtin_runtime_policy,
        )
        runtime_policy = get_builtin_runtime_policy(
            tool_env.get("assistant_preset") or state.get("assistant_preset")
        )
        assistant_snapshot = (
            tool_env.get("assistant_preset_snapshot")
            or state.get("assistant_preset_snapshot")
            or {}
        )
        hung_in_plan_mode = bool(tool_env.get("plan_mode"))
        from app.services.tasks.plan_service import choice_approves_plan, choice_skips_plan
        if hung_in_plan_mode and choice_skips_plan(resume_value):
            ack = "好的，计划先放着。需要执行时直接说「执行计划」即可。"
            skip_out = TurnOutcome(answer=ack, streamed_any=True)
            yield channel.message_delta(ack)
            async for payload in turn_finalizer.finalize_resume_turn(
                channel=channel, thread_id=thread_id, run_id=run_id,
                out=skip_out, approval_sink=[], citation_sink=[], image_sink=[],
                sub_names={}, spawn_bg=self._spawn_bg,
                full_text=ack,
                failed_error_text=ack, steps_answer_text=ack,
            ):
                yield payload
            return
        from app.services.agent_harness import run_store as _unlock_run_store
        _unlock_snap = None
        try:
            _unlock_snap = await _unlock_run_store.get_run_snapshot(run_id)
        except Exception:  # noqa: BLE001
            _unlock_snap = None
        approve_exec = choice_approves_plan(resume_value) or choice_approves_plan(fed)
        executing_now = bool(
            (hung_in_plan_mode and approve_exec)
            or plan_execution_already_unlocked(_unlock_snap, tool_env)
        )
        if executing_now:
            tool_env = exit_plan_mode_tool_env(tool_env)
            orchestration["tool_env"] = tool_env
            try:
                await persist_plan_execution_unlock(run_id)
            except Exception:  # noqa: BLE001
                logger.warning("计划执行解锁失败 run=%s", run_id, exc_info=True)
            try:
                await task_run_service.save_run_state(run_id, {
                    **{k: state.get(k) for k in (
                        "subagent_id", "resume_id", "interactive_type",
                        "ask_user", "subagent_rounds",
                    ) if k in (state or {})},
                    "orchestration": orchestration,
                })
            except Exception:  # noqa: BLE001
                logger.debug("plan tool_env persist skipped run=%s", run_id, exc_info=True)
        env_plan_mode = hung_in_plan_mode and not executing_now
        try:
            from app.services.agent_harness import run_store as harness_run_store
            from app.services.tasks import plan_service
            _cur = await harness_run_store.get_run_state(run_id)
            _st = ((_cur or {}).get("state") or {})
            _pending_rev = (
                _st.get("pending_plan_revision")
                if isinstance(_st.get("pending_plan_revision"), dict)
                else None
            )
            if _pending_rev and approve_exec:
                applied = await plan_service.upsert_plan(run_id, _pending_rev.get("steps") or [])
                _ver = int((applied[0].get("plan_version") if applied else 0) or 0)
                await harness_run_store.patch_run_state(run_id, {
                    "approved_plan_version": _ver,
                    "approved_at": datetime.utcnow().isoformat() + "Z",
                    "pending_plan_revision": None,
                    "capability_scope": "default",
                })
                fed = (
                    f"{fed}\n（系统：用户已批准计划修订，请按新步骤继续执行。）"
                )[:8000]
            elif _pending_rev:
                await harness_run_store.patch_run_state(run_id, {"pending_plan_revision": None})
                fed = (
                    f"{fed}\n（系统：用户未批准这次结构性修订，请按已批准的计划继续；"
                    "若要改计划请先说明改法并再次 update_plan。）"
                )[:8000]
            elif hung_in_plan_mode and approve_exec:
                current_steps = await plan_service.get_current_plan(run_id)
                _ver = int(
                    (current_steps[0].get("plan_version") if current_steps else 0)
                    or _st.get("plan_version")
                    or 0
                )
                await harness_run_store.patch_run_state(run_id, {
                    "approved_plan_version": _ver,
                    "approved_at": datetime.utcnow().isoformat() + "Z",
                    "capability_scope": "default",
                })
        except Exception:  # noqa: BLE001
            logger.warning("计划批准版本写入失败 run=%s", run_id, exc_info=True)
        messages.append({"role": "tool", "tool_call_id": pending_id, "content": fed[:8000]})

        # 重建工具集：续接轮主模型仍可继续检索/再委派（候选重新按 ACL 拉取）。
        #
        # 关键是**按挂起时的工具环境重建**，不是按 build_tools 的默认值重建（P1 2026-07-27）。
        # 缺了 tool_env 时这里等价于 action_authority="mutate" + skill_packages_provider=None：
        # 只读轮次挂起一次就能换回全套写工具，@ 选中的技能脚本也不再进沙箱。详见
        # _suspend_orchestration 的 tool_env 说明。
        from app.services.agent_harness import run_store
        env_execution_profile = await run_store.load_finalized_execution_profile(run_id)
        if env_execution_profile is None:
            raise RuntimeError("续接 Run 缺少已冻结的 ExecutionProfile")
        try:
            from app.services.agent_harness.artifact_checkpoint import (
                attach_checkpoint_to_profile,
                hydrate_ppt_staging,
            )
            if env_execution_profile is not None:
                _hitl_ckpt, _ = await hydrate_ppt_staging(
                    run_id=str(run_id or ""),
                    user_id=str(user_id or ""),
                    thread_id=str(thread_id or ""),
                    resume_source_run_id=str(run_id or ""),
                    user_wants_resume=False,
                    execution_profile=env_execution_profile,
                )
                if _hitl_ckpt:
                    env_execution_profile = attach_checkpoint_to_profile(
                        env_execution_profile, _hitl_ckpt,
                    )
        except Exception:  # noqa: BLE001
            logger.debug("HITL PPT staging restore skipped run=%s", run_id, exc_info=True)
        # 存量兼容：本次修复之前挂起的 Run 没有 tool_env。这些游标是老代码写的，本来就
        # 带着这个缺口，续接时退回 "mutate" 只是**保持它们原有行为**，不制造新的回归
        # （部署后新产生的挂起一律带快照，缺口即闭合）。
        # 计划轮续接只有用户明确「开始执行」才升到 mutate（2026-07-28 真机事故 + 2026-08-18
        # 硬契约：点「我要改一改」必须留在只读计划轮，否则批准失去可验证含义）。
        env_authority = "mutate" if executing_now else str(
            tool_env.get("action_authority") or "mutate")
        env_revision_mode = bool(tool_env.get("revision_mode"))
        # 执行轮要能产出新文件；计划轮快照里的 allow_create 不该限制它
        env_allow_create = True if executing_now else bool(tool_env.get("allow_create", True))
        env_revision_target = tool_env.get("revision_target") or None
        # P2.7：修订确认卡 resume —— 用户点选/打字后，把选择映射为 revision_target，
        # 否则写工具仍被硬闸收起（prompt 说已确认、工具面却不能写）。
        if (
            is_ask_user
            and env_revision_mode
            and not env_allow_create
            and not (isinstance(env_revision_target, dict) and env_revision_target.get("file_id"))
        ):
            from app.services.chat.turn_decision import resolve_revision_target_from_choice
            mapped = resolve_revision_target_from_choice(
                fed, tool_env.get("revision_file_candidates") or []
            )
            if mapped and mapped.get("file_id"):
                try:
                    from app.services.files import user_file_service
                    snap = await user_file_service.get_revision_target_snapshot(
                        user_id, str(mapped["file_id"])
                    )
                    mapped.update(snap or {})
                except Exception as _map_exc:  # noqa: BLE001
                    logger.warning("resume 映射 revision_target 快照失败: %s", _map_exc)
                env_revision_target = mapped
                tool_env["revision_target"] = mapped
                logger.info(
                    "revision HITL resume 解锁目标 file_id=%s name=%s",
                    mapped.get("file_id"), mapped.get("filename"),
                )
        env_skill_ids = list(tool_env.get("skill_ids") or [])
        # 计划轮续接：把 messages[0] 里「本轮到此为止、不要动手」换成「开始执行」。
        # 工具集在下面已按快照放开写权限，提示词不跟着改就是两边指令相反——而
        # drive_model 在 initial_messages 非空时完全忽略 system_prompt 参数，
        # messages[0] 是模型这一轮唯一的系统指令。
        if executing_now and rewrite_plan_guard_for_execution(messages):
            logger.info("计划续接：已把计划轮约束改写为执行指令 run=%s", run_id)
        if executing_now:
            messages.append({"role": "system", "content": _PLAN_EXECUTION_GUARD})
        # 技能包按 id 实时回源（不用快照里的正文）：ACL/enabled 以续接这一刻的 auth-api 为准，
        # 挂起期间被停用/失权的技能不会因为快照而复活。
        env_skills = await _fetch_trusted_skills(env_skill_ids, token) if env_skill_ids else []
        if runtime_policy and runtime_policy.validate_resume_skills:
            runtime_policy.validate_resume_skills(env_skills)
        citation_sink: list = []
        image_sink: list = []
        approval_sink: list = []
        # 与首轮同一口径：ToolMetaSink 按 call_id 归属，并发同名调用不互相覆盖
        tool_meta_sink: dict = model_driver.ToolMetaSink()
        tool_progress_queue: asyncio.Queue = asyncio.Queue()
        from app.services.files.work_folders import folder_for_thread
        work_folder = await folder_for_thread(user_id, thread_id)
        tools = await model_driver.build_tools(
            token=token, knowledge_ids=kb_ids, web_enabled=web_enabled, citation_sink=citation_sink,
            image_sink=image_sink,
            tenant_id=await _resolve_kb_tenant(None, kb_ids),
            tool_meta_sink=tool_meta_sink, user_id=user_id,
            # thread_id/newapi_key 原本也漏传：前者是文件工具的会话归属，后者是浏览器/
            # 下载工具的网关凭证——续接轮少了它们，工具会以"无会话/无凭证"降级运行。
            thread_id=thread_id,
            newapi_key=newapi_key or "",
            workspace_folder_id=str((work_folder or {}).get("id") or ""),
            tool_progress_queue=tool_progress_queue,
            run_id=run_id,
            skill_packages_provider=_make_skill_packages_provider(env_skills, token),
            loaded_skills=env_skills,
            user_message=str(tool_env.get("user_message") or ""),
            attachments=tool_env.get("attachments") or None,
            action_authority=env_authority,
            turn_intent=str(tool_env.get("turn_intent") or "conversation"),
            revision_mode=env_revision_mode,
            allow_create=env_allow_create,
            revision_target=env_revision_target,
            execution_profile=env_execution_profile,
            **builtin_tool_build_options(runtime_policy, snapshot=assistant_snapshot),
        )
        if runtime_policy:
            tools = runtime_policy.bound_tools(tools)
        from app.services.agent_harness.tool_registry import (
            assert_tool_specs, visible_main_tools,
        )
        assert_tool_specs(tools)
        tools = await visible_main_tools(run_id, tools)
        sub_names: dict = {}
        subagent_icons: dict = {}
        cands: list = []
        # 委派授权与首轮同一口径（main_tool_turn）：没有 mutate 授权时不注册 call_subagent，
        # 否则只读轮次能借委派把写操作外包给子智能体工作流，绕开主工具的只读门禁。
        if runtime_policy is None and user_context and env_authority == "mutate":
            # 候选快照优先（§七）：恢复轮用挂起时的候选清单重建工具，避免重新召回漂移；
            # 真正调用时 runner 内部仍实时重查权限/状态/版本（下架/失权即明确失败）
            cands = await _resume_candidates(
                orchestration,
                user_context,
                run_id=run_id,
                thread_id=thread_id,
            )
            sub_names = {str(c.get("id")): str(c.get("name") or "") for c in cands}
            subagent_icons = {str(c.get("id")): str(c.get("icon") or "") for c in cands}
            sub_tool = model_driver.build_call_subagent_tool(
                cands,
                self._make_subagent_runner(
                    user_context=user_context, token=token, newapi_key=newapi_key,
                    resolved_model=resolved_model, thread_id=thread_id, run_id=run_id,
                ),
                max_calls=settings.SUBAGENT_TOOL_MAX_CALLS,
                runner_stream=self._make_subagent_stream_runner(
                    user_context=user_context, token=token, newapi_key=newapi_key,
                    resolved_model=resolved_model, thread_id=thread_id, run_id=run_id,
                ),
            )
            if sub_tool:
                tools.append(sub_tool)
        # 续接轮同样可再消歧；交互次数是观测指标，不是任务终止条件。
        if runtime_policy is None or runtime_policy.allow_choice_tool:
            tools.append(model_driver.build_ask_user_tool())
        # 续接不是另一套工具运行时：与首轮一样强制 V3 输出契约，
        # 并从 RunState 恢复已冻结的能力面。旧实现直接把 build_tools 全量
        # 交给模型，导致同一 Run 在 ask_user 前后 schema 突然扩张，且绕过
        # 首轮 assert_tool_contracts 闸。
        model_driver.assert_tool_contracts(tools)
        from app.services.chat.capability_broker import (
            CapabilityBroker,
            build_capability_search_tool,
            detect_explicit_memory_tools,
            resolve_core_pins,
        )
        from app.services.skills.ppt_agentic_adapter import is_agentic_ppt_profile
        from app.services.agent_harness.progress_policy import (
            checkpoint_meta_from_profile,
            required_progress_tools,
        )
        _resume_plan_rows = []
        try:
            from app.services.tasks import plan_service as _pin_plan_service
            _resume_plan_rows = await _pin_plan_service.get_current_plan(run_id)
        except Exception:  # noqa: BLE001
            _resume_plan_rows = []
        fallback_pins = resolve_core_pins(
            action_authority=env_authority,
            web_enabled=bool(web_enabled),
            plan_mode=bool(env_plan_mode),
            turn_intent=str(tool_env.get("turn_intent") or "conversation"),
            has_kb=bool(kb_ids),
            has_selected_files=any(
                _att_field(item, "file_id") for item in (tool_env.get("attachments") or [])
            ),
            has_trusted_skills=bool(env_skills),
            has_subagent_candidates=bool(cands),
            explicit_memory_tools=detect_explicit_memory_tools(
                str(tool_env.get("user_message") or "")
            ),
            pin_plan_tool=is_agentic_ppt_profile(env_execution_profile) or bool(_resume_plan_rows),
            progress_tools=required_progress_tools(
                env_execution_profile,
                _resume_plan_rows,
                checkpoint_meta=checkpoint_meta_from_profile(env_execution_profile),
            ),
        )
        if runtime_policy:
            fallback_pins = set(runtime_policy.pinned_tool_names)
        if executing_now:
            fallback_pins.add("update_plan")
        persisted_caps = [
            str(name) for name in (state.get("active_capabilities") or []) if str(name)
        ]
        # fallback 补齐老 Run 未持久的核心能力；persisted 恢复挂起前
        # search_capabilities 已解锁的次级能力。CapabilityBroker 会忽略已下线工具。
        resume_pins = [*fallback_pins, *persisted_caps]
        if is_agentic_ppt_profile(env_execution_profile):
            resume_pins.append("publish_ppt_artifact")
        if runtime_policy:
            tools = runtime_policy.validate_tools(tools)
            broker = CapabilityBroker(tools, pinned={tool.name for tool in tools})
            broker.freeze()
            tools = broker.initial_tools()
            tools = runtime_policy.validate_tools(tools)
        else:
            broker = CapabilityBroker(tools, pinned=resume_pins)
            broker.register(build_capability_search_tool(broker), active=True)
            broker.freeze()
            tools = broker.initial_tools()

        out = TurnOutcome()
        async with async_session() as _history_session:
            _resume_rows = (
                await _history_session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.thread_id == thread_id)
                    .where(live_chat_message_clause())
                    .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
                )
            ).scalars().all()
        _resume_public_history = []
        for row in _resume_rows:
            role = str(row.role or "")
            if role not in {"user", "assistant"}:
                continue
            display_item = {"role": role, "content": row.content}
            if getattr(row, "attachments_json", None):
                display_item["attachments_json"] = row.attachments_json
            _resume_public_history.append(display_item)
        # 流式段走共用兜底（P0 2026-07-26，turn_finalizer.stream_resume_events）：
        # 取消/异常都先把已流出正文落库再收尾，四条续接轮共享唯一实现。
        stream_state = turn_finalizer.ResumeStreamState()
        async for payload in turn_finalizer.stream_resume_events(
            channel=channel, run_id=run_id, thread_id=thread_id, out=out,
            state=stream_state, spawn_partial_persist=self._spawn_partial_persist,
            log_label="编排续接失败", failed_hint="任务续接失败，请稍后重试",
            # 挂起前正文只在 Run 游标里，中断落库必须带上（见 helper docstring）
            answer_prefix=answer_prefix,
            events=self._map_tool_loop_events(
                channel,
                model_driver.drive_model(
                    model=resolved_model, api_key=newapi_key, tools=tools,
                    # 续接轮同样是根循环：HITL 补全后继续跑的这一轮也要能被引导
                    gateway={"thread_id": thread_id, "user_id": user_id, "run_id": run_id,
                             "steerable": True, "capability_broker": broker},
                    approval_sink=approval_sink, initial_messages=messages,
                    history=_resume_public_history,
                    display_history=_resume_public_history,
                    world_state=resume_world_state,
                    tool_progress_queue=tool_progress_queue,
                    execution_profile=env_execution_profile,
                    # 改计划续接仍是计划轮：漏调 ask_user_choice 时平台还要再挂起确认。
                    # 点了「开始执行」后 plan_mode=False，不再二次确认。
                    plan_mode=bool(env_plan_mode),
                ),
                sub_names, out, tool_meta_sink, subagent_icons=subagent_icons,
            ),
        ):
            yield payload
        if stream_state.aborted:
            return

        if out["suspended"] is not None:
            partial, input_payload = await self._suspend_orchestration(
                run_id, out["suspended"], rounds + 1, kb_ids, web_enabled,
                answer_prefix=answer_prefix, subagent_candidates=cands,
                # 连环挂起（问一次→再问一次）必须把工具环境**原样传下去**：漏了这一手，
                # 第二次挂起写出的游标就没有 tool_env，第三轮又退回默认 mutate，
                # 等于只把缺口往后推了一轮。
                tool_env=tool_env,
            )
            # 消歧提问（ask_user）的问题由选择卡卡头承载，不再作为正文重复流出
            if not (out["suspended"].get("subagent") or {}).get("ask_user"):
                yield channel.message_delta(partial)
            if input_payload is not None:
                yield (
                    channel.plan_confirmation_required(input_payload)
                    if _is_plan_confirmation_payload(input_payload, plan_mode=env_plan_mode)
                    else channel.input_required(input_payload)
                )
            yield channel.done()
            return

        full = (answer_prefix + out["answer"]).strip() or "（无输出）"
        # 续接完成统一收尾（Phase 2b）：resumed_step 需要落库后的 message_id 归属 chip
        # 回放，经 factory 延迟构造；ask_user_choice 消歧续接没有子智能体，不落 subagent
        # step（防回放出幽灵 chip）。
        def _resumed_steps(mid: int) -> list:
            return [] if is_ask_user else [{
                "type": "subagent", "content": (fed or "")[:1000],
                "meta": {"name": "call_subagent",
                         "status": "failed" if result.get("status") == "failed" else "completed",
                         "subagent_id": sub_id, "subagent_name": sub_name, "message_id": mid},
            }]
        async for payload in turn_finalizer.finalize_resume_turn(
            channel=channel, thread_id=thread_id, run_id=run_id, out=out,
            approval_sink=approval_sink, citation_sink=citation_sink, image_sink=image_sink,
            sub_names=sub_names, spawn_bg=self._spawn_bg, full_text=full,
            failed_error_text=full, steps_answer_text=out["answer"],
            extra_steps_factory=_resumed_steps,
        ):
            yield payload

    async def resume_chat(
        self,
        *,
        user_id: str,
        user_context,
        run_id: str,
        resume_value: Any,
        resume_id: Optional[str] = None,
        token: str = "",
        newapi_key: Optional[str] = None,
        resolved_model: Optional[str] = None,
        protocol: str = sse_protocol.HARNESS,
    ) -> AsyncGenerator[str, None]:
        """恢复一个 HITL 挂起的 Run（子智能体交互续接，§10.4）。"""
        if not newapi_key or not resolved_model:
            newapi_key, resolved_model = await self.prepare_resume_chat(user_id, run_id)
        run = await task_run_service.get_run(run_id, user_id)
        thread_id = run["thread_id"] if run else ""
        channel = sse_protocol.SSEChannel(protocol, thread_id, run_id)
        if not run or run["status"] not in task_run_service.WAITING_RUN_STATUSES:
            yield channel.error("没有可恢复的挂起任务")
            yield channel.done()
            return
        was_plan_confirmation = str(run.get("status") or "") == "waiting_confirmation"
        accepted_resume_token = str(resume_id or "").strip()
        # 一次性恢复令牌（§15.5）**原子消费**：一条 CAS UPDATE 校验 waiting_* + 令牌匹配并
        # 同时置 running、清空令牌——并发重复提交只有一个能赢，防同一挂起被双执行（重复
        # 向业务系统提交）。旧前端未传令牌时消费仍原子，仅不校验令牌值。
        if not await task_run_service.consume_resume_token(run_id, resume_id):
            yield channel.error("恢复令牌无效或已被使用，请刷新会话")
            yield channel.done()
            return
        if was_plan_confirmation:
            # Plan 确认原先只有前端乐观气泡。刷新后用户输入与分段边界一起丢失，
            # 后端只能把规划与执行压回同一条轨迹，计划卡因而反复消失/移位。只在
            # resume 令牌 CAS 成功后落 transcript，避免网络重试留下幽灵用户消息。
            from app.services.tasks import run_input_service

            transcript_text = _plan_resume_transcript_text(resume_value)
            if transcript_text:
                input_message_id = await run_input_service.persist_user_message(
                    run_id=run_id,
                    thread_id=thread_id,
                    content=transcript_text,
                )
                if input_message_id is not None:
                    receipt_id = uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"plan-resume:{run_id}:{accepted_resume_token or transcript_text}",
                    ).hex
                    await run_input_service.persist_received_event(
                        run_id=run_id,
                        input_id=receipt_id,
                        message_id=input_message_id,
                        content=transcript_text,
                    )
        last_sequence = await task_run_service.get_last_event_sequence(run_id, user_id)
        channel = sse_protocol.SSEChannel(protocol, thread_id, run_id, start_sequence=last_sequence)
        run = await _unlock_plan_execution_before_resume(run_id, resume_value, run)
        state = run.get("state") or {}
        subagent_id = run.get("subagent_id") or state.get("subagent_id")
        resume_id = run.get("resume_token") or state.get("resume_id")
        rounds = int(state.get("subagent_rounds") or 1)
        yield channel.thread(
            agent_mode=str(run.get("agent_mode") or "standard"), model=resolved_model,
        )
        # 令牌消费回执（P1 五批）：CAS 已在上方成功——前端据此帧（而非 HTTP 2xx 响应头）
        # 判定「卡片不可再回滚」；响应头到达与令牌消费之间断连时，令牌仍有效、卡片可恢复重试
        yield channel.input_accepted()
        is_ask_user = bool(state.get("ask_user"))
        if (not subagent_id and not is_ask_user) or not resume_id:
            from app.services.chat.turn_finalizer import finalize_terminal
            async for terminal_frame in finalize_terminal(
                channel,
                run_id,
                {"run_disposition": "failed"},
                None,
                "恢复上下文缺失",
                "恢复上下文缺失",
            ):
                yield terminal_frame
            yield channel.done()
            return
        # ``rounds`` remains in RunState for diagnostics and resume continuity.  It must not
        # terminate the Run: an additional user answer or model segment is valid progress.

        if is_ask_user:
            # ask_user_choice 消歧挂起：无子智能体可恢复——用户的选择本身就是答案，
            # 直接构造成功结果回灌工具循环游标（_resume_orchestration 会补 tool 消息）
            # 多选卡回灌为可读文本（用户勾选多项→数组）；单选仍是字符串；其余兜底 JSON
            if isinstance(resume_value, (list, tuple)):
                picks = [str(x).strip() for x in resume_value if str(x or "").strip()]
                choice = "、".join(picks)
            elif isinstance(resume_value, dict):
                # 多问题卡以“问题文案 -> 用户答案”回灌，保留语义而不是丢给模型一串
                # 无法阅读的 JSON。数组值同样压成自然语言，跳过值显式说明。
                answer_lines = []
                for question, answer in list(resume_value.items())[:3]:
                    if isinstance(answer, (list, tuple)):
                        rendered = "、".join(str(item).strip() for item in answer if str(item or "").strip())
                    else:
                        rendered = str(answer or "").strip()
                    if rendered == "__auto__":
                        rendered = "用户跳过，请按最合理的默认假设继续"
                    answer_lines.append(f"- {str(question)[:120]}：{rendered or '未作答'}")
                choice = "\n".join(answer_lines)
            elif isinstance(resume_value, str):
                choice = resume_value
            else:
                choice = json.dumps(resume_value, ensure_ascii=False, default=str)
            if str(choice) == "__ASK_USER_SKIP__":
                # 用户点了「跳过」：不给答案，让模型按最合理理解继续并说明假设
                fed_text = "用户跳过了这个问题，没有给出答案；请按你认为最合理的理解继续，并在回答中简要说明该假设。"
            elif not str(choice).strip():
                # 多选卡未勾任何项就提交：等同跳过，避免回灌空「用户已选择：」
                fed_text = "用户没有选择任何选项；请按你认为最合理的理解继续，并在回答中简要说明该假设。"
            else:
                label = "用户对澄清问题的回答" if isinstance(resume_value, dict) else "用户已选择"
                fed_text = f"{label}：{str(choice)[:1200]}"
            result = {
                "status": "succeeded",
                "text": fed_text,
                "subagent_id": "", "subagent_name": "",
            }
        else:
            result = await subagent_service.resume_subagent(
                user=user_context, token=token, newapi_key=newapi_key,
                default_model=resolved_model, subagent_id=subagent_id,
                resume_id=resume_id, resume_value=resume_value,
                audit_run_id=run_id, audit_thread_id=thread_id,
            )
            if (
                result.get("status") == "succeeded"
                and str(state.get("interactive_type") or "") == "userSelect"
                and _is_explicit_cancel_choice(resume_value)
            ):
                result["user_cancelled"] = True

        # 编排续接（ADR-046/开发计划 Phase 2）：挂起发生在 call_subagent 工具循环内——
        # 把子智能体结果回灌保存的循环游标，主模型接着编排（成败都由模型看见并应对）
        orchestration = state.get("orchestration") or {}
        # 不支持已退休的挂起模式；遗留状态在本方法
        # 上方的 _legacy_mode 守卫里被明确拒绝并收成终态，走不到这里。
        if orchestration.get("mode") == "tool_loop":
            async for payload in self._resume_orchestration(
                channel=channel, run_id=run_id, thread_id=thread_id, user_id=user_id,
                user_context=user_context, token=token, newapi_key=newapi_key,
                resolved_model=resolved_model, state=state, rounds=rounds, result=result,
                resume_value=resume_value,
            ):
                yield payload
            return

        if result.get("status") == "needs_input":
            interactive = result.get("interactive") or {}
            new_resume = result.get("resume_id")
            await task_run_service.set_waiting(run_id, "waiting_user", resume_token=new_resume)
            await task_run_service.save_run_state(run_id, {
                "subagent_id": subagent_id,
                "resume_id": new_resume, "interactive_type": interactive.get("type"),
                "subagent_rounds": rounds + 1,
            })
            yield channel.message_delta(result.get("text") or "请在下方补全信息后提交。")
            yield channel.input_required(with_subagent_identity(
                {
                    "run_id": run_id, "resume_id": new_resume,
                    "type": interactive.get("type"), "params": interactive.get("params"),
                },
                subagent_id=result.get("subagent_id") or subagent_id,
                subagent_name=result.get("subagent_name"),
            ))
            yield channel.done()
            return

        text = result.get("text") or "（无输出）"
        async with async_session() as session:
            row = ChatMessage(thread_id=thread_id, role="assistant", content=text,
                              run_id=run_id, status="completed")
            session.add(row)
            th = await session.get(ChatThread, thread_id)
            if th:
                th.updated_at = func.now()
            await session.commit()
            mid = row.id

        if result.get("status") == "failed":
            yield channel.message_completed(text, mid)
            from app.services.chat.turn_finalizer import finalize_terminal
            async for terminal_frame in finalize_terminal(
                channel,
                run_id,
                {"run_disposition": "failed"},
                mid,
                text,
                text,
            ):
                yield terminal_frame
        else:
            yield channel.subagent(text, {
                "id": result.get("subagent_id"), "name": result.get("subagent_name"),
                "status": "succeeded",
            })
            from app.services.chat.turn_finalizer import finalize_terminal
            async for terminal_frame in finalize_terminal(
                channel,
                run_id,
                {},
                mid,
                text,
                text,
            ):
                yield terminal_frame
        yield channel.done()

    async def get_threads(
        self,
        user_id: str,
        search: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        scope: str = "ordinary",
    ) -> List[dict]:
        from app.services.chat.builtin_app_access import (
            ORDINARY_THREAD_SCOPE,
            normalize_thread_scope,
        )

        normalized_scope = normalize_thread_scope(scope)
        async with async_session() as session:
            # 普通主对话与系统内置应用按 Thread.origin 分域。过滤必须在搜索、排序与
            # 分页之前完成，否则某一应用会因其它域的结果占掉 limit/offset 而漏会话。
            stmt = select(ChatThread).where(
                ChatThread.user_id == user_id,
                ChatThread.app_id.is_(None),
                ChatThread.parent_thread_id.is_(None),  # 子智能体独立对话窗的子线程不进主历史列表
            )
            if normalized_scope == ORDINARY_THREAD_SCOPE:
                stmt = stmt.where(ChatThread.origin.is_(None))
            else:
                stmt = stmt.where(ChatThread.origin == normalized_scope)
            # 已发送的用户消息就是历史入口；不能因回答未生成、失败或停止而隐藏。
            # 只建线程的空壳和已归档分支仍不列入，未发送草稿另走本地区域。
            stmt = stmt.where(_history_listable_thread_clause())
            keyword = (search or "").strip()
            if keyword:
                like = f"%{keyword}%"
                msg_subq = (
                    select(ChatMessage.thread_id)
                    .where(ChatMessage.content.like(like))
                    .scalar_subquery()
                )
                stmt = stmt.where(or_(ChatThread.title.like(like), ChatThread.id.in_(msg_subq)))
            stmt = stmt.order_by(ChatThread.pinned.desc(), ChatThread.updated_at.desc())
            # 分页：置顶优先 + 更新倒序是稳定排序，offset/limit 可安全翻页（不传则全量，向后兼容）
            if limit is not None and limit > 0:
                stmt = stmt.offset(max(0, int(offset or 0))).limit(int(limit))
            rows = (await session.execute(stmt)).scalars().all()

        # Runtime PG 与主库是独立连接池。先结束主库读取，再批量加载本页活跃 Run，避免
        # 原先每条会话都串行访问 Runtime PG（limit=30 时最多 30 次网络往返）。
        active_by_thread = await self._resolve_active_runs([row.id for row in rows], user_id)
        from app.services.chat.builtin_assistants.registry import preset_from_thread_origin
        return [{
            "id": row.id,
            "title": row.title or "未命名对话",
            "assistant_preset": preset_from_thread_origin(row.origin) or None,
            "pinned": bool(row.pinned),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "model": str(row.model or "") or None,
            "active_run": active_by_thread.get(row.id),
        } for row in rows]

    async def set_pinned(self, user_id: str, thread_id: str, pinned: bool) -> None:
        async with async_session() as session:
            thread = await session.get(ChatThread, thread_id)
            if not thread or thread.user_id != user_id:
                raise HTTPException(status_code=404, detail="会话不存在")
            thread.pinned = 1 if pinned else 0
            await session.commit()

    async def rename_thread(self, user_id: str, thread_id: str, title: str) -> str:
        """重命名会话（用户手动改名，覆盖自动生成的标题）。"""
        clean = (title or "").strip()[:60]
        if not clean:
            raise HTTPException(status_code=400, detail="标题不能为空")
        async with async_session() as session:
            thread = await session.get(ChatThread, thread_id)
            if not thread or thread.user_id != user_id:
                raise HTTPException(status_code=404, detail="会话不存在")
            thread.title = clean
            await session.commit()
        return clean

    # 子智能体独立对话的会话模型已统一到 /workflow/run/*（运行栈，会话按 app_id 落库、
    # parent_thread_id 标记主对话委派来源）。曾并存的第三套 /chat/subthread* 接口从未被
    # UI 接线，2026-07-14 随会话模型收敛一并移除，避免三套模型并存造成误用。

    async def get_thread_messages(self, user_id: str, thread_id: str) -> List[dict]:
        async with async_session() as session:
            thread = await session.get(ChatThread, thread_id)
            if not thread or thread.user_id != user_id:
                raise HTTPException(status_code=404, detail="会话不存在")

            rows = (
                await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.thread_id == thread_id)
                    # P1 版本化：archived 死分支不返回；superseded 旧版随历史返回
                    # （status 标识，前端折叠为「查看上一版」）
                    .where(visible_chat_message_clause())
                    .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
                )
            ).scalars().all()

        # 当前 Runtime 引用优先；跨设备/环境回看时由同消息的持久展示快照托底。
        from app.services.chat.history_trace_projection import citations_from_projection

        citations_map = await citation_service.get_for_thread(thread_id)
        # 子智能体 chip 回放（§16.5 steps）：刷新/切回后仍能看到「这轮谁办的事」
        subagent_map = await task_run_service.get_subagent_steps_by_thread(thread_id)
        run_traces: dict = {}
        execution_map = await task_run_service.get_execution_traces_by_thread(
            thread_id,
            input_messages=[
                {
                    "message_id": m.id,
                    "run_id": m.run_id,
                    "content": m.content,
                    "created_at": m.created_at,
                }
                for m in rows
                if m.status == "run_input" and m.run_id
            ],
            run_traces=run_traces,
        )
        out = []
        durable_assistant_run_ids = {
            str(message.run_id)
            for message in rows
            if message.role != "user" and message.run_id
        }
        for m in rows:
            stored_trace = decode_execution_trace_projection(
                getattr(m, "execution_trace_json", None)
            )
            # 当前 Runtime 仍是权威来源；共享 MySQL 里的终态投影只在
            # 当前环境查不到该 Run 时托底（本机/服务器使用不同 Runtime PG）。
            trace = execution_map.get(m.id)
            if (
                not trace
                and m.run_id
                and (m.role != "user" or str(m.run_id) not in durable_assistant_run_ids)
            ):
                trace = run_traces.get(str(m.run_id))
            if not trace:
                trace = stored_trace
                if trace and not any(key in trace for key in ("run_id", "steps", "status")):
                    # A citations-only snapshot is not evidence of an execution timeline.
                    trace = None
            out.append({
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "feedback": m.feedback,
                # 消息状态与 run 归属（P0 刷新丢失修复）：status=cancelled/interrupted 时
                # 前端可标注「已停止/执行被中断」；NULL=旧数据视同 completed
                "status": m.status,
                "run_id": m.run_id,
                "agent_mode": (trace or {}).get("agent_mode") if m.role != "user" else None,
                "citations": citations_map.get(m.id) or citations_from_projection(stored_trace) or None,
                "subagent_calls": subagent_map.get(m.id) or None,
                "execution_trace": trace or None,
                "attachments": _loads_attachments(m.attachments_json),
            })
        # 兼容本修复之前已受理的 Run：它们的用户行 run_id=NULL，而助手行又
        # 尚未落库。后端明明已聚合出完整 run_traces，必须显式投影为助手时间线，
        # 否则前端只能从尾游标新建空气泡，丢步骤也丢 startedAt 计时。
        _append_unbound_active_run_trace_projections(out, run_traces)
        return out

    async def get_thread_context_usage(
        self, user_id: str, thread_id: str, model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """估算某会话当前上下文占用（口径同流式 context.usage 事件），供打开旧会话时恢复占用环。

        分母必须用「该会话下一次发送会用的模型」的真实窗口——传入 model 时按 model_window 解析，
        否则退回 _CONTEXT_WINDOW 兜底（32K）。旧实现恒用 32K，qwen3.7-plus（真实 128K）等大窗口
        模型的占用环因此被放大 4 倍以上，短对话也显示接近 100%（2026-07-22 用户报告「圈圈不准」）。
        """
        window = model_window.resolve_window(model) if model else _CONTEXT_WINDOW
        async with async_session() as session:
            thread = await session.get(ChatThread, thread_id)
            if not thread or thread.user_id != user_id:
                raise HTTPException(status_code=404, detail="会话不存在")
            rows = (
                await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.thread_id == thread_id)
                    .where(live_chat_message_clause())  # P1 版本化：占用估算按存活消息
                    .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
                )
            ).scalars().all()
        summary = await context_service.get_summary(thread_id)
        prompt_rows, summary_block, _dropped = context_service.apply_context_budget(rows, summary)
        est = estimate_messages(m.content or "" for m in prompt_rows) + estimate_tokens(summary_block)
        ratio = round(min(est / window, 1.0), 3)
        return {"tokens": est, "window": window, "ratio": ratio}

    async def set_message_feedback(self, user_id: str, message_id: int, feedback: Optional[str]) -> None:
        if feedback not in (None, "up", "down"):
            raise HTTPException(status_code=400, detail="无效的反馈值")
        async with async_session() as session:
            msg = await session.get(ChatMessage, message_id)
            if not msg:
                raise HTTPException(status_code=404, detail="消息不存在")
            thread = await session.get(ChatThread, msg.thread_id)
            if not thread or thread.user_id != user_id:
                raise HTTPException(status_code=403, detail="无权操作此消息")
            msg.feedback = feedback
            await session.commit()

    async def delete_thread(self, user_id: str, thread_id: str):
        subthread_ids: list[str] = []
        async with async_session() as session:
            thread = await session.get(ChatThread, thread_id)
            if not thread:
                raise HTTPException(status_code=404, detail="会话不存在")
            if thread.user_id != user_id:
                raise HTTPException(status_code=403, detail="无权删除此会话")
            # 级联删该主对话下的子智能体独立对话子线程（ADR-046 增强；ChatMessage FK CASCADE 带走消息）。
            # 注意排除 app_id 非空的委派会话（2026-07-14）：那是子智能体自己的运行历史（「我的智能体」
            # /@ 窗口可见），主对话删除只解除 parent 关联、不删记录。
            subthread_ids = (
                await session.execute(
                    select(ChatThread.id).where(
                        ChatThread.user_id == user_id,
                        ChatThread.parent_thread_id == thread_id,
                        ChatThread.app_id.is_(None),
                    )
                )
            ).scalars().all()
            await session.execute(
                sa_update(ChatThread)
                .where(
                    ChatThread.user_id == user_id,
                    ChatThread.parent_thread_id == thread_id,
                    ChatThread.app_id.isnot(None),
                )
                .values(parent_thread_id=None)
            )
            if subthread_ids:
                await session.execute(sa_delete(ChatThread).where(ChatThread.id.in_(subthread_ids)))
            await session.delete(thread)
            await session.commit()
        # 会话附件/摘要/引用/待发送队列随 Thread 清理（Runtime PG，尽力而为不阻断删除）；子线程同样清
        from app.services.tasks import thread_queue_service
        from app.services.chat import model_input_audit
        for _tid in [thread_id, *subthread_ids]:
            await thread_attachment_service.delete_for_thread(_tid)
            await context_service.delete_for_thread(_tid)
            await citation_service.delete_for_thread(_tid)
            await thread_queue_service.clear_thread(_tid)
            await model_input_audit.delete_for_thread(_tid)
            try:
                from app.services.agent_harness.workspace_service import delete_for_thread as _ws_delete
                await _ws_delete(_tid, user_id)
            except Exception:  # noqa: BLE001
                pass


harness_orchestrator = HarnessOrchestrator()
