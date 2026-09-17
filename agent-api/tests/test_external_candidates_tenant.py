"""R6 外部兜底推荐的租户隔离（深扫 P0-3）。

原病灶：`app_capability_registry.external_candidates()` 只按 execution_scope=external_app +
enabled=1 过滤，完全不看调用者租户——`_upsert_external` 明明显式同步了 tenant_id。任一租户的
用户问「有什么能力」，都可能被推荐到别的租户的广场应用（名称 + 描述 + 直达 URL）。

修法：必传 tenant_id，语义严格对齐 published_visibility.tenant_matches
（应用侧 NULL/''/'0' = 全租户可见；真实租户严格相等）。
"""
import pytest
import pytest_asyncio
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.core.database import Base
from app.models import AppInfoCapabilityRegistry
from app.services.agents import app_capability_registry


@compiles(MEDIUMTEXT, "sqlite")
def _mediumtext_on_sqlite(_element, _compiler, **_kw):
    # 测试用 sqlite 内存库承载 MySQL 模型：MEDIUMTEXT 按 TEXT 编译
    return "TEXT"


def _row(cap_code, tenant_id, *, enabled=1, scope="external_app"):
    return AppInfoCapabilityRegistry(
        id=cap_code,
        app_info_id=cap_code,
        tenant_id=tenant_id,
        capability_code=cap_code,
        capability_name=f"应用-{cap_code}",
        source_system="external_catalog",
        source_app_id=cap_code,
        route_description="描述",
        capability_type="subagent",
        execution_scope=scope,
        launch_mode="redirect",
        runtime_type="external_link",
        endpoint=f"https://example.com/{cap_code}",
        enabled=enabled,
    )


@pytest_asyncio.fixture
async def seeded(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(app_capability_registry, "async_session", factory)
    async with factory() as session:
        session.add_all([
            _row("t1-app", "1000"),          # 租户 1000 私有
            _row("t2-app", "2000"),          # 租户 2000 私有
            _row("global-zero", "0"),        # 占位租户 → 全租户可见
            _row("global-null", None),       # NULL → 全租户可见
            _row("global-blank", ""),        # '' → 全租户可见
            _row("t1-disabled", "1000", enabled=0),          # 停用：任何租户都不出
            _row("t1-internal", "1000", scope="campus_internal"),  # 非 external_app：不出
        ])
        await session.commit()
    yield
    await engine.dispose()


async def _ids(tenant_id):
    rows = await app_capability_registry.external_candidates(tenant_id=tenant_id)
    return {r["id"] for r in rows}


@pytest.mark.asyncio
async def test_other_tenant_private_app_is_invisible(seeded):
    """跨租户不可见：租户 1000 看不到租户 2000 的私有应用，反之亦然。"""
    ids_1000 = await _ids("1000")
    assert "t1-app" in ids_1000
    assert "t2-app" not in ids_1000

    ids_2000 = await _ids("2000")
    assert "t2-app" in ids_2000
    assert "t1-app" not in ids_2000


@pytest.mark.asyncio
async def test_placeholder_tenant_apps_are_visible_to_everyone(seeded):
    """全局可见：应用侧 '0' / NULL / '' 归一为占位租户，对所有租户可见（tenant_matches 语义）。"""
    globals_ = {"global-zero", "global-null", "global-blank"}
    for tenant in ("1000", "2000", "0", "", None):
        assert globals_ <= await _ids(tenant), tenant


@pytest.mark.asyncio
async def test_disabled_and_non_external_rows_stay_filtered(seeded):
    """既有过滤不回归：enabled=0 与非 external_app 作用域仍然不出候选。"""
    ids_1000 = await _ids("1000")
    assert "t1-disabled" not in ids_1000
    assert "t1-internal" not in ids_1000


@pytest.mark.asyncio
async def test_tenant_zero_caller_sees_only_global_apps(seeded):
    """调用者租户为 0（网关身份路径的固定值）只看得到全局应用，不越到真实租户。"""
    assert await _ids("0") == {"global-zero", "global-null", "global-blank"}


@pytest.mark.asyncio
async def test_tenant_id_is_a_required_keyword(seeded):
    """漏传租户即 TypeError，而不是静默跨租户返回全部。"""
    with pytest.raises(TypeError):
        await app_capability_registry.external_candidates()
