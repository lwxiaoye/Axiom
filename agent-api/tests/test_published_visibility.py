import json
import unittest
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.ext.compiler import compiles

from app.core.auth import UserContext
from app.core.database import Base
from app.models import WorkflowApp, WorkflowDefinition, WorkflowVersion
from app.services.agents import published_visibility, subagent_service
from app.services.agents.published_visibility import publish_visibility_allows_user


@compiles(MEDIUMTEXT, "sqlite")
def _mediumtext_on_sqlite(_element, _compiler, **_kw):
    # 测试用 sqlite 内存库承载 MySQL 模型：MEDIUMTEXT 按 TEXT 编译
    return "TEXT"


class PublishedVisibilityTest(unittest.TestCase):
    def test_empty_visibility_denies_non_owner_user(self):
        user = UserContext(user_id="u1", username="lisi", role_ids=[], dept_ids=[])

        self.assertFalse(publish_visibility_allows_user([], [], user))

    def test_role_or_dept_match_allows_user(self):
        user = UserContext(user_id="u1", username="lisi", role_ids=["role-a"], dept_ids=["dept-b"])

        self.assertTrue(publish_visibility_allows_user(["role-a"], [], user))
        self.assertTrue(publish_visibility_allows_user([], ["dept-b"], user))

    def test_database_relation_ids_are_used_for_matching(self):
        user = UserContext(user_id="u1", username="lisi", role_ids=["role-code"], dept_ids=["dept-code"])

        self.assertTrue(
            publish_visibility_allows_user(
                ["role-id"],
                ["dept-id"],
                user,
                relation_role_ids=["role-id"],
                relation_dept_ids=[],
            )
        )
        self.assertTrue(
            publish_visibility_allows_user(
                ["role-id"],
                ["dept-id"],
                user,
                relation_role_ids=[],
                relation_dept_ids=["dept-id"],
            )
        )

    def test_non_matching_visibility_denies_user(self):
        user = UserContext(user_id="u1", username="lisi", role_ids=["role-a"], dept_ids=["dept-b"])

        self.assertFalse(publish_visibility_allows_user(["role-x"], ["dept-x"], user))

    def test_collaboration_role_does_not_grant_published_app_run_access(self):
        """运行权限只匹配发布时的可见角色，不能误用协作 ACL 的角色。"""
        user = UserContext(user_id="u1", username="lisi", role_ids=["collaborator-role"])

        self.assertFalse(publish_visibility_allows_user(["publisher-role"], [], user))
        user.role_ids.append("publisher-role")
        self.assertTrue(publish_visibility_allows_user(["publisher-role"], [], user))


# ---------- deny-by-default（2026-07-22 语义发现升级）----------

@pytest.mark.asyncio
async def test_user_can_run_denies_when_no_approved_version(monkeypatch):
    """无 approved 版本 → 非 owner 拒绝（不再沿用「无版本默认所有人可用」）。"""
    async def _none(_s, _app_id):
        return None
    monkeypatch.setattr(published_visibility, "load_published_visibility_version", _none)
    app = SimpleNamespace(id="a1", owner_user_id="owner-x", status="published")
    user = UserContext(user_id="u1", username="u1")

    assert not await published_visibility.user_can_run_published_app(None, app, user)
    # owner 不受影响
    owner = UserContext(user_id="owner-x", username="o")
    assert await published_visibility.user_can_run_published_app(None, app, owner)


@pytest.mark.asyncio
async def test_user_can_run_denies_unpublished_owner_before_owner_shortcut():
    """owner 也只能执行 published 应用；公共闸门覆盖首次、流式与 HITL 恢复。"""
    app = SimpleNamespace(id="a1", owner_user_id="owner-x", status="draft")
    owner = UserContext(user_id="owner-x", username="o")

    assert not await published_visibility.user_can_run_published_app(None, app, owner)


@pytest.mark.asyncio
async def test_user_can_run_allows_cross_tenant_published_role_while_isolation_is_disabled(monkeypatch):
    """当前阶段：发布角色命中即可运行，智能体/工作流不再按租户拒绝。"""
    async def _version(_s, _app_id):
        return SimpleNamespace(visible_role_ids='["r1"]', visible_dept_ids="[]")
    monkeypatch.setattr(published_visibility, "load_published_visibility_version", _version)

    async def _rel(_s, _u):
        return [], []
    monkeypatch.setattr(published_visibility, "load_user_relation_ids", _rel)

    app = SimpleNamespace(id="a1", owner_user_id="owner-x", status="published",
                          tenant_id="tenant-b")
    role_user = UserContext(user_id="u1", username="u1", role_ids=["r1"], tenant_id="tenant-a")
    assert await published_visibility.user_can_run_published_app(None, app, role_user)
    owner = UserContext(user_id="owner-x", username="o", tenant_id="tenant-a")
    assert await published_visibility.user_can_run_published_app(None, app, owner)

    # 同租户恢复正常语义（NULL/'' 归一为 '0'）
    same = UserContext(user_id="u1", username="u1", role_ids=["r1"], tenant_id="tenant-b")
    assert await published_visibility.user_can_run_published_app(None, app, same)
    app0 = SimpleNamespace(id="a2", owner_user_id="owner-x", status="published", tenant_id=None)
    owner0 = UserContext(user_id="owner-x", username="o", tenant_id="0")
    assert await published_visibility.user_can_run_published_app(None, app0, owner0)


@pytest.mark.asyncio
async def test_user_can_run_restores_tenant_rejection_when_switch_is_reenabled(monkeypatch):
    async def _version(_s, _app_id):
        return SimpleNamespace(visible_role_ids='["r1"]', visible_dept_ids="[]")

    async def _rel(_s, _u):
        return [], []

    monkeypatch.setattr(published_visibility, "load_published_visibility_version", _version)
    monkeypatch.setattr(published_visibility, "load_user_relation_ids", _rel)
    monkeypatch.setattr(published_visibility.settings, "AGENT_WORKFLOW_TENANT_ISOLATION_ENABLED", True)
    app = SimpleNamespace(id="a1", owner_user_id="owner-x", status="published", tenant_id="tenant-b")
    user = UserContext(user_id="u1", username="u1", role_ids=["r1"], tenant_id="tenant-a")

    assert not await published_visibility.user_can_run_published_app(None, app, user)


# ---------- 完整 ACL 全集 list_callable_subagent_ids（sqlite 真 SQL）----------

def _user(uid="u1", roles=None, depts=None, tenant="0"):
    return UserContext(
        user_id=uid, username=uid,
        role_ids=list(roles or []), dept_ids=list(depts or []), tenant_id=tenant,
    )


@pytest_asyncio.fixture
async def sf(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("CREATE TABLE sys_user_role (user_id TEXT, role_id TEXT)"))
        await conn.execute(text("CREATE TABLE sys_user_depart (user_id TEXT, dep_id TEXT)"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(subagent_service, "async_session", factory)
    yield factory
    await engine.dispose()


async def _seed(sf, app_id, *, owner="owner-1", status="published", app_type="chatAgent",
                tenant="0", published_json='{"nodes":[]}', published_version=1,
                with_version=True, version_status="approved", roles=None, depts=None,
                name=None):
    async with sf() as s:
        s.add(WorkflowApp(
            id=app_id, tenant_id=tenant, ai_app_type=app_type, name=name or app_id,
            description="", status=status, owner_user_id=owner,
        ))
        s.add(WorkflowDefinition(
            id=f"d-{app_id}", app_id=app_id,
            published_json=published_json, published_version=published_version,
        ))
        if with_version:
            s.add(WorkflowVersion(
                id=f"v-{app_id}", app_id=app_id, version_no=published_version or 1,
                status=version_status,
                visible_role_ids=json.dumps(list(roles or [])),
                visible_dept_ids=json.dumps(list(depts or [])),
            ))
        await s.commit()


@pytest.mark.asyncio
async def test_owner_sees_own_published_app(sf):
    await _seed(sf, "own-1", owner="u1", roles=[], depts=[])
    assert await subagent_service.list_callable_subagent_ids(_user("u1")) == {"own-1"}


@pytest.mark.asyncio
async def test_role_match_allows(sf):
    await _seed(sf, "role-app", roles=["r-9"])
    assert "role-app" in await subagent_service.list_callable_subagent_ids(
        _user("u1", roles=["r-9"])
    )


@pytest.mark.asyncio
async def test_dept_match_allows(sf):
    await _seed(sf, "dept-app", depts=["dep-3"])
    assert "dept-app" in await subagent_service.list_callable_subagent_ids(
        _user("u1", depts=["dep-3"])
    )


@pytest.mark.asyncio
async def test_no_role_or_dept_match_denies(sf):
    await _seed(sf, "far-app", roles=["r-x"], depts=["dep-x"])
    assert await subagent_service.list_callable_subagent_ids(
        _user("u1", roles=["r-other"], depts=["dep-other"])
    ) == set()


@pytest.mark.asyncio
async def test_empty_visibility_denies_non_owner(sf):
    await _seed(sf, "empty-vis", roles=[], depts=[])
    assert await subagent_service.list_callable_subagent_ids(_user("u1")) == set()


@pytest.mark.asyncio
async def test_no_approved_version_denies(sf):
    await _seed(sf, "no-ver", with_version=False, roles=["r-1"])
    await _seed(sf, "pending-ver", version_status="pending_review", roles=["r-1"])
    assert await subagent_service.list_callable_subagent_ids(
        _user("u1", roles=["r-1"])
    ) == set()


@pytest.mark.asyncio
async def test_missing_published_json_or_version_pointer_denies(sf):
    await _seed(sf, "no-json", published_json=None, roles=["r-1"])
    await _seed(sf, "empty-json", published_json="", roles=["r-1"])
    await _seed(sf, "zero-ver", published_version=0, roles=["r-1"])
    assert await subagent_service.list_callable_subagent_ids(
        _user("u1", roles=["r-1"])
    ) == set()


@pytest.mark.asyncio
async def test_draft_and_unpublished_status_denied(sf):
    await _seed(sf, "draft-app", status="draft", roles=["r-1"])
    await _seed(sf, "off-app", status="unpublished", roles=["r-1"])
    await _seed(sf, "tool-app", app_type="workflowTool", roles=["r-1"])
    assert await subagent_service.list_callable_subagent_ids(
        _user("u1", roles=["r-1"])
    ) == set()


@pytest.mark.asyncio
async def test_user_isolation_applies_to_discovery_and_direct_subagent_id(sf):
    """列表不可见时，伪造 subagent_id 也不得绕过；owner 草稿同样拒绝。"""
    await _seed(sf, "owner-draft", owner="owner-1", status="draft", tenant="tenant-a")
    await _seed(
        sf,
        "shared-approved",
        owner="owner-2",
        tenant="tenant-a",
        roles=["delegation-role"],
    )

    owner = _user("owner-1", tenant="tenant-a")
    allowed = _user("allowed-user", roles=["delegation-role"], tenant="tenant-a")
    hidden = _user("hidden-user", roles=["other-role"], tenant="tenant-a")
    cross_tenant = _user("cross-tenant", roles=["delegation-role"], tenant="tenant-b")

    assert "owner-draft" not in await subagent_service.list_callable_subagent_ids(owner)
    assert await subagent_service._resolve_accessible(owner, "owner-draft") is None

    assert "shared-approved" in await subagent_service.list_callable_subagent_ids(allowed)
    assert await subagent_service._resolve_accessible(allowed, "shared-approved") is not None

    assert "shared-approved" not in await subagent_service.list_callable_subagent_ids(hidden)
    assert await subagent_service._resolve_accessible(hidden, "shared-approved") is None
    assert "shared-approved" in await subagent_service.list_callable_subagent_ids(cross_tenant)
    assert await subagent_service._resolve_accessible(cross_tenant, "shared-approved") is not None


@pytest.mark.asyncio
async def test_tenant_mismatch_allows_when_published_role_matches(sf):
    await _seed(sf, "tenant-2-app", tenant="t2", roles=["r-1"])
    assert "tenant-2-app" in await subagent_service.list_callable_subagent_ids(
        _user("u1", roles=["r-1"], tenant="0")
    )
    assert "tenant-2-app" in await subagent_service.list_callable_subagent_ids(
        _user("u1", roles=["r-1"], tenant="t2")
    )


@pytest.mark.asyncio
async def test_full_set_not_truncated_by_update_time(sf):
    """60 个可见应用全部返回：不再有 limit 200 / 前 50 / 前 20 截断。"""
    for i in range(60):
        await _seed(sf, f"bulk-{i}", roles=["r-1"])
    allowed = await subagent_service.list_callable_subagent_ids(_user("u1", roles=["r-1"]))
    assert len(allowed) == 60
    rows = await subagent_service.list_subagents(_user("u1", roles=["r-1"]))
    assert len(rows) == 60


@pytest.mark.asyncio
async def test_app_role_table_no_longer_grants_recall(sf):
    """反转（2026-07-28 用户拍板）：app_role/app_dept 不再是召回的授权来源。

    原用例断言「表命中即放行、不退回版本判定」。但执行侧 user_can_run_published_app
    只读版本上的 visible_*，从不读那两张表——这条快路放进候选的恰好是执行必拒的那批。
    现在召回与执行同一把尺子：版本可见性不命中就不进候选（改由推荐卡兜底）。
    """
    await _seed(sf, "fast-app", roles=["r-other"])  # 版本可见性不命中该用户

    assert "fast-app" not in await subagent_service.list_callable_subagent_ids(_user("u1")), (
        "执行侧会拒绝的应用不得进入委派候选")


@pytest.mark.asyncio
async def test_version_pointer_mismatch_denies_no_fallback(sf):
    """复审 #3：published_version 指针存在但匹配不到 approved 版本 → 拒绝，
    不得回退旧 approved 版本的 ACL（否则权限与实际执行版本错位）。"""
    # 指针指 v5，但只有 v3 是 approved（角色本可命中）
    await _seed(sf, "drift-app", published_version=5, with_version=False, roles=["r-1"])
    async with sf() as s:
        s.add(WorkflowVersion(
            id="v3-drift-app", app_id="drift-app", version_no=3, status="approved",
            visible_role_ids=json.dumps(["r-1"]), visible_dept_ids="[]",
        ))
        await s.commit()

    # 全集批量路径拒绝
    assert await subagent_service.list_callable_subagent_ids(
        _user("u1", roles=["r-1"])
    ) == set()
    # 单应用权威版本加载同样返回 None
    async with sf() as s:
        assert await published_visibility.load_published_visibility_version(s, "drift-app") is None


@pytest.mark.asyncio
async def test_db_relation_ids_merge_into_matching(sf):
    """UserContext 里没有、但 sys_user_role/sys_user_depart 里有的关系也参与匹配。"""
    await _seed(sf, "rel-app", roles=["r-db"])
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO sys_user_role (user_id, role_id) VALUES ('u1', 'r-db')"
        ))
        await s.commit()
    assert "rel-app" in await subagent_service.list_callable_subagent_ids(_user("u1"))


if __name__ == "__main__":
    unittest.main()
