# -*- coding: utf-8 -*-
"""有状态浏览会话的生命周期（2026-07-27）。

这组守的是**资源与隔离**，不是功能：
- 一个重页面 context 常驻约 400MiB，会话漏一个就少 400MiB；
- 会话按 Run 归属——串号意味着 A 用户能操作 B 用户打开的页面（连带 B 的登录态）。
两条都不会在功能测试里暴露出来，必须单独钉住。
"""
import asyncio

import pytest

from app.core.config import settings
from app.services.chat.tools import browser as browser_tools


class _FakeSession:
    def __init__(self):
        self.closed = False

    async def call(self, _method, params=None, **_kw):
        if (params or {}).get("name") == "browser_close":
            self.closed = True
        return {"content": []}

    async def call_recovering(self, _method, params=None):
        return {"content": [{"type": "text", "text": "- button \"下一页\" [ref=e1]"}]}

    async def close_all_contexts(self, *, why: str = ""):
        """镜像 _McpSession 的同名方法（2026-07-27 新增）。

        真实实现要关掉本对象用过的**每一个**会话——重新握手换掉 session_id 后，旧会话
        在服务端还攥着一个 chromium context，只关当前会话就会一路漏到服务开不出页面。
        替身少一个方法会被 _live_discard 的 except 静默吞掉，反而让回收测试假绿，
        所以这里必须跟着实现走。
        """
        await self.call("tools/call", {"name": "browser_close", "arguments": {}})


class _FakeClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


def _entry(now: float, *, opened: float = None):
    return {
        "client": _FakeClient(), "session": _FakeSession(), "url": "https://x.com/",
        "opened_at": opened if opened is not None else now, "touched_at": now,
    }


@pytest.fixture(autouse=True)
def _clean():
    browser_tools._LIVE.clear()
    yield
    browser_tools._LIVE.clear()


@pytest.mark.asyncio
async def test_idle_session_is_reclaimed_and_context_released(monkeypatch):
    monkeypatch.setattr(settings, "BROWSER_LIVE_IDLE_TTL_S", 180, raising=False)
    now = asyncio.get_event_loop().time()  # 值不重要，只要与 time.time 拉开距离
    import time as _t
    entry = _entry(_t.time() - 10_000)     # 空闲远超 TTL
    browser_tools._LIVE["run-idle"] = entry

    await browser_tools._live_sweep()

    assert "run-idle" not in browser_tools._LIVE
    assert entry["session"].closed is True, "必须真的调 browser_close，否则浏览器侧 context 泄漏"
    assert entry["client"].closed is True, "httpx 客户端也要关，否则连接漏着"
    assert now is not None


@pytest.mark.asyncio
async def test_session_over_max_life_is_reclaimed_even_if_busy(monkeypatch):
    """一直在用但开了太久也要回收：防「模型忘了 close」把 context 挂到天荒地老。"""
    monkeypatch.setattr(settings, "BROWSER_LIVE_MAX_LIFE_S", 900, raising=False)
    import time as _t
    entry = _entry(_t.time(), opened=_t.time() - 10_000)  # 刚碰过，但寿命超了
    browser_tools._LIVE["run-old"] = entry

    await browser_tools._live_sweep()

    assert "run-old" not in browser_tools._LIVE
    assert entry["session"].closed is True


@pytest.mark.asyncio
async def test_sessions_are_isolated_per_run():
    """按 Run 归属：拿不到别人的页面，也就拿不到别人的登录态。"""
    import time as _t
    browser_tools._LIVE["run-a"] = _entry(_t.time())
    browser_tools._LIVE["run-b"] = _entry(_t.time())

    a = await browser_tools._live_use("run-a")
    b = await browser_tools._live_use("run-b")
    assert a is not b

    with pytest.raises(Exception) as exc:
        await browser_tools._live_use("run-c")
    assert "browser_open" in str(exc.value), "没有会话时要明确引导先 open，而不是替它猜一个页面"


@pytest.mark.asyncio
async def test_close_releases_and_is_idempotent():
    import time as _t
    entry = _entry(_t.time())
    browser_tools._LIVE["run-x"] = entry

    assert await browser_tools._live_close("run-x") is True
    assert entry["session"].closed and entry["client"].closed
    # 再关一次不报错、也不假装关掉了什么
    assert await browser_tools._live_close("run-x") is False


@pytest.mark.asyncio
async def test_open_rejects_when_capacity_is_full(monkeypatch):
    """满闸时明确失败，不排队、不悄悄挤掉别人的页面。"""
    monkeypatch.setattr(settings, "BROWSER_LIVE_MAX_SESSIONS", 2, raising=False)
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    import time as _t
    browser_tools._LIVE["r1"] = _entry(_t.time())
    browser_tools._LIVE["r2"] = _entry(_t.time())

    with pytest.raises(Exception) as exc:
        await browser_tools._live_open("r3", "https://example.com/")
    assert "上限" in str(exc.value)
    assert len(browser_tools._LIVE) == 2, "失败不能留下半个会话"


@pytest.mark.asyncio
async def test_disabled_switch_refuses_to_open(monkeypatch):
    monkeypatch.setattr(settings, "BROWSER_LIVE_MAX_SESSIONS", 0, raising=False)
    with pytest.raises(Exception) as exc:
        await browser_tools._live_open("r9", "https://example.com/")
    assert "未启用" in str(exc.value)


# ---- context 泄漏回归（2026-07-27 实测事故）----
# call_recovering 重新握手会换掉 _session_id，而旧会话在服务端仍攥着一个 chromium
# context。原先 finally 只关当前会话，于是每抖动一次漏一个：实测一批并发失败后
# agent-browser 的 PID 从 12 涨到 108、空闲仍占 179% CPU，此后只应答协议、开不出
# 页面。healthcheck 只探端口所以永远绿，restart: unless-stopped 也就永不触发。
def test_close_all_contexts_closes_every_session_ever_used():
    """握手换过几次会话，就要关几次——只关最后一个等于漏掉前面所有的。"""
    import asyncio

    from app.services.chat.tools.browser import _McpSession

    closed: list = []

    class _Client:
        async def post(self, _url, json=None, headers=None):  # noqa: A002
            if (json or {}).get("params", {}).get("name") == "browser_close":
                closed.append(headers.get("Mcp-Session-Id"))
            raise AssertionError("本用例只驱动 close 路径")

    session = _McpSession(_Client(), "http://x/mcp")
    # 模拟三次握手换出的三个会话 id
    session._used_session_ids = ["sid-1", "sid-2", "sid-3"]
    session._session_id = "sid-3"

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        session.close_all_contexts(why="test"))

    assert closed == ["sid-1", "sid-2", "sid-3"], (
        f"只关了 {closed}——没关掉的会话会在服务端漏一个 chromium context")
    # 关完不能把当前会话指针弄丢（后续 finally 里可能还要用）
    assert session._session_id == "sid-3"


# ============ 跨轮归属（2026-07-28）============
# 原先按 run_id 归属，而 run_id **每个用户回合都换** —— 上一轮 browser_open 打开的页面，
# 下一轮 browser_act 必然拿不到，只会回「当前没有打开的网页，请先用 browser_open」。
# 而模块文档与工具描述都写着「跨轮活着的浏览器会话」。被遗弃的会话还占着名额，
# 3 分钟内第 5 次 open 就报「已达上限」，用户视角里一个页面都没开着。

@pytest.mark.asyncio
async def test_same_thread_across_turns_shares_one_session(monkeypatch):
    """同一对话的两个回合（run_id 不同）必须落在同一个键上——这才叫"跨轮活着"。"""
    keys: list = []

    async def _fake_use(key):
        keys.append(key)
        raise browser_tools.ToolSoftError("stop")

    monkeypatch.setattr(browser_tools, "_live_use", _fake_use)
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    monkeypatch.setattr(settings, "BROWSER_LIVE_MAX_SESSIONS", 4, raising=False)

    for run in ("run-1", "run-2"):
        tools = browser_tools.build_browser_tools(
            user_id="u1", thread_id="th-A", run_id=run)
        act = next(t for t in tools if t.name == "browser_act")
        with pytest.raises(Exception):
            await act.execute({"action": "click", "target": "e1", "element": "下一页"})

    assert keys[0] == keys[1], (
        f"两轮拿到了不同的会话键 {keys}——run_id 每轮都换，按它归属等于「跨轮」是假的")


@pytest.mark.asyncio
async def test_different_users_never_share_a_session(monkeypatch):
    """换键不能把刚修好的跨用户隔离弄回去：user_id 必须仍在键里。"""
    keys: list = []

    async def _fake_use(key):
        keys.append(key)
        raise browser_tools.ToolSoftError("stop")

    monkeypatch.setattr(browser_tools, "_live_use", _fake_use)
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    monkeypatch.setattr(settings, "BROWSER_LIVE_MAX_SESSIONS", 4, raising=False)

    for uid in ("userA", "userB"):
        tools = browser_tools.build_browser_tools(user_id=uid, thread_id="th-SAME", run_id="r1")
        act = next(t for t in tools if t.name == "browser_act")
        with pytest.raises(Exception):
            await act.execute({"action": "click", "target": "e1", "element": "x"})

    assert keys[0] != keys[1], "不同用户即使 thread_id 撞车也不能共用会话（会连带登录态）"
    assert keys[0].startswith("userA|") and keys[1].startswith("userB|")


@pytest.mark.asyncio
async def test_thread_missing_falls_back_to_run(monkeypatch):
    """拿不到 thread_id 时退回 run_id：至少同回合内可用，而不是掉进公共桶。"""
    keys: list = []

    async def _fake_use(key):
        keys.append(key)
        raise browser_tools.ToolSoftError("stop")

    monkeypatch.setattr(browser_tools, "_live_use", _fake_use)
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    monkeypatch.setattr(settings, "BROWSER_LIVE_MAX_SESSIONS", 4, raising=False)

    tools = browser_tools.build_browser_tools(user_id="u1", thread_id="", run_id="run-9")
    act = next(t for t in tools if t.name == "browser_act")
    with pytest.raises(Exception):
        await act.execute({"action": "click", "target": "e1", "element": "x"})
    assert keys == ["u1|run-9"]


# ============ 上限：并发不可突破 + 满了先牺牲自己的（2026-07-28）============

@pytest.mark.asyncio
async def test_capacity_check_and_insert_are_atomic(monkeypatch):
    """检查容量与占名额必须在**同一次持锁**里完成。

    原实现检查在一次加锁、插入在另一次加锁，中间放开了锁——并发的两次 open 会
    一起看到未满、一起插入，上限形同虚设。
    """
    monkeypatch.setattr(settings, "BROWSER_LIVE_MAX_SESSIONS", 2, raising=False)
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    monkeypatch.setattr(settings, "BROWSER_LIVE_SWEEP_INTERVAL_S", 0, raising=False)

    async def _slow_open(*_a, **_kw):
        # 建连很慢：原实现在这段时间里名额还没占上，别人能挤进来
        await asyncio.sleep(0.05)
        raise RuntimeError("连接失败（本用例只关心名额有没有被占住）")

    class _Client:
        async def aclose(self):
            return None

    monkeypatch.setattr(browser_tools.httpx, "AsyncClient", lambda **_kw: _Client())

    class _Session:
        def __init__(self, *_a, **_kw):
            pass

        handshake = _slow_open

        async def close_all_contexts(self, *, why=""):
            return None

    monkeypatch.setattr(browser_tools, "_McpSession", _Session)

    async def _one(key):
        try:
            await browser_tools._live_open(key, "https://example.com/")
        except Exception as exc:  # noqa: BLE001
            return str(exc)

    # 三个不同用户同时开：cap=2，第三个必须被拒（而不是三个都挤进去）
    results = await asyncio.gather(_one("uA|t"), _one("uB|t"), _one("uC|t"))
    refused = [r for r in results if "上限" in str(r)]
    assert len(refused) == 1, f"cap=2 时三路并发应有且只有一路被拒，实际 {results}"
    assert browser_tools._LIVE == {}, "失败的 open 必须把占位名额还回去，否则会永久少名额"


@pytest.mark.asyncio
async def test_full_capacity_evicts_only_my_own_oldest(monkeypatch):
    """满闸时牺牲**同一个用户**最久没碰的页面；绝不动别人的（那带着别人的登录态）。"""
    monkeypatch.setattr(settings, "BROWSER_LIVE_MAX_SESSIONS", 2, raising=False)
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    monkeypatch.setattr(settings, "BROWSER_LIVE_SWEEP_INTERVAL_S", 0, raising=False)
    import time as _t

    old = _entry(_t.time() - 5)
    other = _entry(_t.time())
    browser_tools._LIVE["u1|th-old"] = old
    browser_tools._LIVE["u2|th-other"] = other

    class _Client:
        async def aclose(self):
            return None

    class _Session:
        def __init__(self, *_a, **_kw):
            pass

        async def handshake(self):
            return None

        async def call_recovering(self, _m, params=None, **_kw):
            return {"content": [{"type": "text", "text": "- Page URL: https://example.com/"}]}

        async def close_all_contexts(self, *, why=""):
            return None

    monkeypatch.setattr(browser_tools.httpx, "AsyncClient", lambda **_kw: _Client())
    monkeypatch.setattr(browser_tools, "_McpSession", _Session)

    await browser_tools._live_open("u1|th-new", "https://example.com/")

    assert "u1|th-old" not in browser_tools._LIVE, "该牺牲的是自己最久没用的那个"
    assert old["session"].closed is True, "被挤掉的会话必须真的关掉，不能只从字典里摘走"
    assert "u2|th-other" in browser_tools._LIVE, "绝不能动别的用户的会话"
    assert "u1|th-new" in browser_tools._LIVE


@pytest.mark.asyncio
async def test_full_capacity_refuses_when_all_slots_belong_to_others(monkeypatch):
    """名额全被别人占着时明确失败，而不是去挤别人的页面。"""
    monkeypatch.setattr(settings, "BROWSER_LIVE_MAX_SESSIONS", 2, raising=False)
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    import time as _t

    browser_tools._LIVE["uX|t1"] = _entry(_t.time())
    browser_tools._LIVE["uY|t2"] = _entry(_t.time())

    with pytest.raises(Exception) as exc:
        await browser_tools._live_open("uZ|t3", "https://example.com/")
    assert "上限" in str(exc.value)
    assert set(browser_tools._LIVE) == {"uX|t1", "uY|t2"}


# ============ 会话丢失 ≠ 元素变了（2026-07-28）============

@pytest.mark.asyncio
async def test_stateful_call_does_not_rehandshake():
    """重握手换来的是**全新空 context**：snapshot 回 about:blank 脚手架、click 回 isError，
    工具于是说「元素可能已经变了」——真实原因是「页面没了」，模型会拿同一批 ref 死循环。"""
    from app.services.chat.tools.browser import _LiveSessionLost, _McpSession, _RetryableMcpError

    calls: list = []

    class _Client:
        async def post(self, _url, json=None, headers=None):  # noqa: A002
            calls.append((json or {}).get("method"))
            raise AssertionError("不该走到真正的 post")

    session = _McpSession(_Client(), "http://x/mcp")

    async def _boom(_method, _params):
        raise _RetryableMcpError("会话失效：HTTP 404 Session not found")

    session.call = _boom  # type: ignore[assignment]

    with pytest.raises(_LiveSessionLost):
        await session.call_recovering("tools/call", {"name": "browser_snapshot"}, stateful=True)
    assert "initialize" not in calls, "有状态调用绝不能重握手（那会造一个孤儿 context）"


@pytest.mark.asyncio
async def test_act_reports_page_lost_and_drops_the_session(monkeypatch):
    """会话没了要如实说「请重新 browser_open」，并且把死会话从 _LIVE 里摘掉。"""
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    monkeypatch.setattr(settings, "BROWSER_LIVE_MAX_SESSIONS", 4, raising=False)
    import time as _t

    entry = _entry(_t.time())

    class _DeadSession(_FakeSession):
        async def call_recovering(self, _method, params=None, *, stateful=False):
            raise browser_tools._LiveSessionLost("会话失效：HTTP 404 Session not found")

    entry["session"] = _DeadSession()
    browser_tools._LIVE["u1|th-1"] = entry

    tools = browser_tools.build_browser_tools(user_id="u1", thread_id="th-1", run_id="r1")
    act = next(t for t in tools if t.name == "browser_act")

    with pytest.raises(Exception) as exc:
        await act.execute({"action": "click", "target": "e1", "element": "下一页"})
    msg = str(exc.value)
    assert "browser_open" in msg and "页面已经不在了" in msg
    assert "元素可能已经变了" not in msg, "这句话会让模型拿同一批 ref 反复重试"
    assert "u1|th-1" not in browser_tools._LIVE, "死会话必须摘掉，不能留着骗下一次调用"


@pytest.mark.asyncio
async def test_act_upstream_fault_is_not_called_element_changed(monkeypatch):
    """上游自身故障（frame detached 之类）同样不能翻译成「元素变了」。"""
    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "http://agent-browser:8089/mcp", raising=False)
    monkeypatch.setattr(settings, "BROWSER_LIVE_MAX_SESSIONS", 4, raising=False)
    import time as _t

    entry = _entry(_t.time())

    class _FaultySession(_FakeSession):
        async def call_recovering(self, _method, params=None, *, stateful=False):
            return {"isError": True, "content": [{
                "type": "text",
                "text": "Error: browserBackend.callTool: net::ERR_ABORTED; maybe frame was detached?",
            }]}

    entry["session"] = _FaultySession()
    browser_tools._LIVE["u9|th-9"] = entry

    tools = browser_tools.build_browser_tools(user_id="u9", thread_id="th-9", run_id="r1")
    act = next(t for t in tools if t.name == "browser_act")
    with pytest.raises(Exception) as exc:
        await act.execute({"action": "click", "target": "e1", "element": "下一页"})
    msg = str(exc.value)
    assert "浏览器服务出错" in msg and "browser_open" in msg
    assert "元素可能已经变了" not in msg


# ============ 后台回收（2026-07-28）============

@pytest.mark.asyncio
async def test_background_sweeper_reclaims_without_any_traffic(monkeypatch):
    """TTL 原先只在「有人再次调用浏览器工具」时才被检查——用户开完页面去做别的事，
    没有下一次调用就没有回收，最多 4 个 context（约 1.6GB）挂到进程重启。"""
    monkeypatch.setattr(settings, "BROWSER_LIVE_IDLE_TTL_S", 30, raising=False)
    monkeypatch.setattr(settings, "BROWSER_LIVE_SWEEP_INTERVAL_S", 15, raising=False)
    import time as _t

    entry = _entry(_t.time() - 10_000)  # 空闲远超 TTL
    browser_tools._LIVE["u1|th-idle"] = entry

    sleeps: list = []
    real_sleep = asyncio.sleep

    async def _fast_sleep(sec):
        sleeps.append(sec)
        await real_sleep(0)

    monkeypatch.setattr(browser_tools.asyncio, "sleep", _fast_sleep)
    browser_tools._ensure_sweeper()
    task = browser_tools._SWEEPER
    assert task is not None, "配了扫描间隔就必须真的起一个后台任务"
    await asyncio.wait_for(task, timeout=2)

    assert sleeps and sleeps[0] == 15
    assert "u1|th-idle" not in browser_tools._LIVE
    assert entry["session"].closed is True, "后台回收也要真的关掉浏览器 context"
    assert browser_tools._SWEEPER is None, "_LIVE 空了任务要自行退出，不要空转"


def test_sweeper_disabled_by_zero_interval(monkeypatch):
    monkeypatch.setattr(settings, "BROWSER_LIVE_SWEEP_INTERVAL_S", 0, raising=False)
    browser_tools._SWEEPER = None
    browser_tools._ensure_sweeper()
    assert browser_tools._SWEEPER is None
