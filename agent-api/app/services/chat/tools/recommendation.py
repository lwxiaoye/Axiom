"""Harness control tool for optional marketplace-agent recommendations."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import PurePath
from typing import Any, Optional

from sqlalchemy import desc, select

from app.core.auth import UserContext
from app.core.config import settings
from app.core.runtime_db import runtime_session
from app.runtime_models import AgentRun, AgentRunEvent
from app.services.agents import recommendation_index

from .base import MainTool, ToolValue

logger = logging.getLogger(__name__)


def _in_rollout(user_id: str) -> bool:
    percent = max(0, min(100, int(settings.AGENT_RECOMMEND_ROLLOUT_PERCENT or 0)))
    if percent <= 0:
        return False
    if percent >= 100:
        return True
    bucket = int(hashlib.sha256(str(user_id or "anonymous").encode()).hexdigest()[:8], 16) % 100
    return bucket < percent


async def _recent_recommendation_ids(
    thread_id: str,
    *,
    current_run_id: str,
    assistant_runs: int = 5,
) -> set[str]:
    """Read the persisted thread recommendation cooldown, fail-open to an empty set."""
    factory = runtime_session()
    if factory is None or not thread_id:
        return set()
    try:
        async with factory() as session:
            run_ids = (
                await session.execute(
                    select(AgentRun.id)
                    .where(
                        AgentRun.thread_id == thread_id,
                        AgentRun.id != current_run_id,
                        AgentRun.status.in_(("completed", "failed", "cancelled")),
                    )
                    .order_by(desc(AgentRun.created_at))
                    .limit(max(1, int(assistant_runs)))
                )
            ).scalars().all()
            if not run_ids:
                return set()
            rows = (
                await session.execute(
                    select(AgentRunEvent.data).where(
                        AgentRunEvent.run_id.in_(list(run_ids)),
                        AgentRunEvent.type == "recommend_agents",
                    )
                )
            ).scalars().all()
        return {
            str(item)
            for data in rows if isinstance(data, dict)
            for item in (data.get("ids") or [])
            if str(item or "").strip()
        }
    except Exception:  # noqa: BLE001 - cooldown is UX protection, not an availability dependency
        return set()


def _attachment_types(attachments: list[Any]) -> list[str]:
    result: list[str] = []
    for item in attachments or []:
        if isinstance(item, dict):
            raw = item.get("kind") or item.get("mime_type") or item.get("filename")
        else:
            raw = getattr(item, "kind", None) or getattr(item, "mime_type", None) or getattr(item, "filename", None)
        value = str(raw or "").strip().lower()
        suffix = PurePath(value).suffix if value else ""
        normalized = suffix.lstrip(".") or value.split("/", 1)[-1]
        if normalized and normalized not in result:
            result.append(normalized[:32])
    return result[:8]


async def build_recommend_agent_tool(
    *,
    user: Optional[UserContext],
    raw_message: str,
    recent_user_messages: list[str],
    attachments: list[Any],
    thread_id: str,
    run_id: str,
    selected_specialist: bool,
) -> Optional[MainTool]:
    """Return the tool only when all server-owned readiness and scope gates pass."""
    if not settings.AGENT_RECOMMEND_ENABLED or user is None or selected_specialist:
        return None
    if not settings.AGENT_RECOMMEND_SHADOW_MODE and not _in_rollout(user.user_id):
        return None
    if not await recommendation_index.readiness():
        return None

    used = False
    used_lock = asyncio.Lock()

    async def _execute(args: dict) -> ToolValue:
        nonlocal used
        async with used_lock:
            if used:
                payload = {
                    "status": "suppressed",
                    "intent": str(args.get("intent") or "capability_gap"),
                    "recommendations": [],
                    "confidence": "weak",
                    "matched_signals": [],
                    "suppressed_reason": "run_call_limit",
                }
                return ToolValue(
                    model_content="本轮已检查过专业智能体，不要重复调用或向用户提及内部推荐流程。",
                    ui=payload,
                )
            used = True

        requested_intent = (
            "explicit_request"
            if str(args.get("intent") or "") == "explicit_request"
            else "capability_gap"
        )
        recent_ids = await _recent_recommendation_ids(
            thread_id, current_run_id=run_id, assistant_runs=5
        )
        # Explicit discovery may legitimately revisit an earlier result.  Implicit fallback uses
        # both a five-assistant-message cooldown and per-agent deduplication.
        if requested_intent == "capability_gap" and recent_ids:
            payload = {
                "status": "suppressed",
                "intent": "capability_gap",
                "recommendations": [],
                "confidence": "weak",
                "matched_signals": [],
                "suppressed_reason": "thread_cooldown",
            }
        else:
            try:
                # 留出少量 Harness 封装收尾时间，超时转成可拒绝的无结果，
                # 不让推荐依赖破坏主回答。
                inner_timeout = max(
                    0.1, float(settings.AGENT_RECOMMEND_TIMEOUT_SECONDS) - 0.1
                )
                result = await asyncio.wait_for(
                    recommendation_index.recommend(
                        raw_message=raw_message,
                        task=str(args.get("task") or "")[:400],
                        requested_intent=requested_intent,
                        recent_user_messages=recent_user_messages[-2:],
                        attachment_types=_attachment_types(attachments),
                        user=user,
                        excluded_ids=recent_ids if requested_intent == "capability_gap" else (),
                    ),
                    timeout=inner_timeout,
                )
                payload = result.to_dict()
            except asyncio.TimeoutError:
                payload = {
                    "status": "suppressed", "intent": requested_intent,
                    "recommendations": [], "confidence": "weak",
                    "matched_signals": [], "suppressed_reason": "timeout",
                }
            except Exception:  # noqa: BLE001 - recommendation must fail closed and not break chat
                logger.warning("recommend_agent query failed", exc_info=True)
                payload = {
                    "status": "suppressed", "intent": requested_intent,
                    "recommendations": [], "confidence": "weak",
                    "matched_signals": [], "suppressed_reason": "index_unavailable",
                }

        if settings.AGENT_RECOMMEND_SHADOW_MODE:
            logger.info(
                "recommend_agent shadow status=%s ids=%s confidence=%s signals=%s",
                payload.get("status"),
                [str(item.get("id")) for item in (payload.get("recommendations") or [])],
                payload.get("confidence"),
                payload.get("matched_signals"),
            )
            payload = {**payload, "shadow": True}
            return ToolValue(
                model_content="推荐能力处于静默评估，本轮不展示智能体卡片。请继续正常回答，不要提及推荐系统。",
                ui=payload,
            )
        if payload.get("status") != "matched":
            return ToolValue(
                model_content="没有智能体达到展示门槛。请继续完成当前回答，不要提及推荐系统或候选结果。",
                ui=payload,
            )
        compact = [
            {"name": item.get("name"), "reason": item.get("reason")}
            for item in payload.get("recommendations") or []
        ]
        return ToolValue(
            model_content=(
                "已找到符合门槛的专业智能体："
                + json.dumps(compact, ensure_ascii=False)
                + "。可用一句克制的话说明这是可选的后续方式；卡片由界面展示，"
                  "不要输出 ID、相似度、内部标记或编造额外能力。"
            ),
            ui=payload,
        )

    return MainTool(
        name="recommend_agent",
        description=(
            "仅在两种情况使用：用户明确询问平台里有没有某类智能体；"
            "或当前任务确实需要模拟面试、结构化填表、专业文档识别等定制流程，"
            "主 Agent 的通用办公能力不适合承接。普通写作、总结、翻译、润色、闲聊或只是"
            "不确定时不得调用。返回候选完全由服务端检索、权限校验和拒绝门槛决定；"
            "不要在 task 中填智能体 ID。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "用一句话概括用户当前需要的专业任务，不要写候选 ID",
                },
                "intent": {
                    "type": "string",
                    "enum": ["explicit_request", "capability_gap"],
                    "description": "用户明确索要智能体，或主 Agent 遇到真实专业能力缺口",
                },
            },
            "required": ["task", "intent"],
        },
        execute=_execute,
        output_model=ToolValue,
        readonly=True,
        parallel_safe=False,
        capability="control.agent.recommendation",
        semantic_tags=("control", "recommendation"),
        effect_scope="none",
        idempotent=True,
        timeout_seconds=float(settings.AGENT_RECOMMEND_TIMEOUT_SECONDS),
        cancellable=True,
        public_action="查找专业智能体",
        control_command=True,
        visible_to_user=False,
        allowed_profiles=("standard",),
        allowed_phases=("executing",),
        allowed_execution_profiles=("interactive",),
    )
