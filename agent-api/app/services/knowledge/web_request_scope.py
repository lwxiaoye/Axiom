"""An in-memory request scope owned and closed by one research Run.

Only transport work is shared; each caller still authorizes and records its own
tool receipt. Neither credentials nor another Run's data enter a global cache.
"""
import asyncio
from copy import deepcopy
import time


class WebRequestScope:
    def __init__(self, *, success_ttl=600.0, failure_ttl=15.0, capacity=128):
        self.success_ttl = success_ttl
        self.failure_ttl = failure_ttl
        self.capacity = capacity
        self._cache = {}
        self._pending = {}
        self._budget_used = {}
        self._budget_lock = asyncio.Lock()
        self._closed = False

    async def claim_budget(self, key, *, limit):
        """Atomically reserve one Run-scoped provider call.

        The scope is owned by one research Run, so this limits paid hybrid searches
        across concurrent members without introducing a cross-user global counter.
        """
        if self._closed:
            raise asyncio.CancelledError()
        cap = max(0, int(limit))
        async with self._budget_lock:
            used = int(self._budget_used.get(key) or 0)
            if used >= cap:
                return False
            self._budget_used[key] = used + 1
            return True

    async def fetch(self, key, factory):
        if self._closed:
            raise asyncio.CancelledError()
        now = time.monotonic()
        self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        cached = self._cache.get(key)
        if cached:
            return {**deepcopy(cached[1]), "cache_hit": True}
        entry = self._pending.get(key)
        shared = entry is not None
        if entry is None:
            async def produce():
                result = await factory()
                ttl = self.failure_ttl if result.get("error") or result.get("ok") is False else self.success_ttl
                if len(self._cache) >= self.capacity:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[key] = (time.monotonic() + ttl, deepcopy(result))
                return result
            entry = {"task": asyncio.create_task(produce()), "waiters": 0}
            self._pending[key] = entry
        entry["waiters"] += 1
        try:
            # A member cancellation must not abort another member's shared read.
            result = await asyncio.shield(entry["task"])
            return {**deepcopy(result), "cache_hit": shared}
        finally:
            entry["waiters"] -= 1
            if not entry["waiters"]:
                self._pending.pop(key, None)
                if not entry["task"].done():
                    entry["task"].cancel()
                await asyncio.gather(entry["task"], return_exceptions=True)

    async def close(self):
        self._closed = True
        tasks = [entry["task"] for entry in self._pending.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
        self._cache.clear()
        self._budget_used.clear()
