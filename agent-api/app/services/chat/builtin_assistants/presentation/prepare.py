"""Authoritative Skill preload with the shared optional-context services."""

import asyncio
import logging

from app.services.chat.builtin_assistants.runtime_types import BuiltinTurnInput, BuiltinTurnServices
from app.services.chat.types import TurnContext
from .policy import resolve_ppt_studio_skill_id

logger = logging.getLogger(__name__)


async def prepare_turn(request: BuiltinTurnInput, services: BuiltinTurnServices) -> TurnContext:
    message, token = request.message, request.token
    user_id, thread_id = request.user_id, request.thread_id
    run_id, root_run_id, budget = request.run_id, request.root_run_id, request.budget
    route_info, clarify_options = None, []
    _get_catalog_records = services.get_catalog_records
    _fetch_trusted_skills = services.fetch_trusted_skills
    _lesson_block = services.lesson_block
    _selected_skill_metadata = services.selected_skill_metadata
    # 演示文稿助手的硬门槛只有两项：ACL 目录里存在启用的精确 ppt-studio，且其
    # 权威说明能够读取。记忆、个性化和历史经验仍尽量加载，但它们是可选上下文，
    # 不能因为任一服务慢或失败就把一个健康的 ppt-studio 误报为不可用。
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget

    async def _optional_context() -> str:
        memory_result, personalization_result, lesson_result = await asyncio.gather(
            services.recall_memory(
                user_id,
                query=message,
                thread_id=thread_id,
                **({
                    "audit_run_id": run_id,
                    "audit_root_run_id": root_run_id,
                } if run_id else {}),
            ),
            services.personalization(user_id),
            _lesson_block(user_id, message, thread_id),
            return_exceptions=True,
        )
        memory_text = ""
        if not isinstance(memory_result, BaseException):
            try:
                memory_text = services.format_memory(memory_result)
            except Exception:  # noqa: BLE001
                logger.info("演示文稿助手记忆格式化失败，忽略可选上下文", exc_info=True)
        personalization_text = (
            str(personalization_result or "")
            if not isinstance(personalization_result, BaseException)
            else ""
        )
        lesson_text = (
            str(lesson_result or "")
            if not isinstance(lesson_result, BaseException)
            else ""
        )
        return "\n\n".join(
            block for block in (personalization_text, memory_text, lesson_text) if block
        )

    async def _cancel_optional(task: asyncio.Task) -> None:
        if task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    optional_task = asyncio.create_task(_optional_context())
    try:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        catalog_records = await asyncio.wait_for(
            _get_catalog_records(token), timeout=remaining,
        )
        ppt_skill_id = resolve_ppt_studio_skill_id(catalog_records)
        if not ppt_skill_id:
            raise RuntimeError("演示文稿助手暂不可用：未找到已启用的 ppt-studio。")
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        trusted_skills = await asyncio.wait_for(
            _fetch_trusted_skills([ppt_skill_id], token), timeout=remaining,
        )
        if not trusted_skills or not str(trusted_skills[0].get("instructions") or "").strip():
            raise RuntimeError("演示文稿助手暂不可用：ppt-studio 权威说明读取失败。")
    except asyncio.TimeoutError as exc:
        await _cancel_optional(optional_task)
        raise RuntimeError("演示文稿助手暂不可用：ppt-studio 权威校验超时。") from exc
    except RuntimeError:
        await _cancel_optional(optional_task)
        raise
    except Exception as exc:  # noqa: BLE001
        await _cancel_optional(optional_task)
        raise RuntimeError("演示文稿助手暂不可用：ppt-studio 权威校验失败。") from exc

    memory_block = ""
    remaining = deadline - loop.time()
    if remaining > 0:
        try:
            memory_block = await asyncio.wait_for(optional_task, timeout=remaining)
        except asyncio.TimeoutError:
            logger.info(
                "演示文稿助手可选记忆上下文超过 %.1fs 预算，保留已验证的 ppt-studio 继续",
                budget,
            )
        except Exception:  # noqa: BLE001
            logger.info("演示文稿助手可选记忆上下文加载失败，继续执行", exc_info=True)
    else:
        await _cancel_optional(optional_task)

    effective_skill_ids = [ppt_skill_id]
    return TurnContext(
        effective_subagent_id=None,
        route_info=route_info,
        clarify_options=clarify_options,
        agents=None,
        trusted_skills=trusted_skills,
        selected_skill_records=_selected_skill_metadata(catalog_records, effective_skill_ids),
        effective_skill_ids=effective_skill_ids,
        memory_block=memory_block,
        skill_catalog_block="",
        recommend_agent_ids=[],
        recommend_external=None,
    )
