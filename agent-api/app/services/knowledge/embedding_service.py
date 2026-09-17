import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional, Union

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session

logger = logging.getLogger(__name__)

SEM = asyncio.Semaphore(8)


@dataclass
class EmbeddingConfig:
    model: str
    api_key: str
    base_url: str
    dimension: Optional[int] = None
    test_status: Optional[str] = None


async def get_active_embedding_config() -> Optional[EmbeddingConfig]:
    """从数据库读取当前启用的平台 embedding 配置。"""
    from app.models import EmbeddingModel
    async with async_session() as session:
        row = (
            await session.execute(
                select(EmbeddingModel)
                .where(EmbeddingModel.is_active == 1, EmbeddingModel.enabled == 1)
                .order_by(EmbeddingModel.id.desc())
            )
        ).scalars().first()
        if not row or not row.api_key or not row.base_url:
            return None
        return EmbeddingConfig(
            model=row.model_id,
            api_key=row.api_key,
            base_url=row.base_url,
            dimension=row.dimension,
            test_status=row.test_status,
        )


async def test_embedding_config(model: str, api_key: str, base_url: str) -> dict:
    """测试 embedding 配置连通性，返回 dimension 和状态。"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "input": "智能体检索测试"},
            )
            resp.raise_for_status()
            vector = resp.json()["data"][0]["embedding"]
            return {"status": "success", "dimension": len(vector), "message": "连接成功"}
    except Exception as e:
        return {"status": "failed", "dimension": None, "message": str(e)[:200]}


async def embed_query(
    text: str,
    model: str = None,
    user_key: str = None,
    config: Optional[EmbeddingConfig] = None,
    audit_run_id: str = "",
    audit_thread_id: str = "",
    audit_root_run_id: str = "",
    audit_parent_tool_call_id: str = "",
    audit_purpose_detail: str = "embedding",
) -> List[float]:
    """单条 Embedding。优先用 config（平台级），否则用 user_key。"""
    if config:
        url = f"{config.base_url.rstrip('/')}/embeddings"
        key = config.api_key
        model = config.model
    else:
        url = f"{settings.NEWAPI_BASE_URL}/embeddings"
        key = user_key
        if not key:
            raise ValueError("未提供 user_key 且无平台 embedding 配置")

    # Tool-internal embeddings inherit the physical tool identity when the caller did not
    # already provide the enclosing Run.  Turn preparation/background services pass it
    # explicitly because they execute outside a model-visible tool task.
    if not audit_run_id:
        try:
            from app.services.chat.tools.base import current_tool_context

            tool_context = current_tool_context()
            if tool_context is not None:
                audit_run_id = str(tool_context.run_id or "")
                audit_thread_id = str(tool_context.thread_id or "")
                audit_parent_tool_call_id = str(tool_context.call_id or "")
        except Exception:  # noqa: BLE001
            pass

    from app.services.agent_harness import model_usage_audit

    wire_payload = {"model": model, "input": text}
    logical = None
    if audit_run_id:
        logical = await model_usage_audit.begin_logical_call(
            run_id=audit_run_id,
            root_run_id=audit_root_run_id,
            thread_id=audit_thread_id,
            parent_tool_call_id=audit_parent_tool_call_id,
            model=str(model or ""),
            transport="embeddings",
            endpoint_family="embeddings",
            purpose="tool_internal",
            purpose_detail=str(audit_purpose_detail or "embedding")[:200],
            scope_key=f"tool_internal:embedding:{str(audit_purpose_detail or 'embedding')}"[:255],
            provider_api_key=key,
        )

    async with SEM:
        previous_attempt_id = ""
        for attempt_index in range(3):
            audit_attempt = await model_usage_audit.begin_attempt(
                logical,
                wire_payload=wire_payload,
                attempt_kind="initial" if attempt_index == 0 else "network_retry",
                retry_of_attempt_id=previous_attempt_id,
                legacy_compatible=False,
            )
            if audit_attempt is not None:
                previous_attempt_id = audit_attempt.attempt_id
            response_payload = {}
            resp = None
            attempt_finished = False
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        url,
                        headers={"Authorization": f"Bearer {key}"},
                        json=wire_payload,
                    )
                try:
                    response_payload = resp.json()
                except Exception:  # noqa: BLE001
                    response_payload = {}
                if resp.status_code >= 400:
                    await model_usage_audit.finish_attempt(
                        audit_attempt,
                        terminal_status="failed",
                        usage=model_usage_audit.provider_usage_from_response(response_payload),
                        response_id=model_usage_audit.provider_response_id(response_payload),
                        provider_event_seen=True,
                        terminal_seen=True,
                        http_status=resp.status_code,
                        committed=False,
                    )
                    attempt_finished = True
                    retryable = int(resp.status_code) in {408, 425} or 500 <= int(resp.status_code) <= 599
                    if retryable and attempt_index < 2:
                        await asyncio.sleep(0.5 * (attempt_index + 1))
                        continue
                    await model_usage_audit.finish_logical_call(
                        logical, terminal_status="failed", committed=False,
                    )
                    resp.raise_for_status()

                vector = response_payload["data"][0]["embedding"]
                await model_usage_audit.finish_attempt(
                    audit_attempt,
                    terminal_status="completed",
                    usage=model_usage_audit.provider_usage_from_response(response_payload),
                    response_id=model_usage_audit.provider_response_id(response_payload),
                    provider_event_seen=True,
                    terminal_seen=True,
                    http_status=resp.status_code,
                    committed=True,
                )
                attempt_finished = True
                await model_usage_audit.finish_logical_call(
                    logical,
                    terminal_status="completed",
                    selected_attempt_id=(audit_attempt.attempt_id if audit_attempt else ""),
                    committed=True,
                )
                return vector
            except asyncio.CancelledError:
                if not attempt_finished:
                    await model_usage_audit.finish_attempt(
                        audit_attempt,
                        terminal_status="cancelled",
                        usage=model_usage_audit.provider_usage_from_response(response_payload),
                        provider_event_seen=resp is not None,
                        terminal_seen=resp is not None,
                        http_status=getattr(resp, "status_code", None),
                        committed=False,
                    )
                await model_usage_audit.finish_logical_call(
                    logical, terminal_status="cancelled", committed=False,
                )
                raise
            except Exception as e:
                if attempt_finished:
                    logger.warning(
                        "Embedding failed after %s attempt(s): %s",
                        attempt_index + 1,
                        e,
                    )
                    raise
                if not attempt_finished:
                    await model_usage_audit.finish_attempt(
                        audit_attempt,
                        terminal_status="failed",
                        usage=model_usage_audit.provider_usage_from_response(response_payload),
                        provider_event_seen=resp is not None,
                        terminal_seen=resp is not None,
                        http_status=getattr(resp, "status_code", None),
                        error_code=type(e).__name__,
                        committed=False,
                    )
                # Only transport failures and 5xx responses are retryable.  Authentication,
                # quota/429, and all other 4xx errors terminate the logical call immediately.
                status_code = int(getattr(resp, "status_code", 0) or 0)
                retryable = (
                    resp is None
                    or status_code in {408, 425}
                    or 500 <= status_code <= 599
                )
                if attempt_index == 2 or not retryable:
                    await model_usage_audit.finish_logical_call(
                        logical, terminal_status="failed", committed=False,
                    )
                    logger.warning(
                        "Embedding failed after %s attempt(s): %s",
                        attempt_index + 1,
                        e,
                    )
                    raise
                await asyncio.sleep(0.5 * (attempt_index + 1))


async def embed_texts(
    texts: List[str],
    model: str = None,
    user_key: str = None,
    config: Optional[EmbeddingConfig] = None,
    return_exceptions: bool = False,
    audit_run_id: str = "",
    audit_thread_id: str = "",
    audit_root_run_id: str = "",
    audit_parent_tool_call_id: str = "",
    audit_purpose_detail: str = "embedding_batch",
) -> List[Union[List[float], Exception]]:
    """批量 Embedding，并发上限 8，可选择保留单条失败。"""
    tasks = [
        embed_query(
            t,
            model=model,
            user_key=user_key,
            config=config,
            audit_run_id=audit_run_id,
            audit_thread_id=audit_thread_id,
            audit_root_run_id=audit_root_run_id,
            audit_parent_tool_call_id=audit_parent_tool_call_id,
            audit_purpose_detail=audit_purpose_detail,
        )
        for t in texts
    ]
    return await asyncio.gather(*tasks, return_exceptions=return_exceptions)
