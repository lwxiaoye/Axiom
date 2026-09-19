import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional, Union

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session
from app.services.connectors.crypto import ConnectorCryptoError, decrypt_secret, encrypt_secret, is_cipher_text

logger = logging.getLogger(__name__)

SEM = asyncio.Semaphore(8)

# ---------------------------------------------------------------------------
# ai_embedding_model.api_key 的密文存取
#
# 对话模型（platform/model_connection）与重排模型（rerank_service）的密钥都是 Fernet 密文
# 入库，embedding 曾是唯一明文的例外：一条 `SELECT api_key FROM ai_embedding_model` 或一份
# 库备份就能拿走线上 DashScope key。这里补齐：不加新列，复用 api_key 列存密文，读取处解密，
# 存量明文由启动迁移（main._encrypt_embedding_keys）原地加密。
#
# 判定「这个值是不是密文」不能只看前缀：Fernet token 的版本字节固定为 0x80，urlsafe base64
# 后恒以 "gAAAA" 开头，而 DashScope/OpenAI 明文 key 以 "sk-" 开头，二者确实不会混淆；但
# 换过 CONNECTOR_SECRET_KEY 后的旧密文前缀相同却解不开——若只看前缀就会把它当有效密文送去
# 当 Bearer，或在迁移里把它再加密一层彻底毁掉。所以「密文」= 前缀匹配 **且** 当前密钥能解开；
# 前缀匹配但解不开的单独归为「解不开」，读取当作无 key、迁移跳过并告警，让管理员重填覆盖。
# ---------------------------------------------------------------------------
_CIPHER_PREFIX = "gAAAA"

KEY_PLAIN = "plain"                  # 明文（尚未迁移）
KEY_CIPHER = "cipher"                # 当前密钥能解开的 Fernet 密文
KEY_UNDECRYPTABLE = "undecryptable"  # 长得像密文但解不开（换过密钥/被篡改）

# 「明文尚未迁移」「密文解不开」的告警按 (类别, 行 id) 只报一次：每次检索都刷一条会淹没日志
_key_warned: set = set()


def _classify_key(value: Optional[str]) -> str:
    """把库里的 api_key 值归为 KEY_PLAIN / KEY_CIPHER / KEY_UNDECRYPTABLE 三类之一。"""
    text = str(value or "")
    if not text.startswith(_CIPHER_PREFIX):
        return KEY_PLAIN
    try:
        decrypt_secret(text)
    except ConnectorCryptoError:
        return KEY_UNDECRYPTABLE
    return KEY_CIPHER


def _is_cipher(value: Optional[str]) -> bool:
    """是否为当前密钥可解的 Fernet 密文（前缀 + 真正解一次，见模块头注释）。

    判据本体在 connectors.crypto.is_cipher_text：平台功能配置（web_search / ocr 的密钥字段）
    也要同一判据，抽到公共处避免它反过来 import 本模块形成循环依赖。
    """
    return is_cipher_text(value)


def _warn_once(kind: str, row_id, message: str, *args) -> None:
    marker = (kind, row_id)
    if marker in _key_warned:
        return
    _key_warned.add(marker)
    logger.warning(message, *args)


def _read_key(row) -> str:
    """读 EmbeddingModel 行的 api_key 明文。

    密文 → 解密；明文（启动迁移尚未跑过/失败）→ 原样返回并告警一次，保证迁移前后检索不中断；
    解不开的伪密文 → 返回空串当作「未配置」：把 Fernet token 当 Bearer 送出去只会换来一个
    让人误以为 key 错了的 401，不如让页面显示无 key，管理员重填即可覆盖。
    """
    value = str(getattr(row, "api_key", None) or "")
    if not value:
        return ""
    kind = _classify_key(value)
    if kind == KEY_CIPHER:
        return decrypt_secret(value)
    row_id = getattr(row, "id", None)
    if kind == KEY_UNDECRYPTABLE:
        _warn_once(
            kind, row_id,
            "ai_embedding_model id=%s 的 api_key 密文无法解密（CONNECTOR_SECRET_KEY 可能已更换），"
            "按未配置处理；请在管理页重新填写 API Key", row_id,
        )
        return ""
    _warn_once(
        kind, row_id,
        "ai_embedding_model id=%s 的 api_key 仍是明文（启动迁移尚未执行），本次按明文使用", row_id,
    )
    return value


def _api_key_column_length() -> int:
    from app.models import EmbeddingModel
    return int(EmbeddingModel.__table__.c.api_key.type.length or 0)


def _store_key(plain: str) -> str:
    """把明文 api_key 变成可入库的 Fernet 密文。

    密文比明文长约 60%（版本+时间戳+IV+HMAC 再 base64），列宽 VARCHAR(512) 能装下约 300 字符
    的明文；超长时 MySQL 非严格模式会静默截断成一个永远解不开的值，这里先拦下来。
    """
    if not plain:
        raise ValueError("API Key 为空")
    cipher = encrypt_secret(plain)
    limit = _api_key_column_length()
    if limit and len(cipher) > limit:
        raise ValueError(f"API Key 过长（加密后 {len(cipher)} 字符，列宽 {limit}）")
    return cipher


def encrypt_plaintext_keys(rows) -> dict:
    """启动迁移的核心：把 rows 里仍是明文的 api_key 原地改写成密文，返回各类计数。

    只改对象属性、不碰 session，调用方决定何时 commit——这样既能在 main 的 lifespan 里跑真库，
    也能拿一列内存对象直接测。幂等：已是密文的行原样不动，第二次跑 migrated=0。
    解不开的伪密文与加密失败（超长等）的行只告警不覆盖：写成空或再加密一层都会让管理员
    连「原来填过什么」的线索都没了，留着让他在页面重填一次覆盖即可。
    """
    stats = {"migrated": 0, "already": 0, "skipped": 0, "empty": 0}
    for row in rows:
        value = str(getattr(row, "api_key", None) or "")
        row_id = getattr(row, "id", None)
        if not value:
            stats["empty"] += 1
            continue
        kind = _classify_key(value)
        if kind == KEY_CIPHER:
            stats["already"] += 1
            continue
        if kind == KEY_UNDECRYPTABLE:
            logger.warning(
                "ai_embedding_model id=%s 的 api_key 像密文但无法解密（CONNECTOR_SECRET_KEY 可能已更换），"
                "迁移跳过不覆盖；请在管理页重新填写 API Key", row_id,
            )
            stats["skipped"] += 1
            continue
        try:
            row.api_key = _store_key(value)
        except (ValueError, ConnectorCryptoError) as exc:
            logger.warning("ai_embedding_model id=%s 的 api_key 加密失败，迁移跳过：%s", row_id, exc)
            stats["skipped"] += 1
            continue
        stats["migrated"] += 1
    return stats


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
        # 库里是 Fernet 密文（迁移前可能仍是明文），这里解成明文交给调用方；解不开视同未配置
        api_key = _read_key(row)
        if not api_key:
            return None
        return EmbeddingConfig(
            model=row.model_id,
            api_key=api_key,
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
