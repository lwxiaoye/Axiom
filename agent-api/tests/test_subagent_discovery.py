"""call_subagent 候选语义发现（2026-07-22 升级）定向测试。

覆盖任务书 §14 的发现侧条目：Registry 硬过滤（disabled/health=down/external_app/tenant）、
30+ 智能体时相关但更早发布的仍进 Top 12、Qdrant 不可用降级关键词、Qdrant 脏数据不能突破
allowed_ids、名称精确命中优先、triggerExamples 提升/negativeExamples 不提升、挂起快照优先、
恢复期下架/失权拒绝、AUTO_ROUTE 关闭时召回仍生效、检索失败不拖垮主对话。

用 sqlite 内存库承载真实 SQL（不连 MySQL）；Qdrant/embedding 一律打桩。
"""
import json

import pytest
import pytest_asyncio
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.core.auth import UserContext
from app.core.config import settings
from app.core.database import Base
from app.models import CapabilityRegistry, WorkflowApp, WorkflowDefinition, WorkflowVersion
from app.services.agents import capability_registry, subagent_service


@compiles(MEDIUMTEXT, "sqlite")
def _mediumtext_on_sqlite(_element, _compiler, **_kw):
    # 测试用 sqlite 内存库承载 MySQL 模型：MEDIUMTEXT 按 TEXT 编译
    return "TEXT"


def _user(uid="u1", roles=None, depts=None, tenant="0"):
    return UserContext(
        user_id=uid, username=uid,
        role_ids=list(roles or []), dept_ids=list(depts or []), tenant_id=tenant,
    )


@pytest_asyncio.fixture
async def sf(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        # sys_user_role / sys_user_depart 已是 ORM 模型（SysUserRole/SysUserDepart），
        # create_all 一并建表，不再手写 CREATE TABLE。
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(subagent_service, "async_session", factory)
    monkeypatch.setattr(capability_registry, "async_session", factory)
    yield factory
    await engine.dispose()


async def _seed_registry(sf, app_id, *, name=None, description="", enabled=1,
                         health="healthy", source="agent_workbench",
                         scope="campus_internal", runtime="python_workflow",
                         pv=1, owner="owner-1", tenant="0", routing=None):
    row = CapabilityRegistry(
        app_id=app_id, name=name or app_id, description=description,
        ai_app_type="chatAgent", source_system=source, execution_scope=scope,
        runtime_type=runtime, published_version=pv, enabled=enabled, health=health,
        capabilities_json="{}", owner_user_id=owner,
    )
    if hasattr(row, "tenant_id"):
        row.tenant_id = tenant
    if routing is not None and hasattr(row, "routing_json"):
        row.routing_json = json.dumps(routing, ensure_ascii=False)
    async with sf() as s:
        s.add(row)
        await s.commit()
    return row


def _no_vector(monkeypatch):
    async def _none(*_a, **_k):
        return None
    monkeypatch.setattr(capability_registry, "_vector_route_hits", _none)


@pytest.mark.asyncio
async def test_registry_hard_filters_exclude_disabled_down_external_and_bad_version(sf, monkeypatch):
    allowed = {"a-ok", "a-disabled", "a-down", "a-external", "a-nopv", "a-scope"}

    async def _ids(_u):
        return set(allowed)
    monkeypatch.setattr(subagent_service, "list_callable_subagent_ids", _ids)
    _no_vector(monkeypatch)

    await _seed_registry(sf, "a-ok", name="正常助手")
    await _seed_registry(sf, "a-disabled", name="停用助手", enabled=0)
    await _seed_registry(sf, "a-down", name="故障助手", health="down")
    await _seed_registry(sf, "a-external", name="外部应用", source="external_app")
    await _seed_registry(sf, "a-nopv", name="零版本助手", pv=0)
    await _seed_registry(sf, "a-scope", name="外域助手", scope="external")

    out = await capability_registry.discover_for_call(_user(), "随便一个任务")
    ids = {c["id"] for c in out}
    assert ids == {"a-ok"}


@pytest.mark.asyncio
async def test_candidate_discovery_ignores_tenant_while_workflow_isolation_is_disabled(sf, monkeypatch):
    """当前阶段：已通过发布 ACL 的智能体不因租户不同而从候选集中消失。"""
    allowed = {"t-global", "t-empty", "t-own", "t-other"}

    async def _ids(_u):
        return set(allowed)
    monkeypatch.setattr(subagent_service, "list_callable_subagent_ids", _ids)
    _no_vector(monkeypatch)

    await _seed_registry(sf, "t-global", name="全局助手", tenant="0")
    await _seed_registry(sf, "t-empty", name="占位助手", tenant="")
    await _seed_registry(sf, "t-own", name="本租户助手", tenant="1000")
    await _seed_registry(sf, "t-other", name="他租户助手", tenant="2000")

    out = await capability_registry.discover_for_call(_user(tenant="1000"), "随便一个任务")
    assert {c["id"] for c in out} == allowed

    out0 = await capability_registry.discover_for_call(_user(tenant="0"), "随便一个任务")
    assert {c["id"] for c in out0} == allowed


@pytest.mark.asyncio
async def test_vector_route_filter_omits_tenant_while_workflow_isolation_is_disabled(monkeypatch):
    """工作流向量召回不应重新加回已关闭的租户隔离。"""
    from app.services.knowledge import vector_service

    captured: dict = {}

    class _FakeClient:
        async def search(self, **kw):
            captured.clear()
            captured.update(kw)
            return []

    monkeypatch.setattr(vector_service, "_get_client", lambda: _FakeClient())

    await vector_service.search_routes("col", [0.1, 0.2], ["a1"], 5, tenant_id="1000")
    assert "tenant_id" not in [getattr(c, "key", "") for c in captured["query_filter"].must]


@pytest.mark.asyncio
async def test_relevant_but_older_agent_reaches_top12_among_30_plus(sf, monkeypatch):
    allowed = {f"app-{i}" for i in range(33)} | {"app-target"}

    async def _ids(_u):
        return set(allowed)
    monkeypatch.setattr(subagent_service, "list_callable_subagent_ids", _ids)
    _no_vector(monkeypatch)

    # 33 个较新的无关智能体 + 1 个相关但（语义上）更早发布的目标——旧实现按 update_time
    # 截前 20 会把它挤掉
    for i in range(33):
        await _seed_registry(sf, f"app-{i}", name=f"通用助手{i}", description="处理通用办公事务")
    await _seed_registry(
        sf, "app-target", name="差旅报销审批助手", description="提交与审批差旅报销单",
    )

    out = await capability_registry.discover_for_call(_user(), "帮我提交这个月的差旅报销")
    assert len(out) <= settings.SUBAGENT_DISCOVERY_TOP_K
    assert "app-target" in [c["id"] for c in out]
    # 相关命中应排最前
    assert out[0]["id"] == "app-target"


@pytest.mark.asyncio
async def test_lexical_fallback_works_when_qdrant_unavailable(sf, monkeypatch):
    allowed = {f"x-{i}" for i in range(20)}

    async def _ids(_u):
        return set(allowed)
    monkeypatch.setattr(subagent_service, "list_callable_subagent_ids", _ids)
    _no_vector(monkeypatch)  # 模拟 embedding/Qdrant 全部不可用

    for i in range(19):
        await _seed_registry(sf, f"x-{i}", name=f"填充助手{i}", description="占位")
    await _seed_registry(sf, "x-19", name="合同审查助手", description="审查合同条款风险")

    out = await capability_registry.discover_for_call(_user(), "这份合同帮我审查一下风险")
    assert out and out[0]["id"] == "x-19"


@pytest.mark.asyncio
async def test_poisoned_qdrant_hits_cannot_escape_allowed_ids(sf, monkeypatch):
    allowed = {f"y-{i}" for i in range(15)}

    async def _ids(_u):
        return set(allowed)
    monkeypatch.setattr(subagent_service, "list_callable_subagent_ids", _ids)

    async def _poisoned(*_a, **_k):
        # Qdrant 里存在越权/已删除的点位：不在 allowed/Registry 交集内，必须被丢弃
        return [{"id": "evil-app", "name": "越权应用", "score": 0.99},
                {"id": "y-3", "name": "合法应用", "score": 0.5}]
    monkeypatch.setattr(capability_registry, "_vector_route_hits", _poisoned)

    for i in range(15):
        await _seed_registry(sf, f"y-{i}", name=f"业务助手{i}", description="办理业务")

    out = await capability_registry.discover_for_call(_user(), "办理一下业务")
    ids = [c["id"] for c in out]
    assert "evil-app" not in ids
    assert "y-3" in ids


@pytest.mark.asyncio
async def test_exact_name_match_ranks_first(sf, monkeypatch):
    allowed = {f"n-{i}" for i in range(20)} | {"n-translate"}

    async def _ids(_u):
        return set(allowed)
    monkeypatch.setattr(subagent_service, "list_callable_subagent_ids", _ids)
    _no_vector(monkeypatch)

    for i in range(20):
        await _seed_registry(sf, f"n-{i}", name=f"日常助手{i}", description="日常事务与翻译支持")
    await _seed_registry(sf, "n-translate", name="翻译助手", description="中英互译")

    out = await capability_registry.discover_for_call(_user(), "用翻译助手把这段话翻成英文")
    assert out[0]["id"] == "n-translate"


@pytest.mark.skipif(not hasattr(CapabilityRegistry, "routing_json"),
                    reason="routing_json 列尚未加入 ORM（Phase B）")
@pytest.mark.asyncio
async def test_trigger_examples_boost_and_negative_examples_do_not(sf, monkeypatch):
    allowed = {f"t-{i}" for i in range(16)} | {"t-trigger", "t-negative"}

    async def _ids(_u):
        return set(allowed)
    monkeypatch.setattr(subagent_service, "list_callable_subagent_ids", _ids)
    _no_vector(monkeypatch)

    for i in range(16):
        await _seed_registry(sf, f"t-{i}", name=f"填充智能体{i}", description="打杂")
    await _seed_registry(
        sf, "t-trigger", name="薪酬服务", description="人事薪酬相关服务",
        routing={"routeDescription": "工资条查询与核算", "triggerExamples": ["帮我算工资条"],
                 "negativeExamples": [], "tags": ["薪酬"]},
    )
    await _seed_registry(
        sf, "t-negative", name="考勤服务", description="人事考勤相关服务",
        routing={"routeDescription": "考勤打卡统计", "triggerExamples": [],
                 "negativeExamples": ["帮我算工资条"], "tags": ["考勤"]},
    )

    out = await capability_registry.discover_for_call(_user(), "帮我算工资条")
    ids = [c["id"] for c in out]
    assert ids[0] == "t-trigger"
    # negativeExamples 命中不得提升排名：考勤服务不能排在真正的触发命中前面
    assert "t-negative" not in ids[:1]
    if "t-negative" in ids:
        assert ids.index("t-negative") > ids.index("t-trigger")


@pytest.mark.asyncio
async def test_small_candidate_set_returns_all_without_recall(sf, monkeypatch):
    allowed = {"s-1", "s-2", "s-3"}

    async def _ids(_u):
        return set(allowed)
    monkeypatch.setattr(subagent_service, "list_callable_subagent_ids", _ids)

    async def _boom(*_a, **_k):
        raise AssertionError("候选数 ≤ top_k 时不应触发向量召回")
    monkeypatch.setattr(capability_registry, "_vector_route_hits", _boom)

    for i in (1, 2, 3):
        await _seed_registry(sf, f"s-{i}", name=f"少量助手{i}")

    out = await capability_registry.discover_for_call(_user(), "任意问题")
    assert {c["id"] for c in out} == allowed


@pytest.mark.asyncio
async def test_registry_filtered_empty_returns_empty_not_acl_fallback(sf, monkeypatch):
    """复审 #1：Registry 正常查询但过滤后为空（唯一候选已停用）→ 返回空，
    不得回退 ACL 应用集把停用智能体重新放回候选。"""
    async def _ids(_u):
        return {"only-1"}
    monkeypatch.setattr(subagent_service, "list_callable_subagent_ids", _ids)
    _no_vector(monkeypatch)

    await _seed_registry(sf, "only-1", name="唯一助手", enabled=0)
    # 就算 ACL 降级路径能查到该应用，也不允许走到那一步
    async with sf() as s:
        s.add(WorkflowApp(id="only-1", ai_app_type="chatAgent", name="唯一助手",
                          description="", status="published", owner_user_id="o1"))
        await s.commit()

    out = await capability_registry.discover_for_call(_user(), "找唯一助手办事")
    assert out == []

    # health=down 同理
    async def _ids2(_u):
        return {"down-1"}
    monkeypatch.setattr(subagent_service, "list_callable_subagent_ids", _ids2)
    await _seed_registry(sf, "down-1", name="故障助手", health="down")
    assert await capability_registry.discover_for_call(_user(), "找故障助手") == []


@pytest.mark.asyncio
async def test_registry_unavailable_falls_back_to_acl_keyword(sf, monkeypatch):
    allowed = {"f-1", "f-2"}

    async def _ids(_u):
        return set(allowed)
    monkeypatch.setattr(subagent_service, "list_callable_subagent_ids", _ids)
    _no_vector(monkeypatch)

    async def _broken(*_a, **_k):
        raise RuntimeError("registry table missing")
    monkeypatch.setattr(capability_registry, "_registry_candidates", _broken)

    async with sf() as s:
        s.add(WorkflowApp(id="f-1", ai_app_type="chatAgent", name="发票助手",
                          description="开票查验", status="published", owner_user_id="o1"))
        s.add(WorkflowApp(id="f-2", ai_app_type="chatAgent", name="日程助手",
                          description="安排会议日程", status="published", owner_user_id="o1"))
        await s.commit()

    out = await capability_registry.discover_for_call(_user(), "帮我查验一张发票")
    assert {c["id"] for c in out} == allowed  # ≤ top_k 全量返回
    assert out  # 主对话拿得到候选，不因 Registry 故障拖垮


@pytest.mark.asyncio
async def test_total_discovery_failure_returns_empty_not_raise(monkeypatch):
    async def _boom(_u):
        raise RuntimeError("mysql down")
    monkeypatch.setattr(subagent_service, "list_callable_subagent_ids", _boom)

    out = await capability_registry.discover_for_call(_user(), "任何问题")
    assert out == []


@pytest.mark.asyncio
async def test_auto_route_stays_off_and_discovery_is_independent(sf, monkeypatch):
    # 新召回不依赖旧整轮路由开关：AUTO_ROUTE_ENABLED 默认 False 时依然生效，
    # 且返回的只是候选清单（不产生 effective_subagent_id 之类的整轮直达语义）
    assert settings.AUTO_ROUTE_ENABLED is False

    async def _ids(_u):
        return {"r-1"}
    monkeypatch.setattr(subagent_service, "list_callable_subagent_ids", _ids)
    _no_vector(monkeypatch)
    await _seed_registry(sf, "r-1", name="档案查询助手")

    out = await capability_registry.discover_for_call(_user(), "查一下档案")
    assert [c["id"] for c in out] == ["r-1"]
    assert all(set(c) >= {"id", "name", "description", "type", "scope", "published_version"}
               for c in out)


@pytest.mark.asyncio
async def test_candidate_metadata_is_sanitized(sf, monkeypatch):
    async def _ids(_u):
        return {"z-1"}
    monkeypatch.setattr(subagent_service, "list_callable_subagent_ids", _ids)
    _no_vector(monkeypatch)
    await _seed_registry(
        sf, "z-1", name="秘书\x01助手",
        description="正常描述\x00带控制字符\n还有换行\x1b[31m转义" + "长" * 500,
    )

    out = await capability_registry.discover_for_call(_user(), "找秘书")
    assert out[0]["name"] == "秘书助手"
    assert "\x00" not in out[0]["description"] and "\x1b" not in out[0]["description"]
    assert "\n" not in out[0]["description"]
    assert len(out[0]["description"]) <= 200


def test_model_can_still_decline_to_call_subagent():
    # call_subagent 只是注册为可选工具：构建它不会执行 runner（模型可直答）
    from app.services.agent_harness import model_driver

    calls = []

    async def _runner(sid, task, fids):
        calls.append(sid)
        return {"status": "succeeded", "text": "ok"}

    tool = model_driver.build_call_subagent_tool(
        [{"id": "c-1", "name": "助手", "description": "d"}], _runner
    )
    assert tool is not None and tool.name == "call_subagent"
    assert calls == []


def test_negative_examples_excluded_from_embedding_text():
    """复审 #5：负例不得进正向路由文本——否则 embedding 会把负例场景当相似加分。"""
    text = capability_registry._route_text(
        "考勤服务", "人事考勤", {},
        routing={"routeDescription": "考勤打卡统计", "triggerExamples": ["查考勤"],
                 "negativeExamples": ["帮我请假"], "tags": ["考勤"]},
    )
    assert "请假" not in text
    assert "查考勤" in text and "考勤打卡统计" in text


@pytest.mark.asyncio
async def test_vector_rank_flows_into_rrf_and_negatives_penalize(sf, monkeypatch):
    """真实向量召回路径（stub search 层）：向量名次进入 RRF 排序；
    负例命中的候选即便被向量排前，也会被扣分压下去。"""
    allowed = {f"v-{i}" for i in range(14)}

    async def _ids(_u):
        return set(allowed)
    monkeypatch.setattr(subagent_service, "list_callable_subagent_ids", _ids)

    for i in range(12):
        await _seed_registry(sf, f"v-{i}", name=f"占位智能体{i}", description="打杂")
    await _seed_registry(
        sf, "v-12", name="休假服务", description="员工休假管理",
        routing={"routeDescription": "休假事务", "triggerExamples": [],
                 "negativeExamples": ["帮我请假"], "tags": []},
    )
    await _seed_registry(
        sf, "v-13", name="请假审批", description="请假申请与审批",
        routing={"routeDescription": "请假申请审批", "triggerExamples": ["帮我请假"],
                 "negativeExamples": [], "tags": []},
    )

    async def _hits(query_text, candidate_ids, tenant, recall_k, **_kwargs):
        # 模拟 Qdrant：负例候选 v-12 因语义相近被排在第一、v-13 第二
        assert "帮我请假" in query_text
        return [{"id": "v-12", "score": 0.93}, {"id": "v-13", "score": 0.91}]
    monkeypatch.setattr(capability_registry, "_vector_route_hits", _hits)

    out = await capability_registry.discover_for_call(_user(), "帮我请假")
    ids = [c["id"] for c in out]
    # 触发命中 + 无负例的 v-13 必须压过负例命中的 v-12
    assert ids.index("v-13") < ids.index("v-12") if "v-12" in ids else True
    assert ids[0] == "v-13"


def test_route_metadata_validation_limits():
    from fastapi import HTTPException

    from app.routers.workflow import RouteMetadata, _normalize_route_metadata

    assert _normalize_route_metadata(None) is None
    assert _normalize_route_metadata(RouteMetadata()) is None  # 全空不落快照

    ok = _normalize_route_metadata(RouteMetadata(
        routeDescription="报销审批", triggerExamples=["报销", "报销"],  # 去重
        negativeExamples=["请假"], tags=["财务"],
    ))
    parsed = json.loads(ok)
    assert parsed["triggerExamples"] == ["报销"]
    assert parsed["negativeExamples"] == ["请假"] and parsed["tags"] == ["财务"]

    with pytest.raises(HTTPException):
        _normalize_route_metadata(RouteMetadata(routeDescription="长" * 1001))
    with pytest.raises(HTTPException):
        _normalize_route_metadata(RouteMetadata(triggerExamples=[f"t{i}" for i in range(21)]))
    with pytest.raises(HTTPException):
        _normalize_route_metadata(RouteMetadata(triggerExamples=["长" * 201]))
    with pytest.raises(HTTPException):
        _normalize_route_metadata(RouteMetadata(tags=["长" * 33]))


# ---------- HITL 挂起快照 / 恢复期权限复核 ----------

@pytest.mark.asyncio
async def test_resume_uses_snapshot_without_rediscovery(monkeypatch):
    from app.services.agent_harness import orchestrator as cs

    async def _must_not_call(**_k):
        raise AssertionError("快照存在时不得重新召回")
    monkeypatch.setattr(cs.capability_registry, "discover_for_call", _must_not_call)

    snap = [{"id": "sub-1", "name": "报销助手", "description": "d", "published_version": 2}]
    cands = await cs._resume_candidates({"subagent_candidates": snap}, _user())
    assert cands == snap


@pytest.mark.asyncio
async def test_resume_without_snapshot_rediscovers(monkeypatch):
    from app.services.agent_harness import orchestrator as cs

    seen = {}

    async def _discover(*, user, query, history=None, top_k=0, **_kwargs):
        seen["query"] = query
        return [{"id": "sub-9", "name": "n", "description": ""}]
    monkeypatch.setattr(cs.capability_registry, "discover_for_call", _discover)

    orch = {"messages": [{"role": "user", "content": "帮我办理入职"},
                         {"role": "assistant", "content": "好的"}]}
    cands = await cs._resume_candidates(orch, _user())
    assert [c["id"] for c in cands] == ["sub-9"]
    assert seen["query"] == "帮我办理入职"


@pytest.mark.asyncio
async def test_revoked_or_unpublished_subagent_fails_closed_on_execute(monkeypatch):
    # 恢复/调用前实时复核：失权或下架 → 明确 failed，不继续执行
    async def _denied(_user, _sid):
        return None
    monkeypatch.setattr(subagent_service, "_resolve_accessible", _denied)

    result = await subagent_service.run_subagent(
        user=_user(), token="", newapi_key="", default_model="m",
        subagent_id="gone-app", message="继续",
    )
    assert result["status"] == "failed"
    assert "无访问权限" in result["text"]

    resumed = await subagent_service.resume_subagent(
        user=_user(), token="", newapi_key="", default_model="m",
        subagent_id="gone-app", resume_id="rk", resume_value={"a": 1},
    )
    assert resumed["status"] == "failed"


# ---------- Registry / Qdrant 同步纪律（发布→审核→回滚→下架） ----------

def _capture_index(monkeypatch):
    calls = {"reindex": [], "deindex": []}

    async def _re(app_id, *_a, **kw):
        calls["reindex"].append({"app_id": app_id, **kw})

    async def _de(app_id):
        calls["deindex"].append(app_id)

    monkeypatch.setattr(capability_registry, "_reindex_route", _re)
    monkeypatch.setattr(capability_registry, "_deindex_route", _de)
    return calls


async def _seed_live_app(sf, app_id, *, pv=2, routing=None, tenant="0"):
    async with sf() as s:
        s.add(WorkflowApp(id=app_id, tenant_id=tenant, ai_app_type="chatAgent",
                          name="报销审批助手", description="差旅报销审批",
                          status="published", owner_user_id="o1"))
        s.add(WorkflowDefinition(id=f"d-{app_id}", app_id=app_id,
                                 published_json='{"nodes":[]}', published_version=pv))
        s.add(WorkflowVersion(
            id=f"v{pv}-{app_id}", app_id=app_id, version_no=pv, status="approved",
            visible_role_ids="[]", visible_dept_ids="[]",
            routing_json=json.dumps(routing, ensure_ascii=False) if routing else None,
        ))
        await s.commit()


@pytest.mark.asyncio
async def test_sync_lifecycle_publish_pending_rollback_unpublish(sf, monkeypatch):
    calls = _capture_index(monkeypatch)
    routing_v2 = {"routeDescription": "差旅报销单审批流转", "triggerExamples": ["报销"],
                  "negativeExamples": [], "tags": ["财务"]}
    await _seed_live_app(sf, "lc-1", pv=2, routing=routing_v2)

    # ① 审核通过（上线 v2）→ Registry 行 + Qdrant 重建，routing 冻结自 approved 版本
    await capability_registry.sync_from_app("lc-1")
    async with sf() as s:
        row = await s.get(CapabilityRegistry, "lc-1")
        assert row is not None and row.published_version == 2
        assert row.tenant_id == "0"
        assert "差旅报销单审批流转" in (row.routing_json or "")
        assert row.route_text_hash
        assert row.index_version == capability_registry.ROUTE_INDEX_VERSION
    assert calls["reindex"][-1]["published_version"] == 2

    # ② 待审核版本不影响线上 Registry：加一条 pending v3（不同 routing）再同步
    async with sf() as s:
        s.add(WorkflowVersion(id="v3-lc-1", app_id="lc-1", version_no=3,
                              status="pending_review",
                              routing_json=json.dumps({"routeDescription": "待审草稿描述"},
                                                      ensure_ascii=False)))
        await s.commit()
    await capability_registry.sync_from_app("lc-1")
    async with sf() as s:
        row = await s.get(CapabilityRegistry, "lc-1")
        assert "待审草稿描述" not in (row.routing_json or "")

    # ③ 运维状态保留：停用后再同步，enabled/health 不被重置
    async with sf() as s:
        row = await s.get(CapabilityRegistry, "lc-1")
        row.enabled = 0
        row.health = "degraded"
        await s.commit()
    await capability_registry.sync_from_app("lc-1")
    async with sf() as s:
        row = await s.get(CapabilityRegistry, "lc-1")
        assert row.enabled == 0 and row.health == "degraded"
    assert calls["reindex"][-1]["health"] == "degraded"

    # ④ 回滚到 v1（指针切回旧 approved 版本）→ routing 恢复该版本快照并重建索引
    async with sf() as s:
        s.add(WorkflowVersion(id="v1-lc-1", app_id="lc-1", version_no=1, status="approved",
                              routing_json=json.dumps({"routeDescription": "一版旧描述"},
                                                      ensure_ascii=False)))
        from sqlalchemy import select as _select
        d = (await s.execute(_select(WorkflowDefinition)
                             .where(WorkflowDefinition.app_id == "lc-1"))).scalar_one()
        d.published_version = 1
        await s.commit()
    await capability_registry.sync_from_app("lc-1")
    async with sf() as s:
        row = await s.get(CapabilityRegistry, "lc-1")
        assert row.published_version == 1
        assert "一版旧描述" in (row.routing_json or "")

    # ⑤ 下架 → Registry 行删除 + Qdrant 点位移除
    async with sf() as s:
        app = await s.get(WorkflowApp, "lc-1")
        app.status = "unpublished"
        await s.commit()
    await capability_registry.sync_from_app("lc-1")
    async with sf() as s:
        assert await s.get(CapabilityRegistry, "lc-1") is None
    assert calls["deindex"] == ["lc-1"]


@pytest.mark.asyncio
async def test_stale_index_version_triggers_startup_rebuild(sf, monkeypatch):
    """复审 #6：存量环境只跑 SQL 迁移后，registry 行 index_version 落后 →
    启动期 backfill_if_empty 自动整体重建（补齐新 payload/hash）；已最新则 no-op。"""
    _capture_index(monkeypatch)
    await _seed_live_app(sf, "st-1", pv=1,
                         routing={"routeDescription": "旧描述"})
    # 模拟迁移后的旧行：index_version=1（< ROUTE_INDEX_VERSION）、hash 缺失
    async with sf() as s:
        row = CapabilityRegistry(
            app_id="st-1", name="报销审批助手", description="", ai_app_type="chatAgent",
            source_system="agent_workbench", execution_scope="campus_internal",
            runtime_type="python_workflow", published_version=1, enabled=1,
            health="healthy", capabilities_json="{}", owner_user_id="o1",
            tenant_id="0", index_version=1,
        )
        s.add(row)
        await s.commit()

    rebuilt = await capability_registry.backfill_if_empty()
    assert rebuilt == 1
    async with sf() as s:
        row = await s.get(CapabilityRegistry, "st-1")
        assert row.index_version == capability_registry.ROUTE_INDEX_VERSION
        assert row.route_text_hash

    # 已是最新契约版本：再次启动不重建
    async def _boom():
        raise AssertionError("index_version 已最新时不应触发 backfill_all")
    monkeypatch.setattr(capability_registry, "backfill_all", _boom)
    assert await capability_registry.backfill_if_empty() == 0


@pytest.mark.asyncio
async def test_tenant_drift_triggers_startup_reconcile(sf, monkeypatch):
    """2026-07-27 真机故障回归：应用租户已迁 0，registry 行 tenant_id 仍是旧租户 1000，
    且 index_version 已最新 → 旧变更检测（route_text_hash/index_version 均不含租户）
    不触发重建，租户 0 用户被 _registry_candidates 租户闸整体滤空、call_subagent 不注册。
    启动自检必须联表比对出漂移行并重同步归一（含 Qdrant 点位 payload）。"""
    calls = _capture_index(monkeypatch)
    await _seed_live_app(sf, "td-1", pv=1, routing={"routeDescription": "报销审批"},
                         tenant="0")
    async with sf() as s:
        # 漂移行：index_version 已最新（旧检测不触发）、tenant 却是陈旧的 1000
        s.add(CapabilityRegistry(
            app_id="td-1", name="报销审批助手", description="", ai_app_type="chatAgent",
            source_system="agent_workbench", execution_scope="campus_internal",
            runtime_type="python_workflow", published_version=1, enabled=1,
            health="healthy", capabilities_json="{}", owner_user_id="o1",
            tenant_id="1000", index_version=capability_registry.ROUTE_INDEX_VERSION,
            route_text_hash="stale-but-present",
        ))
        # 归一等价不算漂移：应用租户 ''（全局占位）× registry '0' 同义，不得触发重同步
        s.add(WorkflowApp(id="td-eq", tenant_id="", ai_app_type="chatAgent",
                          name="占位应用", description="", status="published",
                          owner_user_id="o1"))
        s.add(WorkflowDefinition(id="d-td-eq", app_id="td-eq",
                                 published_json='{"nodes":[]}', published_version=1))
        s.add(CapabilityRegistry(
            app_id="td-eq", name="占位应用", description="", ai_app_type="chatAgent",
            source_system="agent_workbench", execution_scope="campus_internal",
            runtime_type="python_workflow", published_version=1, enabled=1,
            health="healthy", capabilities_json="{}", owner_user_id="o1",
            tenant_id="0", index_version=capability_registry.ROUTE_INDEX_VERSION,
            route_text_hash="x",
        ))
        await s.commit()

    fixed = await capability_registry.backfill_if_empty()
    assert fixed == 1
    async with sf() as s:
        row = await s.get(CapabilityRegistry, "td-1")
        assert row.tenant_id == "0"
    # Qdrant 点位 payload 的租户同步修正（向量召回同样按租户过滤），且只重同步漂移行
    assert len(calls["reindex"]) == 1
    assert calls["reindex"][-1]["app_id"] == "td-1"
    assert calls["reindex"][-1]["tenant_id"] == "0"

    # 已归一后二次启动：既不整体重建也不重同步
    async def _boom(*_a, **_k):
        raise AssertionError("无租户漂移时不应触发重建/重同步")
    monkeypatch.setattr(capability_registry, "backfill_all", _boom)
    monkeypatch.setattr(capability_registry, "sync_from_app", _boom)
    assert await capability_registry.backfill_if_empty() == 0


@pytest.mark.asyncio
async def test_publish_without_route_metadata_inherits_live_snapshot(sf, monkeypatch):
    """复审 #4：API 未传 routeMetadata 时继承线上版本快照，而不是清空。"""
    from types import SimpleNamespace

    from app.routers import workflow as wf

    live_routing = json.dumps({"routeDescription": "线上路由描述",
                               "triggerExamples": ["旧触发"]}, ensure_ascii=False)
    await _seed_live_app(sf, "inh-1", pv=2,
                         routing={"routeDescription": "线上路由描述",
                                  "triggerExamples": ["旧触发"]})
    monkeypatch.setattr(wf, "async_session", sf)
    monkeypatch.setattr(wf.settings, "PUBLISH_APPROVAL_REQUIRED", True)

    async def _perm(_s, app_id, _u, **_k):
        # _submit_or_publish 会先用 app.status 做「线上版本未变更则 409」的判断，桩要带上
        return SimpleNamespace(id=app_id, status="published"), "OWNER"
    monkeypatch.setattr(wf, "_require_permission", _perm)

    captured = {}

    async def _submit(_s, app, workflow_json, note, user, roles, depts, routing_json=None, **_k):
        # 现役签名还带 publish_channels / embed_origins；本用例只关心 routing_json
        captured["routing_json"] = routing_json
        return SimpleNamespace(id="v-new")
    monkeypatch.setattr(wf, "_do_submit_review", _submit)
    monkeypatch.setattr(wf, "_version_dict", lambda v, **_k: {"id": v.id})

    payload = wf.DefinitionSaveRequest(appId="inh-1", workflowJson="{}")  # 未传 routeMetadata
    await wf._submit_or_publish(payload, _user("owner-1"))
    assert json.loads(captured["routing_json"]) == json.loads(live_routing)

    # 复审二轮 #1：显式传空 routeMetadata = 用户明确清空 → 不继承、按空保存
    payload_clear = wf.DefinitionSaveRequest(
        appId="inh-1", workflowJson="{}", routeMetadata=wf.RouteMetadata(),
    )
    await wf._submit_or_publish(payload_clear, _user("owner-1"))
    assert captured["routing_json"] is None

    # 显式传新内容 → 按本次内容保存，不被线上版本覆盖
    payload_new = wf.DefinitionSaveRequest(
        appId="inh-1", workflowJson="{}",
        routeMetadata=wf.RouteMetadata(routeDescription="新描述"),
    )
    await wf._submit_or_publish(payload_new, _user("owner-1"))
    assert json.loads(captured["routing_json"])["routeDescription"] == "新描述"


@pytest.mark.asyncio
async def test_globally_empty_registry_falls_back_to_acl_with_reason(sf, monkeypatch):
    """复审二轮 #2：Registry 全局无任何行（未初始化）→ 临时回退 ACL 候选；
    与「有行但过滤为空必须返回空」（test_registry_filtered_empty…）成对。"""
    async def _ids(_u):
        return {"g-1"}
    monkeypatch.setattr(subagent_service, "list_callable_subagent_ids", _ids)
    _no_vector(monkeypatch)

    async with sf() as s:  # 只有应用，registry 一行都没有
        s.add(WorkflowApp(id="g-1", ai_app_type="chatAgent", name="新环境助手",
                          description="", status="published", owner_user_id="o1"))
        await s.commit()

    out = await capability_registry.discover_for_call(_user(), "找新环境助手")
    assert [c["id"] for c in out] == ["g-1"]


@pytest.mark.asyncio
async def test_suspend_snapshot_shape_is_minimal(monkeypatch):
    from app.services.agent_harness import orchestrator as cs

    snap = cs._candidate_snapshot([
        {"id": "a", "name": "n" * 300, "description": "d" * 500,
         "route_description": "r" * 500, "published_version": 3, "tags": ["x"]},
        {"name": "无 id 丢弃"},
    ])
    assert len(snap) == 1
    row = snap[0]
    assert set(row) == {"id", "name", "description", "route_description", "published_version", "icon"}
    assert len(row["name"]) <= 64 and len(row["description"]) <= 200
    assert row["published_version"] == 3
    assert row["icon"] == ""
