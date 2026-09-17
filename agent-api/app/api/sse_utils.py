"""SSE 传输层加固（防代理缓冲/空闲超时掐断连接）。

- SSE_HEADERS：流式端点统一响应头。``Cache-Control: no-cache`` 防中间层缓存；
  ``X-Accel-Buffering: no`` 让 nginx（及认这个头的网关）对本响应关闭代理缓冲——
  对仓库外改不到配置的上游网关一跳，这是唯一的穿透手段。
- with_sse_keepalive：空闲期心跳。模型思考/首 token 等待期业务生成器零字节
  下发，代理按空闲超时（nginx proxy_read_timeout 默认 60s）会掐断连接；
  每 interval 秒补一帧 ``: ping`` SSE 注释帧。注释帧以冒号开头，SSE 规范要求
  客户端忽略；前端解析器只认 ``data:`` 开头的行（agentApi.ts 已核实），
  对业务零影响。
"""

import asyncio
from typing import AsyncGenerator, AsyncIterator, Optional

# 供 StreamingResponse(headers=...) 使用，与 media_type="text/event-stream" 搭配
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}

# SSE 注释帧（以冒号开头），仅用于保活，客户端按规范忽略
SSE_KEEPALIVE_FRAME = ": ping\n\n"


async def with_sse_keepalive(
    gen: AsyncIterator[str],
    interval: float = 15.0,
) -> AsyncGenerator[str, None]:
    """包装已格式化的 SSE 字符串生成器：下一帧等待超过 interval 秒时插入心跳注释帧。

    关键约束：超时后绝不能 cancel 底层 ``__anext__`` 任务——async generator 被
    cancel 会在其内部 await 点抛 CancelledError 直接杀死生成器；必须保留同一个
    pending task 继续等待，原帧顺序与内容零改动透传。

    结束/断开语义：
    - 底层 StopAsyncIteration → 正常结束；
    - 底层抛异常 → 原样透传给消费方；
    - 消费方 aclose/取消（客户端断开）→ finally 先取消并等待 pending 的
      ``__anext__``，再 aclose 底层生成器，保证其 finally（如取消订阅）照常执行。
    """
    it = gen.__aiter__()
    next_task: Optional[asyncio.Task] = None
    try:
        while True:
            if next_task is None:
                next_task = asyncio.ensure_future(it.__anext__())
            done, _pending = await asyncio.wait({next_task}, timeout=interval)
            if not done:
                # 空闲超时：发心跳后继续等同一个 pending task（不重建、不取消）
                yield SSE_KEEPALIVE_FRAME
                continue
            next_task = None
            try:
                item = done.pop().result()
            except StopAsyncIteration:
                return
            yield item
    finally:
        # 客户端断开（GeneratorExit）或上层取消时走到这里：
        # 先让 pending 的 __anext__ 完整走完取消，再关闭底层生成器。
        if next_task is not None and not next_task.done():
            next_task.cancel()
            try:
                await next_task
            except BaseException:
                pass  # 取消回声/底层收尾异常不掩盖原始退出原因
        aclose = getattr(gen, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except BaseException:
                pass
