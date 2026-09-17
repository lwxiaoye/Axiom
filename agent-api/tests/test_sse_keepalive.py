"""SSE keepalive 包装器单测（app/api/sse_utils.py）。纯 asyncio，无外部依赖。

覆盖：慢生成器插心跳且原帧顺序不变；快生成器零心跳；超时不 cancel 底层
__anext__（慢帧跨多个心跳后完整到达）；StopAsyncIteration/异常透传；
aclose/消费方取消时底层生成器 finally 照常执行（断开语义不变）。
"""
import asyncio

import pytest

from app.api.sse_utils import SSE_HEADERS, SSE_KEEPALIVE_FRAME, with_sse_keepalive

pytestmark = pytest.mark.asyncio


async def _collect(gen):
    return [frame async for frame in gen]


async def test_slow_generator_gets_pings_order_preserved():
    async def slow():
        yield "data: a\n\n"
        await asyncio.sleep(0.25)
        yield "data: b\n\n"
        await asyncio.sleep(0.25)
        yield "data: c\n\n"

    out = await _collect(with_sse_keepalive(slow(), interval=0.05))
    # 原帧顺序与内容零改动
    assert [f for f in out if f != SSE_KEEPALIVE_FRAME] == ["data: a\n\n", "data: b\n\n", "data: c\n\n"]
    # 每个 0.25s 空闲期都至少插入了心跳
    assert out.count(SSE_KEEPALIVE_FRAME) >= 2
    # 心跳出现在帧间隙：a 之后、b 之前有心跳
    assert out[0] == "data: a\n\n"
    assert out.index(SSE_KEEPALIVE_FRAME) < out.index("data: b\n\n")


async def test_fast_generator_no_pings():
    async def fast():
        for i in range(20):
            yield f"data: {i}\n\n"

    out = await _collect(with_sse_keepalive(fast(), interval=0.5))
    assert out == [f"data: {i}\n\n" for i in range(20)]


async def test_pending_anext_not_cancelled_across_multiple_pings():
    """关键行为：超时后保留同一个 pending __anext__ 继续等。
    若每次超时重建/cancel 任务，async generator 会在内部 await 点被
    CancelledError 杀死，慢帧永远到不了。"""
    async def one_slow_frame():
        await asyncio.sleep(0.3)
        yield "data: slow\n\n"

    out = await _collect(with_sse_keepalive(one_slow_frame(), interval=0.05))
    assert out[-1] == "data: slow\n\n"
    assert out.count(SSE_KEEPALIVE_FRAME) >= 3
    assert set(out[:-1]) == {SSE_KEEPALIVE_FRAME}


async def test_normal_end_stops_iteration():
    async def two():
        yield "data: 1\n\n"
        yield "data: 2\n\n"

    gen = with_sse_keepalive(two(), interval=0.5)
    assert await _collect(gen) == ["data: 1\n\n", "data: 2\n\n"]
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


async def test_underlying_exception_passthrough():
    async def boom():
        yield "data: ok\n\n"
        raise ValueError("boom")

    gen = with_sse_keepalive(boom(), interval=0.5)
    assert await gen.__anext__() == "data: ok\n\n"
    with pytest.raises(ValueError, match="boom"):
        await gen.__anext__()


async def test_aclose_closes_underlying_generator():
    closed = asyncio.Event()

    async def src():
        try:
            yield "data: 1\n\n"
            await asyncio.sleep(10)
            yield "data: never\n\n"
        finally:
            closed.set()

    gen = with_sse_keepalive(src(), interval=0.05)
    assert await gen.__anext__() == "data: 1\n\n"
    # 等到一个心跳，确认此刻底层 __anext__ 正 pending（挂在 10s sleep 上）
    assert await gen.__anext__() == SSE_KEEPALIVE_FRAME
    await gen.aclose()
    assert closed.is_set()


async def test_consumer_cancel_closes_underlying_generator():
    closed = asyncio.Event()

    async def src():
        try:
            await asyncio.sleep(10)
            yield "data: never\n\n"
        finally:
            closed.set()

    async def consume():
        async for _ in with_sse_keepalive(src(), interval=0.05):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


async def test_headers_constant():
    assert SSE_HEADERS == {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    # SSE 注释帧：冒号开头 + 空行结尾，客户端按规范忽略
    assert SSE_KEEPALIVE_FRAME.startswith(":") and SSE_KEEPALIVE_FRAME.endswith("\n\n")
