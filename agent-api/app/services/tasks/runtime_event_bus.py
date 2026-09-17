"""PostgreSQL LISTEN/NOTIFY for durable wakeups and ephemeral live-only frames.

Events remain durable in ``agent_run_events``.  Notifications carry only a Run id and are an
optimization; listeners always re-read by sequence and fall back to polling on disconnect.

``EPHEMERAL_CHANNEL`` carries complete SSE frames that are never written to a table: public
deltas, heartbeats, progress, and live reasoning. Frames larger than the PG NOTIFY limit are
split into ordered shards and reassembled by the listener.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from contextlib import suppress

import psycopg

from app.core import runtime_db


logger = logging.getLogger(__name__)
CHANNEL = "agent_harness_run_events"
EPHEMERAL_CHANNEL = "agent_harness_ephemeral"
JOB_CHANNEL = "agent_harness_jobs"
EPHEMERAL_NOTIFY_LIMIT = 7900
_waiters: dict[str, set[asyncio.Event]] = defaultdict(set)
_job_waiters: set[asyncio.Event] = set()
_ephemeral_subscribers: dict[str, set[asyncio.Queue[str]]] = defaultdict(set)
_publisher_conn = None
_publisher_lock = asyncio.Lock()
_slow_subscriber_drops = 0
_frag_buf: dict[str, dict] = {}


def _connection_url() -> str:
    url = str(runtime_db._resolve_url() or "")  # noqa: SLF001 - single runtime URL fact source
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


async def wait_for_run_event(run_id: str, *, timeout: float = 0.1) -> bool:
    event = asyncio.Event()
    _waiters[run_id].add(event)
    try:
        # 上限 0.15s：与 subscribe_run 兜底轮询对齐，避免把跨进程正文帧攒半秒再喷
        await asyncio.wait_for(event.wait(), timeout=max(0.05, min(float(timeout), 0.15)))
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        waiters = _waiters.get(run_id)
        if waiters is not None:
            waiters.discard(event)
            if not waiters:
                _waiters.pop(run_id, None)


def notify_local(run_id: str) -> None:
    for event in tuple(_waiters.get(str(run_id), ())):
        event.set()


def notify_job_local() -> None:
    """Wake idle workers in this process; PG LISTEN covers the cross-process path."""
    for event in tuple(_job_waiters):
        event.set()


async def wait_for_job(*, timeout: float = 1.0) -> bool:
    event = asyncio.Event()
    _job_waiters.add(event)
    try:
        await asyncio.wait_for(event.wait(), timeout=max(0.05, float(timeout)))
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        _job_waiters.discard(event)


def register_ephemeral_subscriber(run_id: str, queue: asyncio.Queue[str]) -> None:
    """注册当前连接的瞬时公开帧队列；注册前与断线期的帧不可回放。"""
    _ephemeral_subscribers[str(run_id)].add(queue)


def unregister_ephemeral_subscriber(run_id: str, queue: asyncio.Queue[str]) -> None:
    subscribers = _ephemeral_subscribers.get(str(run_id))
    if subscribers is None:
        return
    subscribers.discard(queue)
    if not subscribers:
        _ephemeral_subscribers.pop(str(run_id), None)


def notify_ephemeral(run_id: str, payload: str) -> None:
    """只投递给当下活订阅；慢订阅时允许丢瞬时帧，不影响持久事件。"""
    global _slow_subscriber_drops
    for queue in tuple(_ephemeral_subscribers.get(str(run_id), ())):
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            _slow_subscriber_drops += 1
            logger.warning(
                "drop ephemeral frame for slow subscriber run=%s dropped=%s",
                run_id, _slow_subscriber_drops,
            )


def _utf8_chunks(text: str, size: int) -> list[str]:
    raw = text.encode("utf-8")
    chunks: list[str] = []
    i = 0
    while i < len(raw):
        end = min(i + size, len(raw))
        while end > i:
            try:
                chunks.append(raw[i:end].decode("utf-8"))
                break
            except UnicodeDecodeError:
                end -= 1
        else:
            raise ValueError("cannot split utf-8 payload")
        i = end
    return chunks or [""]


def build_ephemeral_envelopes(run_id: str, payload: str) -> list[str]:
    """Pack one SSE frame into one or more PG NOTIFY envelopes under the 8KB cap."""
    rid = str(run_id)
    body = str(payload or "")
    envelope = json.dumps(
        {"run_id": rid, "payload": body},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(envelope.encode("utf-8")) < EPHEMERAL_NOTIFY_LIMIT:
        return [envelope]
    shard_id = uuid.uuid4().hex
    overhead = 96 + len(rid)
    chunk_size = max(256, EPHEMERAL_NOTIFY_LIMIT - overhead)
    parts = _utf8_chunks(body, chunk_size)
    total = len(parts)
    return [
        json.dumps(
            {"run_id": rid, "id": shard_id, "i": index, "n": total, "part": part},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for index, part in enumerate(parts)
    ]


def ingest_ephemeral_notification(raw: str) -> tuple[str, str] | None:
    """Return ``(run_id, payload)`` when a complete frame is ready."""
    data = json.loads(raw)
    run_id = str(data.get("run_id") or "")
    shard_id = str(data.get("id") or "")
    if shard_id and "part" in data:
        total = int(data.get("n") or 0)
        index = int(data.get("i") or 0)
        buf = _frag_buf.setdefault(shard_id, {"n": total, "run_id": run_id, "parts": {}})
        buf["parts"][index] = str(data.get("part") or "")
        if total > 0 and len(buf["parts"]) >= total:
            payload = "".join(buf["parts"][i] for i in range(total))
            _frag_buf.pop(shard_id, None)
            return run_id, payload
        return None
    return run_id, str(data.get("payload") or "")


async def publish_ephemeral_payload(run_id: str, payload: str) -> None:
    """跨进程发布一帧不落库 SSE；超限帧分片下发，连接断开时重连一次。"""
    global _publisher_conn
    envelopes = build_ephemeral_envelopes(run_id, payload)
    async with _publisher_lock:
        for envelope in envelopes:
            for attempt in range(2):
                try:
                    if _publisher_conn is None or _publisher_conn.closed:
                        _publisher_conn = await psycopg.AsyncConnection.connect(
                            _connection_url(), autocommit=True,
                        )
                    await _publisher_conn.execute(
                        f"SELECT pg_notify('{EPHEMERAL_CHANNEL}', %s)", (envelope,),
                    )
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "ephemeral publish failed run=%s attempt=%s: %s",
                        run_id, attempt + 1, exc,
                    )
                    if _publisher_conn is not None:
                        with suppress(Exception):
                            await _publisher_conn.close()
                    _publisher_conn = None
            else:
                return


async def run_job_listener() -> None:
    """Worker-only LISTEN for job enqueue; does not consume ephemeral frames."""
    while True:
        conn = None
        try:
            url = _connection_url()
            if not url:
                await asyncio.sleep(1.0)
                continue
            conn = await psycopg.AsyncConnection.connect(url, autocommit=True)
            await conn.execute(f"LISTEN {JOB_CHANNEL}")
            async for notification in conn.notifies():
                if notification.channel == JOB_CHANNEL:
                    notify_job_local()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Job LISTEN disconnected; polling remains active: %s", exc)
            await asyncio.sleep(1.0)
        finally:
            if conn is not None:
                with suppress(Exception):
                    await conn.close()


async def run_listener() -> None:
    """Keep LISTEN active for this API process; reconnect forever on transient PG failures."""
    while True:
        conn = None
        try:
            url = _connection_url()
            if not url:
                await asyncio.sleep(1.0)
                continue
            conn = await psycopg.AsyncConnection.connect(url, autocommit=True)
            await conn.execute(f"LISTEN {CHANNEL}")
            await conn.execute(f"LISTEN {EPHEMERAL_CHANNEL}")
            async for notification in conn.notifies():
                if notification.channel == CHANNEL:
                    notify_local(notification.payload)
                    continue
                if notification.channel != EPHEMERAL_CHANNEL:
                    continue
                try:
                    assembled = ingest_ephemeral_notification(notification.payload)
                    if assembled is None:
                        continue
                    notify_ephemeral(assembled[0], assembled[1])
                except Exception:  # noqa: BLE001
                    logger.debug("invalid ephemeral notification", exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Runtime event LISTEN disconnected; polling remains active: %s", exc)
            await asyncio.sleep(1.0)
        finally:
            if conn is not None:
                with suppress(Exception):
                    await conn.close()
