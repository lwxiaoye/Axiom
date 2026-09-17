"""沙箱并发闸不泄漏名额的定向测试（2026-07-27）。

历史隐患（`sandbox_executor` 自己的注释点名过）：`await asyncio.wait_for(sem.acquire(), T)` 是
asyncio 的已知陷阱——超时取消与「内部已拿到名额」竞争时，Semaphore 可能**已经被获取**而
调用方收到 TimeoutError，那个名额就永久丢了。而超时**只在满载时发生**，于是越忙漏得越快，
漏满 SKILL_SANDBOX_MAX_CONCURRENT 就永久「沙箱繁忙」直到重启。

修复口径：`_acquire_with_timeout` 用 wait + cancel + 兜底 release —— cancel 之后仍 await 一次
那个 task，若其实已经成功就立刻把名额还回去。本文件构造那个竞态来证明名额没丢。
"""
import asyncio

import pytest

from app.core.config import settings
from app.services.sandbox import sandbox_executor
from app.services.sandbox.sandbox_executor import _acquire_with_timeout, _get_semaphore


@pytest.mark.asyncio
async def test_acquire_returns_true_when_slot_free():
    sem = asyncio.Semaphore(1)
    assert await _acquire_with_timeout(sem, 1.0) is True
    assert sem.locked() is True


@pytest.mark.asyncio
async def test_timeout_returns_false_and_leaks_nothing():
    """名额被别人占着 → 超时返回 False；占用者释放后名额必须完好可取。"""
    sem = asyncio.Semaphore(1)
    await sem.acquire()                                   # 占满
    assert await _acquire_with_timeout(sem, 0.05) is False  # 排队超时
    sem.release()                                          # 占用者归还
    # 关键断言：如果超时路径漏了名额，这里就取不到
    assert await _acquire_with_timeout(sem, 0.5) is True


@pytest.mark.asyncio
async def test_race_between_timeout_and_acquire_does_not_leak():
    """构造「超时与获取同时发生」的竞态：占用者在超时线附近释放。

    旧写法在这个时序下会出现「调用方收到超时、名额却已被拿走」——名额永久丢失。
    """
    limit = 2
    sem = asyncio.Semaphore(limit)
    holders = [await sem.acquire() for _ in range(limit)] and None  # 占满两个
    assert sem.locked() is True

    async def release_at_deadline():
        # 恰好在超时时刻附近归还，最大化撞上竞态窗口
        await asyncio.sleep(0.05)
        sem.release()
        sem.release()

    releaser = asyncio.create_task(release_at_deadline())
    # 一批等待者同时排队、同时超时
    results = await asyncio.gather(*[_acquire_with_timeout(sem, 0.05) for _ in range(6)])
    await releaser

    # 拿到名额的那些要还回来（模拟正常 finally）
    for got in results:
        if got:
            sem.release()

    # 无论上面谁超时谁成功，容量必须完好：应该能连续取满 limit 个
    taken = 0
    for _ in range(limit):
        if await _acquire_with_timeout(sem, 0.5):
            taken += 1
    assert taken == limit, f"名额泄漏了：只取到 {taken}/{limit}"


@pytest.mark.asyncio
async def test_sustained_timeouts_do_not_exhaust_capacity():
    """满载下反复超时 30 次——旧写法会逐次漏掉名额，最终永久繁忙。"""
    limit = 2
    sem = asyncio.Semaphore(limit)
    await sem.acquire()
    await sem.acquire()

    for _ in range(30):
        assert await _acquire_with_timeout(sem, 0.01) is False

    sem.release()
    sem.release()
    assert await _acquire_with_timeout(sem, 0.5) is True
    assert await _acquire_with_timeout(sem, 0.5) is True


def test_semaphore_rebuilt_for_new_event_loop(monkeypatch):
    """绑在已关闭事件循环上的 Semaphore，其等待者永远唤不醒 = 永久「沙箱繁忙」。

    uvicorn --reload 和测试各自建 loop，所以换 loop 必须重建。
    """
    monkeypatch.setattr(settings, "SKILL_SANDBOX_MAX_CONCURRENT", 3, raising=False)
    sandbox_executor._sem = None
    sandbox_executor._sem_loop = None
    sandbox_executor._sem_limit = 0

    async def grab():
        return _get_semaphore()

    loop_a = asyncio.new_event_loop()
    try:
        sem_a = loop_a.run_until_complete(grab())
    finally:
        loop_a.close()

    loop_b = asyncio.new_event_loop()
    try:
        sem_b = loop_b.run_until_complete(grab())
    finally:
        loop_b.close()

    assert sem_a is not sem_b, "换事件循环后必须重建信号量"
    sandbox_executor._sem = None
    sandbox_executor._sem_loop = None
    sandbox_executor._sem_limit = 0


def test_semaphore_rebuilt_when_limit_changes(monkeypatch):
    """并发上限改配置后要生效，不能一直用旧容量。"""
    sandbox_executor._sem = None
    sandbox_executor._sem_loop = None
    sandbox_executor._sem_limit = 0

    async def grab():
        return _get_semaphore()

    monkeypatch.setattr(settings, "SKILL_SANDBOX_MAX_CONCURRENT", 2, raising=False)
    loop = asyncio.new_event_loop()
    try:
        first = loop.run_until_complete(grab())
        monkeypatch.setattr(settings, "SKILL_SANDBOX_MAX_CONCURRENT", 5, raising=False)
        second = loop.run_until_complete(grab())
    finally:
        loop.close()

    assert first is not second
    sandbox_executor._sem = None
    sandbox_executor._sem_loop = None
    sandbox_executor._sem_limit = 0


def test_zero_limit_means_unlimited(monkeypatch):
    monkeypatch.setattr(settings, "SKILL_SANDBOX_MAX_CONCURRENT", 0, raising=False)
    sandbox_executor._sem = None
    sandbox_executor._sem_loop = None
    sandbox_executor._sem_limit = 0
    assert _get_semaphore() is None


@pytest.mark.asyncio
async def test_external_cancel_while_queued_does_not_leak_permit():
    """排队期间被**外部取消**（用户点停止/断连/切会话）不得泄漏名额。

    与本文件开头那条已被否证的「超时泄漏」不是同一条：超时路径有 cancel+兜底 release，
    而外部取消走的是 `asyncio.wait` 自身抛 CancelledError —— 它**不会**取消传进去的
    acquire task（只有 wait_for 会）。那个 acquire 稍后拿到名额，而 execute_in_sandbox 的
    try/finally 在拿到名额之后才开始，acquired 仍是 False → 名额永不归还。
    累计到闸值就永久「沙箱繁忙（并发已满）」直到进程重启。
    """
    sem = asyncio.Semaphore(3)
    await sem.acquire()
    await sem.acquire()
    await sem.acquire()          # 闸满，后续一律排队

    waiters = [
        asyncio.ensure_future(sandbox_executor._acquire_with_timeout(sem, 60))
        for _ in range(3)
    ]
    await asyncio.sleep(0)       # 让它们真正进入排队

    for w in waiters:
        w.cancel()
    for w in waiters:
        with pytest.raises(asyncio.CancelledError):
            await w

    # 释放最初占用的三个，可用名额必须完整回到 3
    sem.release()
    sem.release()
    sem.release()
    await asyncio.sleep(0)

    free = 0
    while True:
        try:
            await asyncio.wait_for(sem.acquire(), timeout=0.05)
        except asyncio.TimeoutError:
            break
        free += 1
    assert free == 3, f"被取消的排队者泄漏了 {3 - free} 个并发名额"
