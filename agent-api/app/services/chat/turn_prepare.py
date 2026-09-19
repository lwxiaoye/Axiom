"""回合准备层(结构手术 Phase 2a):并发预取。

此前这约 90 行在 chat()(非流式)与 stream_chat()(流式)里各维护一份、近乎逐行
相同——本模块是唯一实现,两个入口共享。产出 TurnContext(chat/types.py):
- 并发预取:技能按 ID 回源 auth-api(防 Prompt Injection)/ 长期记忆
  召回 / 个性化 / Skill 目录,四项互不依赖,gather 压缩首字延迟(各自内部已做失败降级);

（自动路由 / 子智能体候选 / 智能体推荐已随工作流编排整体删除。）
"""
import asyncio
import logging
from typing import Any, List, Optional

from app.core.config import settings
from app.services.memory import memory_service, personalization_service
from app.services.skills.ppt_policy import effective_ppt_skill_ids
from app.services.chat.builtin_assistants.runtime import get_builtin_runtime_policy
from app.services.chat.builtin_assistants.runtime_types import BuiltinTurnInput, BuiltinTurnServices

from .turn_context_builder import (
    _fetch_trusted_skills,
    _format_catalog,
    _get_catalog_records,
)
from .turn_finalizer import recent_history
from .types import TurnContext

logger = logging.getLogger(__name__)


def _selected_skill_metadata(records: list, skill_ids: list) -> list[dict]:
    """仅保留 ACL 目录中的显式选择元数据；SKILL.md 与包只由 use_skill 读取。"""
    selected = {str(item or "").strip() for item in (skill_ids or []) if str(item or "").strip()}
    out: list[dict] = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        skill_id = str(record.get("skillId") or record.get("id") or "").strip()
        if not skill_id or skill_id not in selected:
            continue
        out.append({
            "id": skill_id,
            "record_id": str(record.get("id") or record.get("recordId") or "").strip() or None,
            "name": str(record.get("name") or skill_id).strip()[:160],
            "description": str(record.get("description") or "").strip()[:400],
            "version": str(record.get("version") or record.get("skillVersion") or "").strip()[:128] or None,
            "selected": True,
        })
    return out


async def _lesson_block(user_id: str, message: str, thread_id: str = "") -> str:
    try:
        from app.services.agent_harness.goal_contract import (
            load_prior_goal_contract,
            seed_goal_contract,
        )
        from app.services.agent_harness.task_lesson import (
            format_lessons_for_prompt,
            recall_task_lessons,
        )
        from app.services.chat.turn_context_builder import needs_resume_checkpoint
        prior = None
        if thread_id and needs_resume_checkpoint(message or ""):
            from app.services.agent_harness import run_store
            source = await run_store.load_latest_finalized_execution_profile_source(
                thread_id=thread_id, user_id=user_id,
            )
            if source:
                prior = await load_prior_goal_contract(str(source.get("run_id") or ""))
        contract = seed_goal_contract(message or "", prior=prior)
        lessons = await recall_task_lessons(
            user_id, deliverable=contract.deliverable,
        )
        return format_lessons_for_prompt(lessons)
    except Exception:  # noqa: BLE001
        return ""


async def prepare_turn(
    *,
    message: str,
    user_context: Any,
    knowledge_ids: Optional[List[str]],
    selected_knowledge: Optional[List[Any]],
    web_search: bool,
    image_urls: List[Any],
    resolved_model: str,
    newapi_key: str,
    skill_ids: Optional[List[str]],
    token: str,
    user_id: str,
    thread_id: str,
    attachment_names: Optional[List[str]] = None,
    run_id: str = "",
    root_run_id: str = "",
    assistant_preset: str = "",
) -> TurnContext:
    """执行路由/预取，返回 TurnContext。

    普通主对话的非关键依赖继续就地降级；presentation 的 ppt-studio 权威校验失败时
    必须抛出清晰错误，不能静默退回普通主对话。
    """
    runtime_policy = get_builtin_runtime_policy(assistant_preset)

    # 2026-08-08：目录/历史/记忆/个性化并发；总墙钟受 TURN_PREPARE_BUDGET
    # 约束——超时用已完成部分继续，绝不因 auth-api/embedding 慢把首字拖成空白十几秒。
    async def _prior_users_task():
        # PPT 语境要看最近几轮（2026-07-27 真机事故）；失败降级只看当轮。
        try:
            return [
                str(h.get("content") or "") for h in await recent_history(thread_id)
                if h.get("role") == "user"
            ]
        except Exception:  # noqa: BLE001
            logger.info("取最近历史失败，PPT 语境判定降级为只看当轮", exc_info=True)
            return []

    budget = max(0.5, float(getattr(settings, "TURN_PREPARE_BUDGET_SECONDS", 2.5) or 2.5))

    if runtime_policy:
        return await runtime_policy.prepare_turn(
            BuiltinTurnInput(
                message=message, token=token, user_id=user_id, thread_id=thread_id,
                run_id=run_id, root_run_id=root_run_id, budget=budget,
            ),
            BuiltinTurnServices(
                get_catalog_records=_get_catalog_records,
                fetch_trusted_skills=_fetch_trusted_skills,
                recall_memory=memory_service.recall,
                format_memory=memory_service.format_for_prompt,
                personalization=personalization_service.prompt_block,
                lesson_block=_lesson_block,
                selected_skill_metadata=_selected_skill_metadata,
            ),
        )

    async def _core_prefetch():
        jobs = {
            "catalog": asyncio.create_task(_get_catalog_records(token)),
            "history": asyncio.create_task(_prior_users_task()),
            "memory": asyncio.create_task(memory_service.recall(
                user_id, query=message, thread_id=thread_id,
                **({"audit_run_id": run_id, "audit_root_run_id": root_run_id} if run_id else {}),
            )),
            "personalization": asyncio.create_task(personalization_service.prompt_block(user_id)),
            "lessons": asyncio.create_task(_lesson_block(user_id, message, thread_id)),
        }
        values: dict[str, Any] = {}
        try:
            done, _pending = await asyncio.wait(jobs.values(), timeout=budget)
            if _pending:
                logger.warning("prepare_turn 依赖超时，仅跳过：%s",
                               ",".join(name for name, job in jobs.items() if job in _pending))
            for name, job in jobs.items():
                if job in done and not job.cancelled():
                    try:
                        values[name] = job.result()
                    except Exception:  # noqa: BLE001 - each optional dependency fails independently
                        logger.warning("prepare_turn %s 不可用，保留其它预取结果", name)
        finally:
            for job in jobs.values():
                if not job.done():
                    job.cancel()
            await asyncio.gather(*jobs.values(), return_exceptions=True)
        catalog_records = values.get("catalog") or []
        if "catalog" not in values:
            # Keep the existing short cache retry, independently of completed recall.
            try:
                catalog_records = await asyncio.wait_for(_get_catalog_records(token), timeout=0.4)
            except Exception:
                pass
        _prior_users = values.get("history") or []
        _memory_recall = values.get("memory") or []
        _personalization_block = values.get("personalization") or ""
        trusted: list[dict] = []
        effective_skill_ids = effective_ppt_skill_ids(
            message, skill_ids, catalog_records, attachment_names,
            recent_user_messages=_prior_users,
        )
        selected_skill_records = _selected_skill_metadata(catalog_records, effective_skill_ids)
        skill_catalog_block = _format_catalog(catalog_records, set(effective_skill_ids))
        memory_block = "\n\n".join(
            b for b in (
                _personalization_block,
                memory_service.format_for_prompt(_memory_recall),
                values.get("lessons") or "",
            ) if b
        )
        return trusted, selected_skill_records, effective_skill_ids, memory_block, skill_catalog_block

    (
        trusted_skills, selected_skill_records, effective_skill_ids, memory_block,
        skill_catalog_block,
    ) = await _core_prefetch()
    return TurnContext(
        trusted_skills=trusted_skills,
        selected_skill_records=selected_skill_records,
        effective_skill_ids=effective_skill_ids,
        memory_block=memory_block,
        skill_catalog_block=skill_catalog_block,
    )
