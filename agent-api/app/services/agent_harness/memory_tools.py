"""Model-facing memory controls backed by MemoryController governance."""

from __future__ import annotations

import uuid

from app.services.chat.tools.base import MainTool, ToolSoftError, ToolValue
from app.services.memory import memory_service
from app.services.memory.governance import IDENTITY_INSTRUCTIONS, grounded_quote, memory_identity

from .memory import (
    MemoryCandidate,
    MemoryController,
    MemoryGrounding,
    MemoryStability,
)


def build_memory_tools(*, user_id: str, thread_id: str | None = None,
                       user_message: str = "", run_id: str = "") -> list[MainTool]:
    controller = MemoryController()

    async def remember(args: dict) -> ToolValue:
        content = str(args.get("content") or "").strip()
        memory_type = str(args.get("type") or "").strip()
        if not content:
            raise ToolSoftError("没有要记住的内容。")
        explicitly_requested = bool(args.get("user_requested"))
        source = await memory_service.user_memory_source(user_id, thread_id or "", run_id)
        source_text = str(source.get("text") or user_message)
        quote = grounded_quote(args.get("source_quote"), source_text)
        if not quote:
            raise ToolSoftError("请引用本轮用户原话作为记忆来源；缺少可核对来源时不能写入。")
        metadata, source_ids = memory_service.memory_source_provenance(
            source, quote, run_id=run_id, kind="user_requested",
        )
        identity = args.get("identity")
        if identity is not None:
            if memory_identity({"identity": identity}) is None:
                raise ToolSoftError("记忆的对象、范围和属性必须完整；不确定时请省略 identity。")
            metadata["identity"] = identity
        candidate = MemoryCandidate(
            candidate_id=uuid.uuid4().hex,
            user_id=user_id,
            memory_type=memory_type,
            content=content,
            grounding=(
                MemoryGrounding.USER_STATED
                if explicitly_requested else MemoryGrounding.INFERRED
            ),
            stability=MemoryStability.STABLE,
            source_thread_id=thread_id,
            explicit_user_request=explicitly_requested,
        )

        async def writer(item: MemoryCandidate) -> str | None:
            if not await memory_service.is_enabled(user_id):
                return None
            memory_id, _is_new = await memory_service.store_memory(
                user_id=user_id,
                mem_type=item.memory_type,
                content=item.content,
                source_thread_id=item.source_thread_id,
                source_message_ids=source_ids or None,
                structured_value=metadata,
                audit_run_id=run_id,
                audit_thread_id=thread_id or "",
                return_new=True,
            )
            return str(memory_id) if memory_id else None

        decision = await controller.commit(candidate, writer)
        if not decision.accepted or not decision.memory_id:
            raise ToolSoftError(f"长期记忆未写入：{decision.reason_code}")
        return ToolValue(
            model_content=f"已按用户明确要求记住：{content}",
            ui={"summary": "长期记忆", "action": "已记录"},
            receipts=[{"kind": "memory", "action": "created", "id": decision.memory_id}],
        )

    async def forget(args: dict) -> ToolValue:
        description = str(args.get("description") or "").strip()
        if not description:
            raise ToolSoftError("请说明要忘记哪条记忆。")
        result = await memory_service.forget_by_description(user_id, description)
        deleted = result.get("deleted") if isinstance(result, dict) else None
        if deleted:
            return ToolValue(
                model_content="已删除用户指定的长期记忆。",
                ui={"summary": "长期记忆", "action": "已删除"},
                receipts=[{
                    "kind": "memory",
                    "action": "deleted",
                    "id": str(deleted.get("id") or ""),
                }],
            )
        if isinstance(result, dict) and result.get("none"):
            return ToolValue(model_content="当前没有可删除的长期记忆。")
        raise ToolSoftError("没有找到足够确定的匹配；请让用户明确要删除的内容。")

    return [
        MainTool(
            name="remember_fact",
            description=(
                "仅当用户本轮明确要求记住一条长期稳定的偏好或事实时调用。"
                "不得记录推测、临时任务状态或敏感信息；user_requested 必须为 true。"
                "source_quote 必须逐字引用本轮用户原话。" + IDENTITY_INSTRUCTIONS
            ),
            parameters={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["preference", "fact", "skills", "interests", "work_info", "context"],
                    },
                    "content": {"type": "string"},
                    "user_requested": {"type": "boolean", "const": True},
                    "source_quote": {"type": "string", "minLength": 2, "maxLength": 1000},
                    "identity": {
                        "type": "object", "additionalProperties": False,
                        "properties": {key: {"type": "string", "minLength": 1, "maxLength": 160}
                                       for key in ("subject", "scope", "attribute")},
                        "required": ["subject", "scope", "attribute"],
                    },
                },
                "required": ["type", "content", "user_requested", "source_quote"],
            },
            execute=remember,
            output_model=ToolValue,
            public_action="记录长期偏好",
            semantic_tags=("memory_candidate",),
            effect_scope="memory",
            idempotent=False,
            resource_locks=("user-memory",),
            internal=True,
        ),
        MainTool(
            name="forget_memory",
            description="仅在用户明确要求删除某条长期记忆时调用；匹配不确定时必须先澄清。",
            parameters={
                "type": "object",
                "properties": {"description": {"type": "string"}},
                "required": ["description"],
            },
            execute=forget,
            output_model=ToolValue,
            public_action="删除长期记忆",
            semantic_tags=("memory_delete",),
            effect_scope="memory",
            idempotent=False,
            resource_locks=("user-memory",),
            internal=True,
        ),
    ]
