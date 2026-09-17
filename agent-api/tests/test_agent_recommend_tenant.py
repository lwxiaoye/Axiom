"""内部智能体推荐（向量召回）的租户隔离（深扫 P0，同源于 test_external_candidates_tenant）。

原病灶：外部兜底推荐 `external_candidates` 已按租户过滤，但**先跑的那条**——广场智能体
向量召回（backfill → Qdrant → vector_service.search → _retrieve_agents）全链路没有租户维度：

  - backfill_service.fetch_app_rows 的 SELECT 不取 app_info.tenant_id；
  - vector_service.upsert_agents 的 payload 12 个字段里没有 tenant_id；
  - vector_service.search 的 filter 只有 status/published + is_public/role_ids/dept_ids。

放大因子：回填把 role_ids/dept_ids 一律留空 → _resolve_is_public 判 True → 所有租户的
应用统一「公开」。命中即出推荐卡，且召回结果整段写进每一轮的系统提示词（智能体目录），
所以这是跨租户的应用名 + 描述泄露，不只是推荐卡不准。

本文件覆盖：filter 口径、端到端可见性（含 admin 不豁免租户）、payload 写入、
Java 同步契约向后兼容、回填的租户取值、以及 payload 口径版本（AGENT_INDEX_VERSION）
——旧点位没有 tenant_id 字段时 Qdrant MatchAny 不命中，不 bump 版本就会召回恒空。
"""
import pytest

from app.schemas.schemas import AgentSyncAgent
from app.services.agents import agent_sync_service
from app.services.knowledge import vector_service
from app.services.platform import backfill_service


# ---------- 迷你 Qdrant：按真实语义求值 Filter，验证「过滤后到底剩谁」 ----------

def _match_ok(condition, payload: dict) -> bool:
    value = payload.get(condition.key)
    match = condition.match
    if hasattr(match, "any"):
        # MatchAny：字段缺失 → 不命中（这正是旧 payload 必须靠版本 bump 重建的原因）
        if value is None:
            return False
        if isinstance(value, (list, tuple, set)):
            return bool(set(map(str, value)) & set(map(str, match.any)))
        return str(value) in set(map(str, match.any))
    return value == match.value


def _filter_ok(flt, payload: dict) -> bool:
    for cond in (getattr(flt, "must", None) or []):
        if hasattr(cond, "key"):
            if not _match_ok(cond, payload):
                return False
        elif not _filter_ok(cond, payload):
            return False
    should = getattr(flt, "should", None) or []
    if should and not any(
        _match_ok(c, payload) if hasattr(c, "key") else _filter_ok(c, payload) for c in should
    ):
        return False
    for cond in (getattr(flt, "must_not", None) or []):
        if _match_ok(cond, payload) if hasattr(cond, "key") else _filter_ok(cond, payload):
            return False
    return True


class _Hit:
    def __init__(self, payload):
        self.payload = payload
        self.score = 0.9


class _FakeClient:
    """只做 filter 求值 + 记录入参；不做向量相似度（推荐链路没有阈值以外的排序诉求）。"""

    def __init__(self, points=None):
        self.points = list(points or [])
        self.last_kwargs: dict = {}
        self.upserted: list = []
        self.set_payloads: list = []

    async def search(self, **kw):
        self.last_kwargs = kw
        flt = kw.get("query_filter")
        return [_Hit(p) for p in self.points if flt is None or _filter_ok(flt, p)][: kw.get("limit", 10)]

    async def upsert(self, collection_name, points):
        self.upserted.extend(points)

    async def set_payload(self, collection_name, payload, points):
        self.set_payloads.append(payload)


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(vector_service, "_get_client", lambda: client)
    return client


def _point(agent_id, tenant_id="0", **over):
    p = {
        "agent_id": agent_id,
        "tenant_id": tenant_id,
        "name": agent_id,
        "description": "",
        "status": 1,
        "published": True,
        "is_public": True,
        "role_ids": [],
        "dept_ids": [],
    }
    p.update(over)
    return p


async def _search_ids(tenant_id, *, is_admin=False, roles=None, depts=None):
    hits = await vector_service.search(
        collection="col",
        query_vec=[0.1, 0.2],
        user_role_ids=list(roles or []),
        user_dept_ids=list(depts or []),
        is_admin_user=is_admin,
        top_k=50,
        tenant_id=tenant_id,
    )
    return {h["id"] for h in hits}


# ---------- ① filter 口径与 search_routes / tenant_visible_clause 对齐 ----------

@pytest.mark.asyncio
async def test_search_filter_omits_tenant_while_workflow_isolation_is_disabled(fake_client):
    await _search_ids("1000")
    keys = [getattr(c, "key", "") for c in fake_client.last_kwargs["query_filter"].must]
    assert "tenant_id" not in keys


@pytest.mark.asyncio
async def test_search_filter_returns_when_workflow_isolation_is_reenabled(fake_client, monkeypatch):
    monkeypatch.setattr(vector_service.settings, "AGENT_WORKFLOW_TENANT_ISOLATION_ENABLED", True)
    await _search_ids("1000")
    condition = next(
        c for c in fake_client.last_kwargs["query_filter"].must
        if getattr(c, "key", "") == "tenant_id"
    )
    assert set(condition.match.any) == {"0", "1000"}


@pytest.mark.asyncio
async def test_admin_keeps_acl_bypass_without_a_tenant_filter(fake_client):
    await _search_ids("1000", is_admin=True)
    keys = [getattr(c, "key", "") for c in fake_client.last_kwargs["query_filter"].must]
    assert "tenant_id" not in keys
    # 管理员分支确实跳过了 ACL（否则这条断言无法区分「租户生效」与「整体没走 admin 分支」）
    assert not any(getattr(c, "should", None) for c in fake_client.last_kwargs["query_filter"].must)


# ---------- ② 端到端可见性 ----------

@pytest.mark.asyncio
async def test_other_tenant_agents_are_visible_while_isolation_is_disabled(fake_client):
    fake_client.points = [
        _point("a-1000", "1000"), _point("a-2000", "2000"), _point("a-global", "0"),
    ]
    assert await _search_ids("1000") == {"a-1000", "a-2000", "a-global"}
    assert await _search_ids("2000") == {"a-1000", "a-2000", "a-global"}


@pytest.mark.asyncio
async def test_global_tenant_agents_visible_to_everyone(fake_client):
    fake_client.points = [_point("a-global", "0"), _point("a-1000", "1000")]
    for tenant in ("0", "1000", "2000", "", None):
        assert "a-global" in await _search_ids(tenant), tenant


@pytest.mark.asyncio
async def test_tenant_zero_caller_can_discover_published_agents(fake_client):
    fake_client.points = [_point("a-global", "0"), _point("a-1000", "1000"), _point("a-2000", "2000")]
    assert await _search_ids("0") == {"a-global", "a-1000", "a-2000"}


@pytest.mark.asyncio
async def test_existing_acl_and_status_filters_do_not_regress(fake_client):
    """既有过滤不回归：未发布/停用/非公开且 ACL 不命中的点位仍被挡掉。"""
    fake_client.points = [
        _point("ok", "1000"),
        _point("unpublished", "1000", published=False),
        _point("disabled", "1000", status=0),
        _point("private", "1000", is_public=False, role_ids=["r9"]),
        _point("by-role", "1000", is_public=False, role_ids=["r1"]),
        _point("by-dept", "1000", is_public=False, dept_ids=["d1"]),
    ]
    assert await _search_ids("1000", roles=["r1"], depts=["d1"]) == {"ok", "by-role", "by-dept"}


@pytest.mark.asyncio
async def test_legacy_payload_without_tenant_field_remains_discoverable(fake_client):
    legacy = _point("legacy", "0")
    legacy.pop("tenant_id")
    fake_client.points = [legacy, _point("fresh", "0")]
    assert await _search_ids("0") == {"fresh", "legacy"}
    assert await _search_ids("1000") == {"fresh", "legacy"}


# ---------- ③ payload 口径版本（回填触发机制） ----------

def test_collection_name_carries_index_version():
    """集合名带 payload 口径版本：bump 即换集合 → 新集合为空 → 自动回填重建。"""
    name = vector_service.collection_name("bge-m3", 1024)
    assert name.startswith(f"agents_v{vector_service.AGENT_INDEX_VERSION}_")
    assert vector_service.AGENT_INDEX_VERSION >= 4  # tenant_id 口径起于 v4，不允许回退
    # 同模型不同版本必须是不同集合（否则旧点位会被新口径的 filter 全部滤掉）
    assert name != f"agents_v{vector_service.AGENT_INDEX_VERSION - 1}_" + name.split("_", 2)[2]


# ---------- ④ 写入侧：payload 真的带上租户 ----------

@pytest.mark.asyncio
async def test_upsert_and_update_payload_write_tenant(fake_client):
    await vector_service.upsert_agents("col", [
        {"agent_id": "a1", "tenant_id": "1000", "name": "n", "vector": [0.1]},
        {"agent_id": "a2", "name": "n", "vector": [0.1]},          # 缺省 → 全局 '0'
        {"agent_id": "a3", "tenant_id": None, "name": "n", "vector": [0.1]},
        {"agent_id": "a4", "tenant_id": "  ", "name": "n", "vector": [0.1]},
    ])
    got = {p.payload["agent_id"]: p.payload["tenant_id"] for p in fake_client.upserted}
    assert got == {"a1": "1000", "a2": "0", "a3": "0", "a4": "0"}

    # 内容未变、只更新元数据的分支同样要写租户，否则点位会停留在旧租户值上
    await vector_service.update_agent_payload("col", {"agent_id": "a1", "tenant_id": "1000", "name": "n"})
    assert fake_client.set_payloads[-1]["tenant_id"] == "1000"


def test_agent_sync_payload_carries_tenant():
    agent = AgentSyncAgent(id="a1", tenant_id="1000", name="n", source_version=1)
    assert agent_sync_service._agent_payload(agent, "h", 1)["tenant_id"] == "1000"


def test_java_bulk_sync_contract_is_backward_compatible():
    """旧版 Java /internal/agents/bulk-sync 不传 tenant_id 时落全局 '0'，不报 422。"""
    agent = AgentSyncAgent(id="a1", name="n", source_version=1)
    assert agent.tenant_id == "0"
    assert agent_sync_service._agent_payload(agent, "h", 1)["tenant_id"] == "0"

    blank = AgentSyncAgent(id="a2", tenant_id="", name="n", source_version=1)
    assert agent_sync_service._agent_payload(blank, "h", 1)["tenant_id"] == "0"


# ---------- ⑤ 回填侧：app_info.tenant_id → AgentSyncAgent ----------

def test_backfill_maps_tenant_from_app_info():
    rows = [
        {"id": 1, "name": "本租户", "status": 1, "tenant_id": "1000"},
        {"id": 2, "name": "全局", "status": 1, "tenant_id": None},
        {"id": 3, "name": "空串", "status": 1, "tenant_id": ""},
        {"id": 4, "name": "数值租户", "status": 1, "tenant_id": 2000},
    ]
    got = {a.id: a.tenant_id for a in backfill_service.build_agents(rows, include_disabled=False)}
    assert got == {"1": "1000", "2": "0", "3": "0", "4": "2000"}


def test_backfill_without_tenant_column_falls_back_to_global():
    """探测不到 app_info.tenant_id 时行整体无该键 → 全部落全局 '0'（回填链路不能因此挂掉）。"""
    rows = [{"id": 1, "name": "无租户列", "status": 1}]
    assert backfill_service.build_agents(rows, include_disabled=False)[0].tenant_id == "0"


# ---------- ⑥ 消费点：user_context.tenant_id 一路传到 Qdrant ----------

@pytest.mark.asyncio
async def test_retrieve_agents_passes_user_tenant(monkeypatch, fake_client):
    from app.services.chat import turn_context_builder as tcb

    class _Cfg:
        model = "bge-m3"
        dimension = 2

    async def _cfg():
        return _Cfg()

    async def _embed(_text, config=None):
        return [0.1, 0.2]

    monkeypatch.setattr(tcb.embedding_service, "get_active_embedding_config", _cfg)
    monkeypatch.setattr(tcb.embedding_service, "embed_query", _embed)
    fake_client.points = [_point("a-1000", "1000"), _point("a-2000", "2000"), _point("a-global", "0")]

    from app.core.auth import UserContext
    user = UserContext(user_id="u1", username="u1", role_ids=[], dept_ids=[], tenant_id="1000")
    got = await tcb._retrieve_agents("有什么能力", user)
    assert {a["id"] for a in got} == {"a-1000", "a-2000", "a-global"}

    # 租户字段缺失的旧快照调用方同样按发布 ACL 获取候选，不抛异常。
    class _NoTenant:
        user_id = "u2"
        username = "u2"
        role_ids: list = []
        dept_ids: list = []
    got0 = await tcb._retrieve_agents("有什么能力", _NoTenant())
    assert {a["id"] for a in got0} == {"a-1000", "a-2000", "a-global"}
