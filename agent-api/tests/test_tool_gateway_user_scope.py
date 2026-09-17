"""Tool Gateway 幂等键归属校验定向测试（深度扫描 P1）。

历史缺陷：execute() 按 idempotency_key 查行时不带 user_id 过滤，而该列在表上是**全局唯一**
且键值可由调用方给定（/gateway/execute 直接透传请求体）——
- 命中别人的 completed 行 → 把他人的 result_json 当缓存结果返回（结果泄露）；
- 命中别人的 approved/failed 行 → 抢占执行权，拿别人的审批放行执行自己的调用。
同文件的 get_call/approve/reject/list_calls 都显式校验 row.user_id，唯独 execute 漏了。

修复口径：execute() 查行后校验归属，别人的行按「键冲突」拒绝（不返回结果、不抢执行权、
不回 callId），首插竞争输家重读的分支同样校验。
"""
import pytest
import pytest_asyncio
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.core.database import Base
from app.models import ToolGatewayCall
from app.services.gateway import tool_gateway

KEY = "run-1:call-1:v1"


@compiles(MEDIUMTEXT, "sqlite")
def _mediumtext_on_sqlite(_element, _compiler, **_kw):
    return "TEXT"


@pytest_asyncio.fixture
async def sf(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tool_gateway, "async_session", factory)
    yield factory
    await engine.dispose()


async def _seed(sf, *, user_id, status, result_json=None, sensitive=0):
    async with sf() as s:
        s.add(ToolGatewayCall(
            id=f"row-{user_id}-{status}", idempotency_key=KEY, user_id=user_id,
            tool_name="submit_form", args_hash="h", sensitive=sensitive,
            status=status, result_json=result_json,
        ))
        await s.commit()


async def _rows(sf):
    from sqlalchemy import select
    async with sf() as s:
        return (await s.execute(select(ToolGatewayCall))).scalars().all()


class _Executor:
    def __init__(self):
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        return {"ok": True}


@pytest.mark.asyncio
async def test_other_users_completed_row_never_leaks_result(sf):
    await _seed(sf, user_id="user-a", status="completed", result_json='{"secret": "a 的报销单"}')
    ex = _Executor()

    res = await tool_gateway.execute(
        idempotency_key=KEY, user_id="user-b", tool_name="submit_form",
        args={"x": 1}, executor=ex,
    )

    assert res["status"] == "failed"
    assert "冲突" in res["error"]
    assert "result" not in res and "callId" not in res
    assert ex.calls == 0
    rows = await _rows(sf)
    assert len(rows) == 1 and rows[0].user_id == "user-a" and rows[0].status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["approved", "failed"])
async def test_other_users_row_cannot_be_seized_for_execution(sf, status):
    """approved/failed 是「可抢占执行权」分支——归属校验必须先于 CAS。"""
    await _seed(sf, user_id="user-a", status=status, sensitive=1)
    ex = _Executor()

    res = await tool_gateway.execute(
        idempotency_key=KEY, user_id="user-b", tool_name="submit_form",
        args={"x": 1}, sensitive=True, executor=ex,
    )

    assert res["status"] == "failed"
    assert ex.calls == 0, "绝不能用别人的审批放行执行自己的调用"
    rows = await _rows(sf)
    assert len(rows) == 1 and rows[0].status == status


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending_approval", "rejected", "running", "unknown"])
async def test_other_users_row_states_are_not_disclosed(sf, status):
    """等待/终态分支同样不能借幂等键窥探他人调用状态。"""
    await _seed(sf, user_id="user-a", status=status)
    res = await tool_gateway.execute(
        idempotency_key=KEY, user_id="user-b", tool_name="submit_form", args={}, executor=_Executor(),
    )
    assert res["status"] == "failed" and "callId" not in res


@pytest.mark.asyncio
async def test_insert_race_lost_to_other_user_is_rejected(sf, monkeypatch):
    """首插竞争输给**别的用户**：唯一索引冲突后的重读分支同样要按归属拒绝，
    否则并发窗口里换个路径照样能读到他人的 completed 结果。"""
    from sqlalchemy import text as sa_text

    await _seed(sf, user_id="user-a", status="completed", result_json='{"secret": "a 的报销单"}')

    real_select = tool_gateway.select
    state = {"blind": True}

    def _blind_first_select(*a, **kw):
        # 首次查询打成空 = 模拟「查的时候还没有、插的时候已被别人插入」的并发窗口
        stmt = real_select(*a, **kw)
        if state["blind"]:
            state["blind"] = False
            return stmt.where(sa_text("1=0"))
        return stmt

    monkeypatch.setattr(tool_gateway, "select", _blind_first_select)

    ex = _Executor()
    res = await tool_gateway.execute(
        idempotency_key=KEY, user_id="user-b", tool_name="submit_form", args={}, executor=ex,
    )

    assert state["blind"] is False, "没走到首次查询，用例失效"
    assert res["status"] == "failed" and "callId" not in res and "result" not in res
    assert ex.calls == 0
    rows = await _rows(sf)
    assert len(rows) == 1 and rows[0].user_id == "user-a"


# ---------- 回归：本人的行行为不变 ----------

@pytest.mark.asyncio
async def test_own_completed_row_still_returns_cache(sf):
    await _seed(sf, user_id="user-a", status="completed", result_json='{"n": 1}')
    ex = _Executor()
    res = await tool_gateway.execute(
        idempotency_key=KEY, user_id="user-a", tool_name="submit_form", args={}, executor=ex,
    )
    assert res["status"] == "completed" and res["cached"] is True and res["result"] == {"n": 1}
    assert ex.calls == 0


@pytest.mark.asyncio
async def test_own_approved_row_still_executes(sf):
    await _seed(sf, user_id="user-a", status="approved", sensitive=1)
    ex = _Executor()
    res = await tool_gateway.execute(
        idempotency_key=KEY, user_id="user-a", tool_name="submit_form",
        args={}, sensitive=True, executor=ex,
    )
    assert res["status"] == "completed" and ex.calls == 1
    rows = await _rows(sf)
    assert len(rows) == 1 and rows[0].status == "completed"


@pytest.mark.asyncio
async def test_new_key_per_user_is_unaffected(sf):
    ex = _Executor()
    res = await tool_gateway.execute(
        idempotency_key="fresh-key", user_id="user-b", tool_name="submit_form", args={}, executor=ex,
    )
    assert res["status"] == "completed" and ex.calls == 1
