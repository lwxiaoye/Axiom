"""本地沙箱僵尸容器收割（2026-07-28，P2）。

病灶：容器 entrypoint 是 `sleep 4200`（到点自毁，session_pool 三层回收之三），可 `docker run`
没带 `--rm`、SDK 侧 auto_remove 写死 False——自毁只是把容器变成 Exited(0) 空壳留在 daemon 里，
再没人回来收。本机实测 31 个 agent-sbx-* 全是 Exited(0)、最早 5 天前，每个的
Created→FinishedAt 恰好 4200s，尸体来源确凿。

修法两条腿：`--rm` 让自毁真的等于消失（不再产生新空壳）+ 启动/周期收割清存量与漏网之鱼。
本文件锁死收割器的两条命门：
1. **只删已退出的，绝不碰运行中的**——服务端过滤 + 客户端状态复核 + 永不用 force，三道闸；
2. **纯 best-effort**——docker 不可达 / 无权限 / 并发删同一个 / 超时，一律不许把沙箱带下水。

全程假掉 subprocess 与 docker SDK，不起任何真容器。
"""

import asyncio
import time

import pytest

from app.services.sandbox import local_adapter as la
from app.services.sandbox.base import SandboxError
from app.services.sandbox.local_adapter import LocalDockerAdapter


@pytest.fixture(autouse=True)
def _reset_reaper_state():
    """收割冷却、"在用容器"登记、后台任务集都是模块级状态，用例之间必须互不串味。"""
    la._last_reap_at = 0.0
    la._live_containers.clear()
    la._reap_tasks.clear()
    yield
    la._last_reap_at = 0.0
    la._live_containers.clear()
    la._reap_tasks.clear()


async def _drain_reap_tasks():
    """收割跑在后台任务里，断言前先等它落地。"""
    tasks = list(la._reap_tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


class _FakeCli:
    """docker CLI 的替身：记录每次 argv，按子命令给回应。"""

    def __init__(self, listing: str = "", ps_rc: int = 0, rm_rc: int = 0, ps_exc=None):
        self.listing = listing
        self.ps_rc = ps_rc
        self.rm_rc = rm_rc
        self.ps_exc = ps_exc
        self.calls: list[tuple] = []

    async def __call__(self, *args, timeout=None, max_bytes=None, on_stdout=None):
        self.calls.append(args)
        if args[0] == "ps":
            if self.ps_exc is not None:
                raise self.ps_exc
            return self.ps_rc, self.listing.encode(), b"" if self.ps_rc == 0 else b"daemon down"
        if args[0] == "rm":
            return self.rm_rc, b"", b"" if self.rm_rc == 0 else b"container is running"
        return 0, b"fake-cid\n", b""

    def argv(self, sub: str) -> tuple:
        for call in self.calls:
            if call and call[0] == sub:
                return call
        return ()

    def count(self, sub: str) -> int:
        return sum(1 for c in self.calls if c and c[0] == sub)


class _FakeContainer:
    def __init__(self, name: str, status: str, remove_exc=None):
        self.name = name
        self.status = status
        self.remove_exc = remove_exc
        self.remove_calls: list[dict] = []

    def remove(self, **kwargs):
        self.remove_calls.append(kwargs)
        if self.remove_exc is not None:
            raise self.remove_exc


class _FakeContainers:
    def __init__(self, items, list_exc=None):
        self.items = items
        self.list_exc = list_exc
        self.list_kwargs = None
        self.run_kwargs = None

    def list(self, **kwargs):
        self.list_kwargs = kwargs
        if self.list_exc is not None:
            raise self.list_exc
        return list(self.items)

    def run(self, *args, **kwargs):
        self.run_kwargs = kwargs
        return _FakeContainer("agent-sbx-new", "running")


class _FakeClient:
    def __init__(self, containers):
        self.containers = containers


def _cli_adapter(fake: _FakeCli) -> LocalDockerAdapter:
    adapter = LocalDockerAdapter()
    adapter._backend = "cli"
    adapter._docker = fake
    return adapter


def _sdk_adapter(containers: _FakeContainers) -> LocalDockerAdapter:
    adapter = LocalDockerAdapter()
    adapter._backend = "sdk"
    adapter._client = _FakeClient(containers)
    return adapter


# ---------------- (a) 只删已退出的，不碰运行中的 ----------------


@pytest.mark.asyncio
async def test_cli_reap_asks_docker_for_exited_and_dead_only():
    """第一道闸：交给 docker 的过滤器必须精确到状态，而不是按名字瞎捞一把。"""
    fake = _FakeCli(listing="agent-sbx-aaa\n")
    await _cli_adapter(fake)._reap_exited_containers()

    ps = fake.argv("ps")
    assert "-a" in ps, "不带 -a 只能看见运行中的容器，正好把要收的全滤没了"
    assert f"name={la._CONTAINER_PREFIX}" in ps
    assert "status=exited" in ps and "status=dead" in ps
    # 同 key 的多个 --filter 是"或"，不同 key 才是"与"——running/created 不会落进结果
    assert "status=running" not in ps and "status=created" not in ps
    assert "{{.Names}}" in ps


@pytest.mark.asyncio
async def test_cli_reap_never_passes_force():
    """最后一道硬闸：不带 -f，万一过滤器语义漂移回了个运行中的容器，docker 自己会拒绝。"""
    fake = _FakeCli(listing="agent-sbx-aaa\nagent-sbx-bbb\n")
    removed = await _cli_adapter(fake)._reap_exited_containers()

    rm = fake.argv("rm")
    assert rm and rm[0] == "rm"
    assert "-f" not in rm and "--force" not in rm, "收僵尸永远不值得用 force"
    assert set(removed) == {"agent-sbx-aaa", "agent-sbx-bbb"}


@pytest.mark.asyncio
async def test_cli_reap_skips_foreign_names_and_in_use_containers():
    """docker 的 name 过滤是子串匹配，会捞到别人的容器；本进程在用的更不能碰。"""
    la._mark_live("agent-sbx-inuse")
    fake = _FakeCli(
        listing="agent-sbx-dead1\nagent-sbx-inuse\nmy-agent-sbx-someoneelse\nunrelated\n\n"
    )
    removed = await _cli_adapter(fake)._reap_exited_containers()

    assert removed == ["agent-sbx-dead1"]
    rm = fake.argv("rm")
    assert "agent-sbx-inuse" not in rm
    assert "my-agent-sbx-someoneelse" not in rm
    assert "unrelated" not in rm


@pytest.mark.asyncio
async def test_cli_reap_without_victims_does_not_call_rm():
    fake = _FakeCli(listing="\n")
    assert await _cli_adapter(fake)._reap_exited_containers() == []
    assert fake.count("rm") == 0, "没有目标就别发 docker rm"


@pytest.mark.asyncio
async def test_cli_reap_caps_batch_size():
    """一次别把 argv 撑爆；剩下的下一轮再收。"""
    fake = _FakeCli(listing="\n".join(f"agent-sbx-{i:04d}" for i in range(la._REAP_MAX_BATCH + 50)))
    removed = await _cli_adapter(fake)._reap_exited_containers()

    assert len(removed) == la._REAP_MAX_BATCH
    assert len(fake.argv("rm")) == la._REAP_MAX_BATCH + 1  # 含 "rm" 本身


@pytest.mark.asyncio
async def test_sdk_reap_only_removes_exited_and_dead():
    """第二道闸：客户端按状态复核一遍，过滤器语义随版本漂移也删不到运行中的。"""
    running = _FakeContainer("agent-sbx-run", "running")
    paused = _FakeContainer("agent-sbx-paused", "paused")
    created = _FakeContainer("agent-sbx-created", "created")
    restarting = _FakeContainer("agent-sbx-restarting", "restarting")
    exited = _FakeContainer("agent-sbx-exited", "exited")
    dead = _FakeContainer("agent-sbx-dead", "dead")
    containers = _FakeContainers([running, paused, created, restarting, exited, dead])

    removed = await _sdk_adapter(containers)._reap_exited_containers()

    assert set(removed) == {"agent-sbx-exited", "agent-sbx-dead"}
    for survivor in (running, paused, created, restarting):
        assert survivor.remove_calls == [], f"{survivor.name}（{survivor.status}）被误删了"
    # 且删的时候不带 force——运行中的容器该由 docker 自己拒绝
    assert exited.remove_calls == [{}] and dead.remove_calls == [{}]


@pytest.mark.asyncio
async def test_sdk_reap_lists_with_all_and_status_filter():
    containers = _FakeContainers([])
    await _sdk_adapter(containers)._reap_exited_containers()

    assert containers.list_kwargs["all"] is True
    filters = containers.list_kwargs["filters"]
    assert filters["name"] == la._CONTAINER_PREFIX
    assert set(filters["status"]) == set(la._REAPABLE_STATES)


@pytest.mark.asyncio
async def test_sdk_reap_skips_foreign_names_and_in_use_containers():
    la._mark_live("agent-sbx-inuse")
    inuse = _FakeContainer("agent-sbx-inuse", "exited")   # 停了但本进程还可能 docker cp 取产物
    foreign = _FakeContainer("my-agent-sbx-other", "exited")
    mine = _FakeContainer("agent-sbx-zombie", "exited")

    removed = await _sdk_adapter(_FakeContainers([inuse, foreign, mine]))._reap_exited_containers()

    assert removed == ["agent-sbx-zombie"]
    assert inuse.remove_calls == [] and foreign.remove_calls == []


# ---------------- (b) docker 报错时不抛异常 ----------------


@pytest.mark.asyncio
async def test_gate_swallows_ps_failure():
    """daemon 挂了/无权限：列举失败只记日志，沙箱创建流程照走。"""
    fake = _FakeCli(ps_rc=1)
    adapter = _cli_adapter(fake)

    with pytest.raises(SandboxError):
        await adapter._reap_exited_containers()   # 底层如实报错
    await adapter._reap_zombies()                 # 门口这层必须吞掉


@pytest.mark.asyncio
async def test_gate_swallows_missing_docker_binary():
    """host 上没有 docker CLI：create_subprocess_exec 直接 FileNotFoundError。"""
    fake = _FakeCli(ps_exc=FileNotFoundError("docker not found"))
    await _cli_adapter(fake)._reap_zombies()


@pytest.mark.asyncio
async def test_cli_rm_partial_failure_is_not_fatal():
    """批量删部分失败（并发收割撞车 / 刚被别人删走）：不抛，继续。"""
    fake = _FakeCli(listing="agent-sbx-a\nagent-sbx-b\n", rm_rc=1)
    adapter = _cli_adapter(fake)

    assert await adapter._reap_exited_containers() == ["agent-sbx-a", "agent-sbx-b"]
    await adapter._reap_zombies()


@pytest.mark.asyncio
async def test_sdk_one_remove_failure_does_not_stop_the_rest():
    """并发删同一个容器会报 404/409——不能让一个失败带走整轮收割。"""
    boom = _FakeContainer("agent-sbx-gone", "exited", remove_exc=RuntimeError("404 no such container"))
    ok1 = _FakeContainer("agent-sbx-1", "exited")
    ok2 = _FakeContainer("agent-sbx-2", "dead")

    removed = await _sdk_adapter(_FakeContainers([boom, ok1, ok2]))._reap_exited_containers()

    assert removed == ["agent-sbx-1", "agent-sbx-2"]
    assert ok1.remove_calls and ok2.remove_calls


@pytest.mark.asyncio
async def test_gate_swallows_sdk_list_failure():
    containers = _FakeContainers([], list_exc=RuntimeError("permission denied on /var/run/docker.sock"))
    await _sdk_adapter(containers)._reap_zombies()


@pytest.mark.asyncio
async def test_gate_swallows_timeout(monkeypatch):
    """daemon 僵死：到预算就放弃，别让后台任务无限挂着。"""
    monkeypatch.setattr(la, "_REAP_TIMEOUT_S", 0.05)
    adapter = LocalDockerAdapter()
    adapter._backend = "cli"

    async def _hang():
        await asyncio.sleep(5)
        return []

    monkeypatch.setattr(adapter, "_reap_exited_containers", _hang)
    await asyncio.wait_for(adapter._reap_zombies(), timeout=2)


# ---------------- 触发时机与"在用"保护 ----------------


def test_reap_due_fires_once_per_interval():
    """进程内第一次创建沙箱＝启动清扫；冷却窗口内的后续创建不再重复扫。

    判定必须是同步的，否则同一 tick 内并发的 create() 会各起一个收割任务。
    """
    assert la._reap_due() is True, "进程刚起，第一次必须扫"
    assert la._reap_due() is False, "冷却窗口内不该重复扫"

    la._last_reap_at = 0.0  # 模拟过了冷却
    assert la._reap_due() is True


@pytest.mark.asyncio
async def test_create_does_not_wait_for_the_reaper():
    """收割绝不能挡在 create() 前面——满载时一次 docker socket 调用能读超时 60s。"""
    fake = _FakeCli(listing="agent-sbx-old\n")
    adapter = _cli_adapter(fake)

    await adapter.create()

    subs = [c[0] for c in fake.calls]
    assert "run" in subs
    assert "ps" not in subs, "create() 返回时收割还没开跑＝它没被挂在关键路径上"
    assert adapter._container in la._live_containers
    assert adapter._container in la._protected_names()

    await _drain_reap_tasks()  # 后台收割随后才落地
    assert fake.count("ps") == 1
    assert "agent-sbx-old" in fake.argv("rm")


@pytest.mark.asyncio
async def test_second_create_within_cooldown_spawns_no_reaper():
    fake = _FakeCli(listing="")
    await _cli_adapter(fake).create()
    await _drain_reap_tasks()
    assert fake.count("ps") == 1

    fake2 = _FakeCli(listing="")
    await _cli_adapter(fake2).create()
    await _drain_reap_tasks()
    assert fake2.count("ps") == 0, "冷却窗口内的第二次创建不该再扫一遍"


def test_spawn_reap_without_event_loop_is_a_noop():
    """同步上下文（无运行中的事件循环）里 create_task 会 RuntimeError——不许炸出去。"""
    la._spawn_reap(_cli_adapter(_FakeCli()))
    assert la._reap_tasks == set()


@pytest.mark.asyncio
async def test_delete_releases_the_live_mark():
    fake = _FakeCli()
    adapter = _cli_adapter(fake)
    await adapter.create()
    assert adapter._container in la._live_containers

    await adapter.delete()
    assert adapter._container not in la._live_containers, "删完还占着保护名单＝收割器永远看不见它"
    await _drain_reap_tasks()


def test_stale_live_mark_expires():
    """delete() 漏调（adapter 被 GC/异常吞掉）不能把容器永久钉在保护名单里。"""
    la._live_containers["agent-sbx-leaked"] = time.monotonic() - la._container_sleep_seconds() - 1
    la._live_containers["agent-sbx-fresh"] = time.monotonic()

    protected = la._protected_names()

    assert "agent-sbx-leaked" not in protected
    assert "agent-sbx-fresh" in protected
    assert "agent-sbx-leaked" not in la._live_containers, "过期登记要顺手摘掉，别无限堆积"


# ---------------- --rm / auto_remove：不再产生新空壳 ----------------


@pytest.mark.asyncio
async def test_cli_create_passes_rm():
    fake = _FakeCli()
    adapter = _cli_adapter(fake)
    await adapter.create()

    run = fake.argv("run")
    assert "--rm" in run, "没有 --rm，entrypoint 到点自毁只会留下 Exited(0) 空壳"
    assert "-d" in run and "--name" in run
    await _drain_reap_tasks()


@pytest.mark.asyncio
async def test_sdk_create_sets_auto_remove():
    containers = _FakeContainers([])
    adapter = _sdk_adapter(containers)
    await adapter.create()

    assert containers.run_kwargs["auto_remove"] is True, "两后端行为契约一致，sdk 侧不能是 False"
    await _drain_reap_tasks()
