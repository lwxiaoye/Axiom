"""直答与工具循环故障回退回合。

仅当工具循环在未流出任何正文时异常、且非任务模式才走到这里(任务模式绝不静默降级):
组装 system_prompt + 历史，按模型能力使用 Responses/Chat 流式直答；
可恢复连接失败最多重连五次，上下文超窗且零输出时强制压缩重试一次。
stream_llm_round 在骨架的 DB session 块内执行(与拆分前同一事务边界);
finish_plain_turn 在块外收尾(终态 CAS/标题/记忆抽取)。
"""
import asyncio
import copy
import logging
import random
import time
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.messages.utils import convert_to_messages
from sqlalchemy import func

from app.core.config import settings
from app.models import ChatMessage
from app.services.memory import context_service, memory_service
from app.services.tasks import task_run_service
from app.services.chat import turn_finalizer
from app.services.chat.turn_context_builder import (
    _attachments_meta,
    _build_system_prompt,
    _history_hard_cap_tokens,
    _is_context_length_error,
    _system_prompt_budget_tokens,
    split_system_prompt_context,
)
from app.services.agent_harness.responses_protocol import (
    messages_to_responses_input,
    model_is_deepseek,
    model_stream_exception_is_retryable,
    model_uses_responses_transport,
    provider_policy_rejection_from_exception,
    responses_exception_is_unsupported,
)
from app.services.agent_harness.model_stream import ModelProviderPolicyRejected
from app.services.agent_harness import run_store
from app.services.agent_harness import model_usage_audit
from app.services.agent_harness.thread_context_projection import (
    PreparedThreadProjection,
    commit_projection_bundle,
    prepare_plain_thread_projection,
    record_projection_canary_error,
    terminal_commit_bundle,
)
from app.services.agents.agent_service import agent_service
from app.services.platform.token_estimator import record_usage

logger = logging.getLogger(__name__)


def _public_history(rows: list[Any]) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for row in rows:
        role = str(getattr(row, "role", "") or "")
        if role not in {"user", "assistant"}:
            continue
        item: dict[str, Any] = {"role": role, "content": row.content}
        attachments_json = getattr(row, "attachments_json", None)
        if attachments_json:
            item["attachments_json"] = attachments_json
        history.append(item)
    return history


def _ordinary_plain_messages(
    *,
    stable_base: str,
    rows: list[Any],
    current_input: Any,
    world_state: str,
) -> list[dict[str, Any]]:
    messages = [{"role": "system", "content": stable_base}]
    messages.extend(
        {"role": str(row.role), "content": row.content}
        for row in rows
        if str(getattr(row, "role", "") or "") in {"user", "assistant"}
    )
    messages.append({"role": "user", "content": current_input})
    if world_state:
        messages.append({"role": "system", "content": world_state})
    return messages


def _provider_messages_to_langchain(
    messages: list[dict[str, Any]],
    *,
    use_responses_transport: bool = False,
) -> list[Any]:
    """Preserve native stateless Responses items when entering ChatOpenAI.

    ``convert_to_messages`` keeps unknown keys only in ``additional_kwargs``; ChatOpenAI's
    Responses serializer does not replay opaque output items from there. Responses/v1 content
    blocks are the supported stateless history representation, so native message wrappers are
    flattened into blocks while reasoning/function items retain their Provider fields.
    """

    converted: list[Any] = []
    for row in messages:
        native = row.get("_responses_output_items") if isinstance(row, dict) else None
        if str(row.get("role") or "") != "assistant" or not isinstance(native, list):
            provider_row = row
            if use_responses_transport and str(row.get("role") or "") == "system":
                # The direct tool path canonicalizes system facts to Responses' developer role.
                # Use the same wire role here so a plain/tool path switch preserves the exact
                # Provider prefix, not merely equivalent text.
                provider_row = {**row, "role": "developer"}
            converted.extend(convert_to_messages([provider_row]))
            continue
        blocks: list[dict[str, Any]] = []
        for item in native:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "") == "message":
                item_id = str(item.get("id") or "")
                for content in item.get("content") or []:
                    if not isinstance(content, dict):
                        continue
                    block = dict(content)
                    if item_id:
                        block.setdefault("id", item_id)
                    blocks.append(block)
                continue
            blocks.append(dict(item))
        converted.append(AIMessage(content=blocks or str(row.get("content") or "")))
    return converted


def _responses_output_items_from_message(message: Any) -> list[dict[str, Any]]:
    """Convert an aggregated Responses/v1 message back to stateless output items.

    LangChain streams Provider items as indexed content blocks. The ledger stores the Provider
    representation because the tool path replays requests without LangChain and encrypted
    reasoning must survive a later plain/tool path switch.
    """

    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return []
    items: list[dict[str, Any]] = []
    message_items: dict[tuple[str, Any], dict[str, Any]] = {}
    replayable_items = {
        "function_call",
        "custom_tool_call",
        "compaction",
        "web_search_call",
        "file_search_call",
        "computer_call",
        "code_interpreter_call",
        "mcp_call",
        "mcp_list_tools",
        "mcp_approval_request",
        "tool_search_call",
        "tool_search_output",
        "apply_patch_call",
        "apply_patch_call_output",
    }
    for raw_block in content:
        if not isinstance(raw_block, dict):
            continue
        block = copy.deepcopy(raw_block)
        block_type = str(block.get("type") or "")
        if block_type in {"text", "output_text", "refusal"}:
            item_id = str(block.get("id") or "")
            key = ("id", item_id) if item_id else ("index", block.get("index", 0))
            item = message_items.get(key)
            if item is None:
                item = {
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                }
                if item_id:
                    item["id"] = item_id
                message_items[key] = item
                items.append(item)
            if block_type == "refusal":
                item["content"].append({
                    "type": "refusal",
                    "refusal": str(block.get("refusal") or ""),
                })
            elif block.get("text") is not None:
                item["content"].append({
                    "type": "output_text",
                    "text": str(block.get("text") or ""),
                    "annotations": [],
                })
            continue
        block.pop("index", None)
        block.pop("sub_index", None)
        if isinstance(block.get("summary"), list):
            block["summary"] = [
                {
                    key: value
                    for key, value in summary.items()
                    if key not in {"index", "sub_index"}
                }
                for summary in block["summary"]
                if isinstance(summary, dict)
            ]
        if block_type == "reasoning":
            # The request is explicitly store=false. Only an encrypted cursor is independently
            # replayable; an ID/summary by itself would reference Provider-side stored state.
            if block.get("encrypted_content"):
                items.append(block)
            continue
        if block_type in replayable_items:
            items.append(block)
    return items


def _plain_attempt_payload(
    messages: list[dict[str, Any]],
    *,
    model: str,
    use_responses_transport: bool,
) -> dict[str, Any]:
    """Describe the frozen SDK request at the physical-attempt boundary.

    The unified audit service applies the repository sanitizer before persistence.  Keeping this
    conversion here limited to public message roles/content avoids depending on LangChain's
    private request builder while still fingerprinting the semantic payload actually replayed.
    """
    payload: dict[str, Any] = {
        "model": str(model or ""),
        "stream": True,
    }
    if use_responses_transport:
        payload["input"] = [
            {"type": "message", **item}
            if item.get("role") and not item.get("type")
            else item
            for item in messages_to_responses_input(messages)
        ]
        payload["store"] = False
        payload["include"] = ["reasoning.encrypted_content"]
    else:
        payload["stream_options"] = {"include_usage": True}
        payload["messages"] = [
            {
                key: value
                for key, value in message.items()
                if not str(key).startswith("_")
            }
            for message in messages
            if isinstance(message, dict)
        ]
    return payload


def _attempt_error_fields(exc: BaseException) -> tuple[int | None, str, str]:
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    try:
        http_status = int(status) if status is not None else None
    except (TypeError, ValueError):
        http_status = None
    body = getattr(exc, "body", None)
    error_code = type(exc).__name__
    if isinstance(body, dict):
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        error_code = str(error.get("code") or error_code)
    return http_status, error_code[:128], str(exc)[:2_000]


def _plain_chunk_usage(value: Any) -> Any:
    """Bridge LangChain's usage-metadata names to the Provider normalizer contract."""
    if not isinstance(value, dict):
        return value
    usage = dict(value)
    input_detail = usage.get("input_token_details")
    if isinstance(input_detail, dict) and input_detail.get("cache_read") is not None:
        usage.setdefault("input_tokens_details", {})["cached_tokens"] = input_detail[
            "cache_read"
        ]
    output_detail = usage.get("output_token_details")
    if isinstance(output_detail, dict) and output_detail.get("reasoning") is not None:
        usage.setdefault("output_tokens_details", {})["reasoning_tokens"] = output_detail[
            "reasoning"
        ]
    return usage


def _model_retry_delay(attempt: int) -> float:
    base = max(
        0.0,
        float(getattr(settings, "MODEL_STREAM_RETRY_BASE_SECONDS", 0.2) or 0.0),
    )
    ceiling = max(
        base,
        float(getattr(settings, "MODEL_STREAM_RETRY_MAX_SECONDS", 3.2) or 0.0),
    )
    raw = min(ceiling, base * (2 ** max(0, int(attempt) - 1)))
    return raw * random.uniform(0.9, 1.1) if raw else 0.0


def _chunk_stream_parts(chunk: Any) -> list[tuple[str, str]]:
    """Normalize Responses API summary/text blocks without exposing opaque reasoning state."""
    parts: list[tuple[str, str]] = []
    for block in getattr(chunk, "content_blocks", None) or []:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        if block_type == "reasoning":
            text = str(block.get("reasoning") or "")
            if not text:
                text = "".join(
                    str(part.get("text") or part.get("reasoning") or "")
                    for part in (block.get("summary") or block.get("content") or [])
                    if isinstance(part, dict)
                )
            if text:
                parts.append(("reasoning", text))
        elif block_type == "text":
            text = str(block.get("text") or "")
            if text:
                parts.append(("text", text))
    if parts:
        return parts

    # Non-Responses models keep the established string-content behavior.
    content = getattr(chunk, "content", "")
    if isinstance(content, str) and content:
        return [("text", content)]
    return []

# 工具循环故障回退直答时追加：此刻模型没有任何工具，但用户请求往往是任务型（搜索/写文件）。
# 真机实证（2026-07-27，deepseek-v4-pro）：不声明的话模型会顺着任务惯性**假装已执行**——
# 0 次真实调用却输出「搜索结果概要」。
# 2026-08-04：禁止「工具系统不可用」假故障口径——用户会误以为沙箱/平台宕机；
# 改为「本轮未启用/未能使用工具」，与 intentional pure_qa 区分。
FALLBACK_NO_TOOLS_GUARD = (
    "【重要】本轮未能使用工具：你当前没有搜索、网页抓取、文件读写等操作入口。"
    "绝不能声称或暗示已经用过这些能力（例如编造「搜索结果」「已保存文件」）。"
    "能凭已有知识回答的部分直接回答；必须依赖工具才能完成的部分，明确说明本轮未能调用工具、因此无法完成，不要编造执行结果，也不要把「下一轮再试」说成平台会自动恢复。"
    "不要暗示平台整体故障或宕机。"
)

# 故意走 pure_qa / direct_answer（高置信纯问答）时的护栏：不是故障回退。
PURE_QA_NO_TOOLS_GUARD = (
    "【本轮是纯问答】不要调用工具，也不要声称已搜索、读写文件或执行命令。"
    "直接基于已有知识自然、简洁地回答。"
    "不要以「收到」「好的」「明白了」等机械确认开头，也不要复述用户原话当开场白；有问题就直接答。"
    "不要假装已执行任何操作，也不要暗示平台故障。"
)


def apply_fallback_no_tools_guard(
    system_prompt: str,
    fallback_plain: bool,
    *,
    intentional_pure_qa: bool = False,
) -> str:
    """直答护栏：intentional_pure_qa=有意零工具；fallback_plain=工具循环故障回退。"""
    if intentional_pure_qa:
        return f"{system_prompt}\n\n{PURE_QA_NO_TOOLS_GUARD}"
    if not fallback_plain:
        return system_prompt
    return f"{system_prompt}\n\n{FALLBACK_NO_TOOLS_GUARD}"


async def stream_llm_round(env):
    """直答主体(session 块内):流式生成→落库→用量校准;取消时部分落库后原样上抛。"""
    session = env.session
    thread = env.thread
    channel = env.channel
    run_id = env.run_id
    thread_id = env.thread_id
    user_id = env.user_id
    user_context = env.user_context
    token = env.token
    newapi_key = env.newapi_key
    resolved_model = env.resolved_model
    message = env.message
    attachments = env.attachments
    regenerate = env.regenerate
    is_first_turn = env.is_first_turn
    prompt_rows = env.prompt_rows
    agents = env.prep.agents
    trusted_skills = env.prep.trusted_skills
    memory_block = env.prep.memory_block
    skill_catalog_block = env.prep.skill_catalog_block
    summary_block = env.summary_block
    knowledge_ids = env.knowledge_ids
    selected_knowledge = env.selected_knowledge
    model_input_content = env.model_input_content
    history_rows = env.history_rows
    raw_est_tokens = env.raw_est_tokens
    ctx_window = env.ctx_window
    summary = env.summary

    prompt_parts = split_system_prompt_context(
        _build_system_prompt(
            agents,
            trusted_skills,
            selected_knowledge,
            knowledge_ids,
            memory_block,
        )
    )
    kb_pre_context = env.kb_pre_context
    if getattr(env, "fallback_plain", False) and not kb_pre_context and (knowledge_ids or selected_knowledge):
        # 工具循环尚未走到前置召回就失败时，直答回退仍要保留用户“选了知识库”的语义。
        from app.services.chat.tools.knowledge import build_kb_pre_context

        ids = [str(item) for item in (knowledge_ids or []) if item]
        for item in selected_knowledge or []:
            kid = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
            if kid and str(kid) not in ids:
                ids.append(str(kid))
        kb_pre_context = await build_kb_pre_context(
            token, ids, message, raw_query=message,
            telemetry_user_id=str(user_id or ""),
            turn_id=str(run_id or ""),
            source="CHAT",
        )
        env.kb_pre_context = kb_pre_context
    dynamic_guard = apply_fallback_no_tools_guard(
        "",
        bool(getattr(env, "fallback_plain", False)),
        intentional_pure_qa=bool(getattr(env, "intentional_pure_qa", False)),
    ).strip()
    world_state_prompt = "\n\n".join(
        p
        for p in [
            prompt_parts.world_state,
            summary_block,
            skill_catalog_block,
            kb_pre_context,
            dynamic_guard,
        ]
        if p
    )
    projection_world_state = prompt_parts.as_context_section()
    projection_world_state.update({
        key: value
        for key, value in {
            "conversation_summary": summary_block,
            "skill_catalog": skill_catalog_block,
            "knowledge_context": kb_pre_context,
            "turn_guard": dynamic_guard,
        }.items()
        if value
    })
    # 当前轮用户消息以 model_input（含附件片段）进模型；落库行保持原文
    rows_for_prompt = prompt_rows[:-1] if (prompt_rows and prompt_rows[-1].role == "user") else list(prompt_rows)
    ordinary_provider_messages = _ordinary_plain_messages(
        stable_base=prompt_parts.stable_base,
        rows=rows_for_prompt,
        current_input=model_input_content,
        world_state=world_state_prompt,
    )
    source_messages = _ordinary_plain_messages(
        stable_base=prompt_parts.stable_base,
        rows=rows_for_prompt,
        current_input=model_input_content,
        world_state="",
    )
    active_provider_messages = list(ordinary_provider_messages)
    llm_messages = _provider_messages_to_langchain(active_provider_messages)
    display_history_current = _public_history(history_rows)
    if display_history_current and display_history_current[-1]["role"] == "user":
        display_history_before_run = display_history_current[:-1]
    else:
        display_history_before_run = list(display_history_current)
        current_display_user: dict[str, Any] = {
            "role": "user",
            "content": str(message or ""),
        }
        attachment_meta = _attachments_meta(attachments)
        if attachment_meta:
            current_display_user["attachments"] = attachment_meta
        display_history_current.append(current_display_user)
    current_run_delta = [{"role": "user", "content": model_input_content}]

    transport_aliases = agent_service.model_transport_aliases(
        resolved_model, newapi_key
    )
    catalog_responses_capability = agent_service.model_responses_capability(
        resolved_model, newapi_key
    )
    deepseek_transport_contract = model_is_deepseek(
        resolved_model,
        aliases=transport_aliases,
    )
    persisted_transport: dict[str, Any] = {}
    try:
        transport_snapshot = await run_store.get_run_state(str(run_id or ""))
        transport_state = (transport_snapshot or {}).get("state") or {}
        candidate_transport = transport_state.get("model_transport")
        if (
            isinstance(candidate_transport, dict)
            and str(candidate_transport.get("model") or "") == str(resolved_model or "")
            and str(candidate_transport.get("protocol") or "")
            in {"responses", "chat_completions"}
        ):
            persisted_transport = dict(candidate_transport)
    except Exception:  # noqa: BLE001 - transport persistence is best-effort here
        logger.debug("Plain-turn model transport lock unavailable run=%s", run_id, exc_info=True)

    persisted_protocol = str(persisted_transport.get("protocol") or "")
    if persisted_protocol:
        use_responses_transport = persisted_protocol == "responses"
        transport_source = str(persisted_transport.get("source") or "run_lock")
    else:
        use_responses_transport = model_uses_responses_transport(
            resolved_model,
            aliases=transport_aliases,
            supports_responses=catalog_responses_capability,
        )
        transport_source = (
            "deepseek_contract"
            if deepseek_transport_contract
            else "catalog"
            if catalog_responses_capability is not None
            else "probe"
        )
    transport_committed = bool(persisted_transport.get("confirmed"))
    responses_fallback_allowed = bool(
        use_responses_transport and not transport_committed
    )

    async def _persist_model_transport(
        protocol: str,
        *,
        confirmed: bool,
        source: str,
    ) -> None:
        nonlocal persisted_transport
        transport_payload = {
            "model": str(resolved_model or ""),
            "protocol": str(protocol or ""),
            "confirmed": bool(confirmed),
            "source": str(source or "")[:40],
        }
        if transport_payload == persisted_transport or not run_id:
            persisted_transport = transport_payload
            return
        try:
            updated = await run_store.patch_run_state(
                str(run_id),
                {"model_transport": transport_payload},
            )
            if updated is not None:
                persisted_transport = transport_payload
        except Exception:  # noqa: BLE001 - answering must not depend on telemetry persistence
            logger.debug(
                "Plain-turn model transport lock persist skipped run=%s protocol=%s",
                run_id,
                protocol,
                exc_info=True,
            )

    await _persist_model_transport(
        "responses" if use_responses_transport else "chat_completions",
        confirmed=bool(
            transport_committed or catalog_responses_capability is False
        ),
        source=transport_source,
    )

    projection_prepared = PreparedThreadProjection(
        provider_messages=list(ordinary_provider_messages),
        commit_bundle=None,
        provider_reuse_active=False,
        eligibility_reason="not_main_plain_answer",
    )
    projection_allowed = str(
        getattr(env, "model_call_purpose", "") or "plain_answer"
    ).strip() == "plain_answer"

    async def _prepare_plain_projection(*, force_reason: str = "") -> None:
        nonlocal projection_prepared, active_provider_messages, llm_messages
        if not projection_allowed:
            active_provider_messages = list(ordinary_provider_messages)
            llm_messages = _provider_messages_to_langchain(
                active_provider_messages,
                use_responses_transport=use_responses_transport,
            )
            return
        projection_prepared = await prepare_plain_thread_projection(
            run_id=str(run_id or ""),
            thread_id=str(thread_id or ""),
            model=str(resolved_model or ""),
            transport=(
                "responses" if use_responses_transport else "chat_completions"
            ),
            stable_base=prompt_parts.stable_base,
            ordinary_provider_messages=ordinary_provider_messages,
            source_messages=source_messages,
            current_run_delta=current_run_delta,
            display_history_before_run=display_history_before_run,
            display_history_current=display_history_current,
            world_state=projection_world_state,
            force_reset_reason=force_reason,
        )
        active_provider_messages = list(projection_prepared.provider_messages)
        llm_messages = _provider_messages_to_langchain(
            active_provider_messages,
            use_responses_transport=use_responses_transport,
        )

    initial_reset_reason = "history_replaced" if regenerate else ""
    await _prepare_plain_projection(force_reason=initial_reset_reason)

    def _new_llm() -> ChatOpenAI:
        llm_options: dict[str, Any] = {
            "model": resolved_model,
            "base_url": settings.NEWAPI_BASE_URL,
            "api_key": newapi_key,
            "streaming": True,
            "stream_usage": True,
            # Harness owns the visible retry/fallback state machine below.  Leaving the SDK's
            # default retries enabled multiplies each logical reconnect into up to three hidden
            # HTTP attempts and makes Provider usage impossible to attribute correctly.
            "max_retries": 0,
        }
        if use_responses_transport:
            llm_options.update({
                "use_responses_api": True,
                "output_version": "responses/v1",
                "store": False,
                "include": ["reasoning.encrypted_content"],
            })
        return ChatOpenAI(**llm_options)

    llm = _new_llm()

    audit_purpose = str(
        getattr(env, "model_call_purpose", "") or "plain_answer"
    ).strip()
    if audit_purpose not in model_usage_audit.MODEL_CALL_PURPOSES:
        audit_purpose = "plain_answer"
    audit_purpose_detail = str(
        getattr(env, "model_call_purpose_detail", "")
        or getattr(env, "route", "")
        or "plain"
    )[:160]
    audit_root_run_id = str(getattr(env, "root_run_id", "") or "")

    async def _begin_logical(
        *,
        parent_logical_call_id: str = "",
    ):
        return await model_usage_audit.begin_logical_call(
            run_id=str(run_id or ""),
            thread_id=str(thread_id or ""),
            root_run_id=audit_root_run_id,
            parent_logical_call_id=str(parent_logical_call_id or ""),
            model=str(resolved_model or ""),
            transport=("responses" if use_responses_transport else "chat_completions"),
            purpose=audit_purpose,
            purpose_detail=audit_purpose_detail,
            provider_api_key=newapi_key,
        )

    logical_call = await _begin_logical()

    full_response = ""
    persisted = False
    usage_prompt_tokens = 0
    reasoning_buf: list[str] = []
    reasoning_started_at = 0.0
    context_retry_used = False
    stream_retries = 0
    max_stream_retries = max(
        0,
        int(getattr(settings, "MODEL_STREAM_MAX_RETRIES", 5) or 0),
    )
    published_text = ""
    published_reasoning = False
    provider_event_seen = False
    suppress_retry_text_stream = False
    recovery_pending_transport = ""
    next_attempt_kind = "initial"
    previous_attempt_id = ""

    def _close_reasoning() -> str:
        nonlocal reasoning_started_at
        if not reasoning_buf:
            return ""
        full = "".join(reasoning_buf)
        reasoning_buf.clear()
        seconds = max(0.0, time.monotonic() - reasoning_started_at) if reasoning_started_at else 0.0
        reasoning_started_at = 0.0
        return channel.message_reasoning_completed(full, seconds)

    while True:
        attempt_handle = None
        attempt_finished = False
        attempt_provider_event_seen = False
        attempt_usage: Any = None
        attempt_response_id = ""
        attempt_response_message: AIMessageChunk | None = None
        attempt_reasoning_parts: list[str] = []
        provider_stream_completed = False
        try:
            wire_payload = _plain_attempt_payload(
                active_provider_messages,
                model=resolved_model,
                use_responses_transport=use_responses_transport,
            )
            attempt_handle = await model_usage_audit.begin_attempt(
                logical_call,
                wire_payload=wire_payload,
                attempt_kind=next_attempt_kind,
                retry_of_attempt_id=previous_attempt_id,
                legacy_compatible=False,
            )
            previous_attempt_id = str(
                getattr(attempt_handle, "attempt_id", "") or previous_attempt_id
            )
            next_attempt_kind = "stream_retry"
            async for chunk in llm.astream(llm_messages):
                attempt_provider_event_seen = True
                provider_event_seen = True
                if isinstance(chunk, AIMessageChunk):
                    try:
                        attempt_response_message = (
                            chunk
                            if attempt_response_message is None
                            else attempt_response_message + chunk
                        )
                    except Exception:  # noqa: BLE001 - cursor capture must not break the answer
                        logger.debug(
                            "plain Responses item aggregation skipped run=%s",
                            run_id,
                            exc_info=True,
                        )
                        attempt_response_message = None
                metadata = getattr(chunk, "response_metadata", None)
                if isinstance(metadata, dict):
                    raw_usage = metadata.get("token_usage") or metadata.get("usage")
                    if isinstance(raw_usage, dict):
                        attempt_usage = dict(raw_usage)
                    attempt_response_id = str(
                        metadata.get("id")
                        or metadata.get("response_id")
                        or attempt_response_id
                    )
                if not attempt_response_id:
                    attempt_response_id = str(getattr(chunk, "id", "") or "")
                if recovery_pending_transport:
                    yield channel.model_connection(
                        "recovered",
                        recovery_pending_transport,
                        attempt=stream_retries or None,
                        max_retries=max_stream_retries or None,
                    )
                    recovery_pending_transport = ""
                for part_type, part_text in _chunk_stream_parts(chunk):
                    if part_type == "reasoning":
                        attempt_reasoning_parts.append(part_text)
                        if not reasoning_buf:
                            reasoning_started_at = time.monotonic()
                        reasoning_buf.append(part_text)
                        if not suppress_retry_text_stream:
                            published_reasoning = True
                            yield channel.message_reasoning_delta(part_text)
                        continue
                    completed = _close_reasoning()
                    if completed and not suppress_retry_text_stream:
                        yield completed
                    full_response += part_text
                    if not suppress_retry_text_stream:
                        published_text += part_text
                        yield channel.message_delta(part_text)
                # 真实用量（若模型/NEWAPI 透传）：langchain usage_metadata.input_tokens
                _um = getattr(chunk, "usage_metadata", None)
                if _um:
                    normalized_chunk_usage = _plain_chunk_usage(_um)
                    if isinstance(normalized_chunk_usage, dict) and isinstance(
                        attempt_usage, dict
                    ):
                        attempt_usage = {**normalized_chunk_usage, **attempt_usage}
                    else:
                        attempt_usage = normalized_chunk_usage
                    if _um.get("input_tokens"):
                        usage_prompt_tokens = int(_um["input_tokens"])

            provider_stream_completed = True

            completed = _close_reasoning()
            if completed and not suppress_retry_text_stream:
                yield completed
            if recovery_pending_transport:
                yield channel.model_connection(
                    "recovered",
                    recovery_pending_transport,
                    attempt=stream_retries or None,
                    max_retries=max_stream_retries or None,
                )
                recovery_pending_transport = ""

            if suppress_retry_text_stream and full_response:
                if full_response.startswith(published_text):
                    suffix = full_response[len(published_text):]
                    if suffix:
                        published_text += suffix
                        yield channel.message_delta(suffix)
                else:
                    logger.info(
                        "模型重连后直答正文前缀发生变化，"
                        "不追加重复 delta；终态以权威全文校正 model=%s",
                        resolved_model,
                    )

            if use_responses_transport:
                transport_committed = True
                responses_fallback_allowed = False
                agent_service.remember_model_responses_capability(
                    resolved_model, newapi_key, True
                )
                await _persist_model_transport(
                    "responses",
                    confirmed=True,
                    source="provider_success",
                )

            # 用真实 prompt_tokens 校准上下文用量指示（§13；拿不到则保留发送前估算值），
            # 并回灌 per-model 校准因子（下一轮估算 × 因子 ≈ 真实）
            if usage_prompt_tokens > 0:
                # 回灌必须用**原始**估算：因子定义为 raw×factor≈actual，若回灌已乘过
                # 因子的值，稳态解会变成 sqrt(actual/raw)（长期欠校正、压缩偏晚）
                record_usage(resolved_model, raw_est_tokens, usage_prompt_tokens)
                from app.services.platform.token_estimator import record_thread_prompt
                record_thread_prompt(thread_id, usage_prompt_tokens)  # 线程级账本（压缩触发下限锚）
                yield channel.context_usage(
                    usage_prompt_tokens, ctx_window,
                    round(min(usage_prompt_tokens / ctx_window, 1.0), 3),
                    kind="actual",
                )

            # 落库前只做不改变 Markdown 结构的内容闸。模型流出的正文是排版事实源：
            # message.delta 已经把原文展示给用户，completed/落库必须保持同一份字节序列，
            # 禁止再按数字、标点猜标题或列表（曾把 82.7 / 2026-07-09 改成假编号）。
            # 否则 pure_qa 弱模型常见「收到。8」落库，前端/矩阵对账都会变机械。
            provider_terminal_response = full_response
            full_response = turn_finalizer.collapse_exact_double_answer(full_response)
            full_response = turn_finalizer.strip_leading_mechanical_ack(full_response)
            full_response = turn_finalizer.scrub_contradictory_completion(full_response)
            full_response = turn_finalizer.scrub_inline_source_markers(full_response)
            # 落库+提交收敛到 turn_finalizer.persist_assistant_turn（2026-07-22）：
            # MySQL 1213 死锁回滚→退避→整轮重放（首轮标题/regenerate 软标记同事务）
            assistant_row = await turn_finalizer.persist_assistant_turn(
                session, thread, thread_id=thread_id, content=full_response,
                run_id=run_id, is_first_turn=is_first_turn, regenerate=regenerate,
                first_message=message,
            )
            assistant_message_id = assistant_row.id
            persisted = True
            native_output_items = (
                _responses_output_items_from_message(attempt_response_message)
                if use_responses_transport
                else []
            )
            assistant_item: dict[str, Any] = {
                "role": "assistant",
                "content": provider_terminal_response,
            }
            provider_reasoning = "".join(attempt_reasoning_parts).strip()
            if provider_reasoning:
                assistant_item["reasoning_content"] = provider_reasoning
            if native_output_items:
                assistant_item["_responses_output_items"] = native_output_items
            commit_bundle = terminal_commit_bundle(
                projection_prepared,
                assistant_item=assistant_item,
            )
            if commit_bundle is not None:
                try:
                    await commit_projection_bundle(
                        commit_bundle,
                        answer=full_response,
                        run_id=str(run_id or ""),
                        display_assistant_message={
                            **{
                                "role": "assistant",
                                "content": getattr(
                                    assistant_row,
                                    "content",
                                    full_response,
                                ),
                            },
                            **(
                                {
                                    "attachments_json": assistant_row.attachments_json,
                                }
                                if getattr(assistant_row, "attachments_json", None)
                                else {}
                            ),
                        },
                    )
                except Exception:  # noqa: BLE001 - transcript commit must remain authoritative
                    logger.warning(
                        "plain terminal projection commit skipped run=%s",
                        run_id,
                        exc_info=True,
                    )
            await model_usage_audit.finish_attempt(
                attempt_handle,
                terminal_status="completed",
                usage=attempt_usage,
                response_id=attempt_response_id,
                provider_event_seen=attempt_provider_event_seen,
                terminal_seen=True,
                committed=True,
            )
            attempt_finished = True
            await model_usage_audit.finish_logical_call(
                logical_call,
                terminal_status="completed",
                selected_attempt_id=str(
                    getattr(attempt_handle, "attempt_id", "") or ""
                ),
                committed=True,
            )
            break
        except (asyncio.CancelledError, GeneratorExit):
            if not attempt_finished:
                await model_usage_audit.finish_attempt(
                    attempt_handle,
                    terminal_status=(
                        "completed" if provider_stream_completed else "cancelled"
                    ),
                    usage=attempt_usage,
                    response_id=attempt_response_id,
                    provider_event_seen=attempt_provider_event_seen,
                    terminal_seen=provider_stream_completed,
                    error_code=("commit_cancelled" if provider_stream_completed else "cancelled"),
                    committed=False,
                )
                attempt_finished = True
            await model_usage_audit.finish_logical_call(
                logical_call,
                terminal_status="cancelled",
                selected_attempt_id=str(
                    getattr(attempt_handle, "attempt_id", "") or ""
                ),
                committed=False,
            )
            # 用户停止生成：把已产出的部分内容落库，避免刷新会话后丢失。外层 session 正随
            # 生成器关闭而回滚，故用独立任务在自有 session 提交——且不能直接 await（当前处于被
            # 取消的上下文，await 会被二次取消打断，写不进去），改为 fire-and-forget 让其跑完。
            # 按 run_id/thread_id 登记（P1-4）：cancel_chat_run 与下一轮写入前据此等它收敛。
            visible_partial = published_text or full_response
            if visible_partial.strip() and not persisted:
                try:
                    env.spawn_partial_persist(run_id, thread_id, visible_partial)
                except Exception:  # noqa: BLE001
                    pass
            raise
        except Exception as exc:  # noqa: BLE001
            if not attempt_finished:
                http_status, error_code, error_detail = _attempt_error_fields(exc)
                await model_usage_audit.finish_attempt(
                    attempt_handle,
                    terminal_status=(
                        "completed" if provider_stream_completed else "failed"
                    ),
                    usage=attempt_usage,
                    response_id=attempt_response_id,
                    provider_event_seen=attempt_provider_event_seen,
                    terminal_seen=provider_stream_completed,
                    http_status=http_status,
                    error_code=(
                        "commit_failed" if provider_stream_completed else error_code
                    ),
                    error_detail=error_detail,
                    committed=False,
                )
                attempt_finished = True
            if provider_stream_completed:
                await model_usage_audit.finish_logical_call(
                    logical_call,
                    terminal_status="not_committed",
                    selected_attempt_id=str(
                        getattr(attempt_handle, "attempt_id", "") or ""
                    ),
                    committed=False,
                )
                raise
            policy_rejection = provider_policy_rejection_from_exception(exc)
            if policy_rejection is not None:
                status_code, policy_code, _policy_message = policy_rejection
                await model_usage_audit.finish_logical_call(
                    logical_call,
                    terminal_status="failed",
                    selected_attempt_id=str(
                        getattr(attempt_handle, "attempt_id", "") or ""
                    ),
                    committed=False,
                )
                raise ModelProviderPolicyRejected(
                    status_code, policy_code, str(exc)
                ) from exc
            # 超窗兜底（§13）：窗口解析可能高估（NEWAPI 渠道上限常小于模型原生窗口）或估算
            # 偏低——上下文被拒且尚未流出任何内容时，强制压缩旧段进摘要后重建 prompt 重试一次。
            if (
                not context_retry_used
                and not published_text
                and not full_response
                and _is_context_length_error(exc)
            ):
                await record_projection_canary_error(
                    projection_prepared,
                    run_id=str(run_id or ""),
                )
                logger.warning("上下文超窗被拒，强制压缩后重试: %s", str(exc)[:200])
                if await context_service.force_compact(
                    thread_id,
                    resolved_model,
                    newapi_key,
                    audit_run_id=str(run_id or ""),
                    audit_root_run_id=audit_root_run_id,
                ):
                    context_retry_used = True
                    next_attempt_kind = "context_retry"
                    summary = await context_service.get_summary(thread_id)
                    _sys_prompt_tokens = _system_prompt_budget_tokens(
                        agents, trusted_skills, selected_knowledge, knowledge_ids, memory_block,
                        skill_catalog_block, summary,
                    )
                    prompt_rows, summary_block, _ = context_service.apply_context_budget(
                        history_rows, summary,
                        hard_cap_tokens=_history_hard_cap_tokens(ctx_window, _sys_prompt_tokens),
                    )
                    retry_parts = split_system_prompt_context(
                        _build_system_prompt(
                            agents,
                            trusted_skills,
                            selected_knowledge,
                            knowledge_ids,
                            memory_block,
                        )
                    )
                    retry_world_state = "\n\n".join(
                        p
                        for p in [
                            retry_parts.world_state,
                            summary_block,
                            skill_catalog_block,
                            kb_pre_context,
                            dynamic_guard,
                        ]
                        if p
                    )
                    rows_for_prompt = (
                        prompt_rows[:-1]
                        if (prompt_rows and prompt_rows[-1].role == "user")
                        else list(prompt_rows)
                    )
                    prompt_parts = retry_parts
                    world_state_prompt = retry_world_state
                    projection_world_state = prompt_parts.as_context_section()
                    projection_world_state.update({
                        key: value
                        for key, value in {
                            "conversation_summary": summary_block,
                            "skill_catalog": skill_catalog_block,
                            "knowledge_context": kb_pre_context,
                            "turn_guard": dynamic_guard,
                        }.items()
                        if value
                    })
                    ordinary_provider_messages = _ordinary_plain_messages(
                        stable_base=prompt_parts.stable_base,
                        rows=rows_for_prompt,
                        current_input=model_input_content,
                        world_state=world_state_prompt,
                    )
                    source_messages = _ordinary_plain_messages(
                        stable_base=prompt_parts.stable_base,
                        rows=rows_for_prompt,
                        current_input=model_input_content,
                        world_state="",
                    )
                    await _prepare_plain_projection(force_reason="compaction")
                    yield channel.context_compacted("上下文超限，已自动整理较早对话后重试")
                    continue

            if (
                use_responses_transport
                and responses_fallback_allowed
                and not provider_event_seen
                and not published_text
                and not full_response
                and not published_reasoning
                and not reasoning_buf
                and responses_exception_is_unsupported(exc)
            ):
                await record_projection_canary_error(
                    projection_prepared,
                    run_id=str(run_id or ""),
                )
                previous_logical_id = str(
                    getattr(logical_call, "logical_call_id", "") or ""
                )
                await model_usage_audit.finish_logical_call(
                    logical_call,
                    terminal_status="failed",
                    selected_attempt_id=str(
                        getattr(attempt_handle, "attempt_id", "") or ""
                    ),
                    committed=False,
                )
                use_responses_transport = False
                responses_fallback_allowed = False
                stream_retries = 0
                await _persist_model_transport(
                    "chat_completions",
                    confirmed=True,
                    source="responses_unsupported",
                )
                await _prepare_plain_projection(force_reason="transport_changed")
                llm = _new_llm()
                logical_call = await _begin_logical(
                    parent_logical_call_id=previous_logical_id,
                )
                previous_attempt_id = ""
                next_attempt_kind = "protocol_fallback"
                reasoning_buf.clear()
                reasoning_started_at = 0.0
                usage_prompt_tokens = 0
                recovery_pending_transport = "chat_completions_fallback"
                yield channel.model_connection(
                    "recovering", "chat_completions_fallback"
                )
                continue

            if model_stream_exception_is_retryable(exc):
                if stream_retries >= max_stream_retries:
                    await model_usage_audit.finish_logical_call(
                        logical_call,
                        terminal_status="failed",
                        selected_attempt_id=str(
                            getattr(attempt_handle, "attempt_id", "") or ""
                        ),
                        committed=False,
                    )
                    yield channel.model_connection(
                        "failed",
                        "stream_retry",
                        attempt=stream_retries,
                        max_retries=max_stream_retries,
                    )
                    raise RuntimeError(
                        f"模型连接连续重试 {max_stream_retries} 次仍未恢复，"
                        "已停止当前执行循环"
                    ) from exc
                stream_retries += 1
                delay = _model_retry_delay(stream_retries)
                logger.warning(
                    "直答模型流连接中断，将重连 %s/%s（%.2fs）: %s",
                    stream_retries,
                    max_stream_retries,
                    delay,
                    str(exc)[:200],
                )
                yield channel.model_connection(
                    "recovering",
                    "stream_retry",
                    attempt=stream_retries,
                    max_retries=max_stream_retries,
                    delay_seconds=delay,
                )
                full_response = ""
                usage_prompt_tokens = 0
                reasoning_buf.clear()
                reasoning_started_at = 0.0
                suppress_retry_text_stream = bool(
                    published_text or published_reasoning
                )
                recovery_pending_transport = "stream_retry"
                if delay > 0:
                    await asyncio.sleep(delay)
                continue
            await model_usage_audit.finish_logical_call(
                logical_call,
                terminal_status="failed",
                selected_attempt_id=str(
                    getattr(attempt_handle, "attempt_id", "") or ""
                ),
                committed=False,
            )
            raise


    env.plain.update(full_response=full_response,
                     assistant_message_id=assistant_message_id,
                     usage_prompt_tokens=usage_prompt_tokens)


async def finish_plain_turn(env):
    """直答收尾(session 块外):推荐兜底→终态 CAS→标题→轮后记忆抽取→done。"""
    channel = env.channel
    message = env.message
    user_context = env.user_context
    resolved_model = env.resolved_model
    newapi_key = env.newapi_key
    run_id = env.run_id
    thread_id = env.thread_id
    user_id = env.user_id
    attachments = env.attachments
    regenerate = env.regenerate
    is_first_turn = env.is_first_turn
    full_response = env.plain["full_response"]
    assistant_message_id = env.plain["assistant_message_id"]

    # V1/协议：终答必须有 message.completed，与工具路径同口径。
    # 缺它时前端/验收矩阵只能拼 commentary+delta，pure_qa 会误显示「收到。」机械前缀。
    if str(full_response or "").strip():
        yield channel.message_completed(full_response, assistant_message_id)
    async for terminal_frame in turn_finalizer.finalize_terminal(
        channel,
        run_id,
        {},
        assistant_message_id,
        full_response,
        full_response or "模型未返回可用回答",
    ):
        yield terminal_frame

    if is_first_turn and not regenerate and full_response.strip():
        # spawn_bg 持引用托管（裸 create_task 可能被 GC 静默丢弃，B4 教训）
        env.spawn_bg(
            turn_finalizer.generate_title(
                thread_id,
                message,
                resolved_model,
                newapi_key,
                attachments=attachments,
                run_id=str(run_id or ""),
                root_run_id=str(getattr(env, "root_run_id", "") or ""),
            )
        )
    if full_response.strip() and env.route != "direct_answer":
        # _spawn_bg 持引用托管（裸 create_task 可能被 GC 静默丢弃，B4 教训）
        # v3.0：并入技能执行摘要（产物结论定向进记忆）
        _skill_summary = ""
        try:
            from app.services.tasks import snapshot_service
            _skill_summary = await snapshot_service.collect_skill_work_lines(thread_id)
        except Exception:  # noqa: BLE001
            pass
        env.spawn_bg(memory_service.extract_and_store(
            user_id=user_id, thread_id=thread_id,
            conversation_text=f"用户：{message}\n助手：{full_response}",
            user_text=message,
            model=resolved_model, api_key=newapi_key, run_id=run_id,
            skill_summary=_skill_summary,
        ))
    # 会话超阈值时异步压缩旧段为摘要（§13.3 Compaction，失败静默）
    pass  # 压缩改为发送前同步 ensure_compacted（§13），不再事后异步触发

    yield channel.done()
