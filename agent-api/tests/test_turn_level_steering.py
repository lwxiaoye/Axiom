"""轮级引导注入（Codex「提交，但不中断模型运行」）。

覆盖 model_driver._inject_run_inputs 的语义契约——它是本功能的内核：
把运行中提交的引导，在工具轮边界作为 role:user 追加进正在跑的这一轮的 messages，
不中止当前轮、不另起 turn。

存储层用真实 Runtime PG（库未配置则 skip）；注入逻辑本身不依赖 LLM，直接驱动。
"""
import pytest

from app.core.runtime_db import init_runtime_tables
from app.services.agent_harness import model_driver
from app.services.tasks import run_input_service as ri

pytestmark = pytest.mark.asyncio

_RUN = "utest-steer-run"
_THREAD = "utest-steer-thread"
_USER = "utest-steer-user"


async def _db_ready() -> bool:
    # Tests may use an already-migrated Runtime PG, but must never run startup DDL against the
    # shared development database: create_all cannot execute historical Alembic backfills.
    try:
        ready = await init_runtime_tables(ddl=False)
    except RuntimeError:
        return False
    if not ready:
        return False
    from app.core.runtime_db import runtime_session
    from app.runtime_models import AgentRun
    factory = runtime_session()
    async with factory() as session:
        run = await session.get(AgentRun, _RUN)
        if run is None:
            session.add(AgentRun(
                id=_RUN, thread_id=_THREAD, user_id=_USER,
                status="running", dispatch_mode="chat",
            ))
        else:
            run.status = "running"
            run.input_intake_closed_at = None
        await session.commit()
    return True


async def _drain(gateway, messages):
    """跑一遍注入，收集它 yield 的事件。"""
    return [ev async for ev in model_driver._inject_run_inputs(gateway, messages)]


async def _cleanup():
    """真删本 Run 的指令行——用 finish(withdrawn) 收尾的话 list_for_run 仍会返回它们，
    上一个用例的残留会污染下一个用例的断言。"""
    from sqlalchemy import text as sa_text

    from app.core.runtime_db import runtime_session
    factory = runtime_session()
    if factory is None:
        return
    async with factory() as session:
        await session.execute(
            sa_text("DELETE FROM agent_run_inputs WHERE run_id = :r"), {"r": _RUN})
        await session.commit()


def _gateway(**over):
    base = {"thread_id": _THREAD, "user_id": _USER, "run_id": _RUN, "steerable": True}
    base.update(over)
    return base


async def test_injects_pending_instruction_into_running_turn():
    """核心路径：引导作为 role:user 落到 messages 尾部，并标记 applied(scope=turn)。"""
    if not await _db_ready():
        pytest.skip("Runtime DB 未配置")
    await _cleanup()
    submitted = await ri.submit(run_id=_RUN, thread_id=_THREAD, user_id=_USER,
                                content="改成只统计数量就行")
    assert submitted is not None

    # 模拟工具轮边界的 messages：assistant tool_calls 与 tool 结果已配对完整
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "原始任务"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                                                             "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "文件内容"},
    ]
    before = len(messages)
    events = await _drain(_gateway(), messages)

    assert len(messages) == before + 1, "引导必须追加，不能替换既有消息"
    injected = messages[-1]
    assert injected["role"] == "user"
    assert "改成只统计数量就行" in injected["content"]
    # 前面的 tool_call/tool 结果配对不被破坏（否则下一次 LLM 请求协议非法）
    assert messages[-2]["role"] == "tool" and messages[-2]["tool_call_id"] == "c1"

    assert len(events) == 1
    assert events[0]["type"] == "input_applied"
    assert events[0]["scope"] == "turn"

    rows = {r["id"]: r for r in await ri.list_for_run(_RUN)}
    assert rows[submitted["id"]]["status"] == "applied"
    assert rows[submitted["id"]]["appliedAtSafePoint"] == "turn"
    await _cleanup()


async def test_consumed_once_not_replayed_every_step():
    """同一条引导只被吸收一次——否则每个工具轮都会重复追加同一段话。"""
    if not await _db_ready():
        pytest.skip("Runtime DB 未配置")
    await _cleanup()
    await ri.submit(run_id=_RUN, thread_id=_THREAD, user_id=_USER, content="只要结论")

    messages: list = []
    first = await _drain(_gateway(), messages)
    second = await _drain(_gateway(), messages)

    assert len(first) == 1
    assert second == [], "第二个轮边界不该再吸收同一条"
    assert len(messages) == 1
    await _cleanup()


async def test_non_steerable_gateway_is_skipped():
    """DAG 节点内嵌 loop（无 steerable 标记）不认领：并行节点共享同一 run_id，
    谁吸收了引导会变得不确定，任务模式的引导语义固定在图边界。"""
    if not await _db_ready():
        pytest.skip("Runtime DB 未配置")
    await _cleanup()
    await ri.submit(run_id=_RUN, thread_id=_THREAD, user_id=_USER, content="节点不该吃掉它")

    messages: list = []
    gw = _gateway()
    gw.pop("steerable")
    assert await _drain(gw, messages) == []
    assert messages == []

    # 仍留在 queued，等根循环/图边界消费
    assert [r["status"] for r in await ri.list_for_run(_RUN)] == ["queued"]
    await _cleanup()


async def test_no_run_id_or_no_pending_is_noop():
    if not await _db_ready():
        pytest.skip("Runtime DB 未配置")
    await _cleanup()
    messages: list = []
    assert await _drain(_gateway(run_id=""), messages) == []
    assert await _drain(None, messages) == []
    # steerable 但队列为空：不该有任何副作用
    assert await _drain(_gateway(), messages) == []
    assert messages == []


async def test_multiple_instructions_absorbed_in_order():
    """连点多条：同一个轮边界按提交顺序一次性吸收，不摊到很多轮之后才生效。"""
    if not await _db_ready():
        pytest.skip("Runtime DB 未配置")
    await _cleanup()
    for text in ("第一条", "第二条", "第三条"):
        await ri.submit(run_id=_RUN, thread_id=_THREAD, user_id=_USER, content=text)

    messages: list = []
    events = await _drain(_gateway(), messages)

    assert len(events) == 3
    bodies = [m["content"] for m in messages]
    assert [("第一条" in b) for b in bodies] == [True, False, False]
    assert "第二条" in bodies[1] and "第三条" in bodies[2]
    await _cleanup()


async def test_one_at_a_time_mode_absorbs_single_instruction_per_boundary(monkeypatch):
    """RUN_INPUT_DRAIN_MODE=one_at_a_time（pi 的 QueueMode 语义）：一个轮边界只吸一条，
    连发三条要经过三个边界逐条生效——每条都得到一次完整的「读到→反应」循环，顺序不变、
    一条不丢。默认 all 模式的行为由上面的用例锁住。"""
    if not await _db_ready():
        pytest.skip("Runtime DB 未配置")
    from app.core.config import settings as app_settings
    monkeypatch.setattr(app_settings, "RUN_INPUT_DRAIN_MODE", "one_at_a_time", raising=False)
    await _cleanup()
    for text in ("第一条", "第二条", "第三条"):
        await ri.submit(run_id=_RUN, thread_id=_THREAD, user_id=_USER, content=text)

    messages: list = []
    first = await _drain(_gateway(), messages)
    assert len(first) == 1 and "第一条" in messages[-1]["content"]

    second = await _drain(_gateway(), messages)
    assert len(second) == 1 and "第二条" in messages[-1]["content"]

    third = await _drain(_gateway(), messages)
    assert len(third) == 1 and "第三条" in messages[-1]["content"]

    assert await _drain(_gateway(), messages) == []
    assert len(messages) == 3

    statuses = {r["content"]: r["status"] for r in await ri.list_for_run(_RUN)}
    assert set(statuses.values()) == {"applied"}
    await _cleanup()


async def test_stale_applying_is_reclaimed():
    """持有方崩溃留下的悬挂 applying 会永久阻塞该 Run 的后续引导——超时后必须打回 queued。"""
    if not await _db_ready():
        pytest.skip("Runtime DB 未配置")
    await _cleanup()
    submitted = await ri.submit(run_id=_RUN, thread_id=_THREAD, user_id=_USER, content="别卡住")
    assert submitted is not None

    # 认领后「崩溃」（不 finish）
    claimed = await ri.claim_next(run_id=_RUN)
    assert claimed is not None and claimed["id"] == submitted["id"]
    # 同一 Run 此刻被 applying 挡住
    assert await ri.claim_next(run_id=_RUN) is None

    # 把 applying_at 推到回收阈值之前，模拟长时间悬挂
    from sqlalchemy import text as sa_text
    from app.core.runtime_db import runtime_session
    factory = runtime_session()
    async with factory() as session:
        await session.execute(sa_text(
            "UPDATE agent_run_inputs SET applying_at = now() - interval '2 hours' "
            "WHERE id = :i"), {"i": submitted["id"]})
        await session.commit()

    again = await ri.claim_next(run_id=_RUN)
    assert again is not None and again["id"] == submitted["id"], "悬挂项应被回收后重新认领"
    await _cleanup()
