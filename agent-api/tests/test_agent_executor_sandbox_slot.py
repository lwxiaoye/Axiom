"""技能沙箱并发闸不泄漏名额（agent_executor 侧，2026-07-28）。

同类不变量在 tests/test_sandbox_semaphore_leak.py 已经为 sandbox_executor（主对话 execute_in_sandbox）
锁住了；本文件锁的是**子智能体/工作流 agent 节点**那条路径——它此前有两处泄漏：

① `_acquire_sandbox_slot` 用 `asyncio.wait_for(sem.acquire(), 60)`：外部取消（用户点
   「停止生成」/断连）时 wait_for 被取消，而内部 acquire() 稍后仍可能拿到名额 → 永不归还；
② `_setup_skill_sandbox` 只在 `except SandboxUnavailable` 分支里 `_release_sandbox_slot()`：
   `sandbox.create()` 抛 CancelledError（local_adapter 显式重抛，**不是** SandboxUnavailable）
   或本机无 docker 的 FileNotFoundError 时，名额永不归还。

累计到 SKILL_SANDBOX_MAX_CONCURRENT 后，所有含脚本技能的子智能体委派永久「沙箱繁忙」
直到进程重启。
"""
import asyncio

import pytest

from app.core.config import settings
from app.services.agents import agent_executor


@pytest.fixture(autouse=True)
def _fresh_semaphore():
    """每个用例用干净的信号量（模块级缓存会跨用例串味）。"""
    agent_executor._sandbox_semaphore = None
    agent_executor._sandbox_sem_loop = None
    agent_executor._sandbox_sem_limit = 0
    yield
    agent_executor._sandbox_semaphore = None
    agent_executor._sandbox_sem_loop = None
    agent_executor._sandbox_sem_limit = 0


def _set_limit(monkeypatch, limit: int):
    monkeypatch.setattr(settings, "SKILL_SANDBOX_MAX_CONCURRENT", limit, raising=False)
    agent_executor._sandbox_semaphore = None
    agent_executor._sandbox_sem_loop = None
    agent_executor._sandbox_sem_limit = 0


@pytest.mark.asyncio
async def test_slot_acquire_and_release_roundtrip(monkeypatch):
    _set_limit(monkeypatch, 2)
    assert await agent_executor._acquire_sandbox_slot() is True
    assert await agent_executor._acquire_sandbox_slot() is True
    agent_executor._release_sandbox_slot()
    agent_executor._release_sandbox_slot()
    # 归还干净：还能再取满
    assert await agent_executor._acquire_sandbox_slot() is True
    assert await agent_executor._acquire_sandbox_slot() is True


@pytest.mark.asyncio
async def test_zero_limit_means_unlimited(monkeypatch):
    _set_limit(monkeypatch, 0)
    assert agent_executor._get_sandbox_semaphore() is None
    assert await agent_executor._acquire_sandbox_slot() is True


@pytest.mark.asyncio
async def test_external_cancel_during_queue_does_not_leak_slot(monkeypatch):
    """用户点「停止生成」的真实形态：等名额的那个 task 被外部 cancel。

    旧的 wait_for 写法在这个时序下会出现「调用方被取消、名额却已到手」——名额永久丢失。
    """
    _set_limit(monkeypatch, 1)
    sem = agent_executor._get_sandbox_semaphore()
    await sem.acquire()  # 占满，让下面那个必须排队

    waiter = asyncio.create_task(agent_executor._acquire_sandbox_slot())
    await asyncio.sleep(0)  # 让它真的挂在 acquire 上
    waiter.cancel()
    # 与「取消刚好撞上名额到手」的窗口重叠
    sem.release()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    # 关键断言：容量必须完好（旧写法这里会拿不到）
    assert await agent_executor._acquire_sandbox_slot() is True
    agent_executor._release_sandbox_slot()


@pytest.mark.asyncio
async def test_queue_timeout_returns_false_without_leaking(monkeypatch):
    _set_limit(monkeypatch, 1)
    monkeypatch.setattr(agent_executor, "_SANDBOX_ACQUIRE_TIMEOUT", 0.05, raising=False)
    sem = agent_executor._get_sandbox_semaphore()
    await sem.acquire()
    assert await agent_executor._acquire_sandbox_slot() is False
    sem.release()
    assert await agent_executor._acquire_sandbox_slot() is True
    agent_executor._release_sandbox_slot()


@pytest.mark.asyncio
async def test_semaphore_rebuilt_when_limit_changes(monkeypatch):
    """配置改了上限必须重建（否则一直按旧容量跑）；换事件循环同理，语义与 sandbox_executor 一致。"""
    _set_limit(monkeypatch, 1)
    first = agent_executor._get_sandbox_semaphore()
    monkeypatch.setattr(settings, "SKILL_SANDBOX_MAX_CONCURRENT", 3, raising=False)
    second = agent_executor._get_sandbox_semaphore()
    assert first is not second


# ---------------- _setup_skill_sandbox 的统一 try/finally ----------------


class _FakeEngine:
    class _Ctx:
        user_id = "u-1"
        app_id = "app-1"

    ctx = _Ctx()

    def __init__(self, skills):
        self._skills = skills

    def input_value(self, node, key, default=None):
        if key == "skills":
            return self._skills
        return default


class _FakeSandbox:
    def __init__(self, create_exc=None):
        self._create_exc = create_exc
        self.deleted = False

    async def create(self):
        if self._create_exc is not None:
            raise self._create_exc

    async def delete(self):
        self.deleted = True


def _install_sandbox_stubs(monkeypatch, sandbox, deploy_exc=None):
    """把 _setup_skill_sandbox 内部延迟 import 的三个目标换成桩。

    只 setattr 真模块的属性，**绝不** monkeypatch.setitem(sys.modules, ...)：把
    `app.services.sandbox` 整个换成假包会污染同一进程里后续用例的 import 解析
    （实测会让 tests/test_workspace_sync.py 在全量跑时红两条，单独跑却是绿的）。
    """
    import types

    import app.routers.agent_skill as agent_skill_mod
    import app.services.sandbox as sandbox_mod
    import app.services.skills.skill_runtime as runtime_mod

    async def _load_skill_packages(skill_ids, owner_user_id=None):
        return [{"skillId": "s1", "name": "打包技能", "hasScripts": True}]

    async def _deploy_skills(sb, pkgs):
        if deploy_exc is not None:
            raise deploy_exc
        return [types.SimpleNamespace(skill_id="s1", name="打包技能", path="/p", error=None)]

    monkeypatch.setattr(agent_skill_mod, "load_skill_packages", _load_skill_packages)
    monkeypatch.setattr(sandbox_mod, "create_configured_sandbox", lambda **kwargs: sandbox)
    monkeypatch.setattr(runtime_mod, "deploy_skills", _deploy_skills)


@pytest.mark.asyncio
async def test_cancelled_during_create_returns_slot_and_kills_container(monkeypatch):
    """用户在起沙箱期间点「停止」：create() 抛 CancelledError（不是 SandboxUnavailable）。
    旧写法在这里名额永不归还，累积到上限后含脚本技能的委派永久失败。"""
    _set_limit(monkeypatch, 1)
    monkeypatch.setattr(settings, "SKILL_SANDBOX_ENABLED", True, raising=False)
    sandbox = _FakeSandbox(create_exc=asyncio.CancelledError())
    _install_sandbox_stubs(monkeypatch, sandbox)

    engine = _FakeEngine([{"skillId": "s1"}])
    with pytest.raises(asyncio.CancelledError):
        await agent_executor._setup_skill_sandbox(engine, {"nodeId": "n1"})

    assert sandbox.deleted is True, "取消路径没清掉可能已起来的孤儿容器"
    # 名额必须回来
    assert await agent_executor._acquire_sandbox_slot() is True
    agent_executor._release_sandbox_slot()


@pytest.mark.asyncio
async def test_non_sandbox_error_during_create_returns_slot(monkeypatch):
    """本机没有 docker：create() 抛 FileNotFoundError，同样不得漏名额。"""
    _set_limit(monkeypatch, 1)
    monkeypatch.setattr(settings, "SKILL_SANDBOX_ENABLED", True, raising=False)
    sandbox = _FakeSandbox(create_exc=FileNotFoundError("docker not found"))
    _install_sandbox_stubs(monkeypatch, sandbox)

    engine = _FakeEngine([{"skillId": "s1"}])
    with pytest.raises(FileNotFoundError):
        await agent_executor._setup_skill_sandbox(engine, {"nodeId": "n1"})
    assert await agent_executor._acquire_sandbox_slot() is True
    agent_executor._release_sandbox_slot()


@pytest.mark.asyncio
async def test_deploy_failure_returns_slot(monkeypatch):
    """回归：部署失败仍归还名额并删容器（旧写法这一条本来就对，别改坏）。"""
    _set_limit(monkeypatch, 1)
    monkeypatch.setattr(settings, "SKILL_SANDBOX_ENABLED", True, raising=False)
    sandbox = _FakeSandbox()
    _install_sandbox_stubs(monkeypatch, sandbox, deploy_exc=RuntimeError("解包失败"))

    engine = _FakeEngine([{"skillId": "s1"}])
    with pytest.raises(agent_executor.AgentToolError):
        await agent_executor._setup_skill_sandbox(engine, {"nodeId": "n1"})
    assert sandbox.deleted is True
    assert await agent_executor._acquire_sandbox_slot() is True
    agent_executor._release_sandbox_slot()


@pytest.mark.asyncio
async def test_success_hands_slot_over_to_caller(monkeypatch):
    """成功路径：名额与沙箱一起交给调用方（由 _execute_agent_node 的 finally 归还），
    这里**不能**提前归还，否则并发闸形同虚设。"""
    _set_limit(monkeypatch, 1)
    monkeypatch.setattr(settings, "SKILL_SANDBOX_ENABLED", True, raising=False)
    sandbox = _FakeSandbox()
    _install_sandbox_stubs(monkeypatch, sandbox)
    monkeypatch.setattr(agent_executor, "_SANDBOX_ACQUIRE_TIMEOUT", 0.05, raising=False)

    engine = _FakeEngine([{"skillId": "s1"}])
    got, deployed = await agent_executor._setup_skill_sandbox(engine, {"nodeId": "n1"})
    assert got is sandbox and deployed
    assert sandbox.deleted is False
    # 名额仍被占着
    assert await agent_executor._acquire_sandbox_slot() is False
    agent_executor._release_sandbox_slot()
