"""Skill 广场隔离与管理员分发（2026-09-19）。

产品决定（用户原话）：「skill 可以让用户自己上传，然后每个用户自己的 skill 广场是隔离开的，
除了管理员可以给每个用户统一分发 skill」，并追加：**管理员看不到任何用户的个人 skill，这是隐私**。

这里验证的是真实路径（真实 MySQL + 真实路由，只替换 token→用户解析），不是替身：
- 学生 A 看不到 / 读不到 / 改不了 / 删不了学生 B 的个人技能，一律 404（不暴露存在性）；
- 管理员对学生的个人技能同样 404（无旁路），分发/撤回也 404；
- 内部加载（load_skill_packages / load_skill_contents / _fetch_trusted_skills）同一套 ACL；
- 管理员分发自己的技能后全员可见、可 @；撤回后消失；内置技能不可撤回/删除/编辑；
- 列表契约字段齐全；技能名清洗、限长、同一用户下重名报可读错误。

运行：需要 DATABASE_URL 指向可用的 MySQL（在 axiom-agent-api 一次性容器里跑）。
每个用例结束都把自己造的数据删干净并 dispose 引擎（pytest-asyncio 每个用例一个事件循环，
aiomysql 连接不能跨循环复用）。
"""
from __future__ import annotations

import io
import json
import uuid
import zipfile
from contextlib import asynccontextmanager
from typing import Optional
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.auth import UserContext, current_user
from app.core.database import async_session, engine
from app.models import AgentSkill, AgentSkillVersion
from app.routers import agent_skill
from app.services.skills import skill_catalog

pytestmark = pytest.mark.asyncio

# 每次运行独立的用户 id，避免与库里真实用户/并行测试串数据；username=admin 才会被 is_admin 认作管理员
_RUN = uuid.uuid4().hex[:8]
ADMIN = UserContext(user_id=f"t-admin-{_RUN}", username="admin")
STUDENT_A = UserContext(user_id=f"t-stu-a-{_RUN}", username=f"stu-a-{_RUN}")
STUDENT_B = UserContext(user_id=f"t-stu-b-{_RUN}", username=f"stu-b-{_RUN}")
TEST_USER_IDS = [ADMIN.user_id, STUDENT_A.user_id, STUDENT_B.user_id]
# token → 用户：_fetch_trusted_skills / skill_catalog 那条路按 token 解析用户，这里替换成固定映射
TOKENS = {f"tok-{u.user_id}": u.user_id for u in (ADMIN, STUDENT_A, STUDENT_B)}

CONTRACT_FIELDS = {
    # 既有
    "id", "skillId", "recordId", "name", "description", "source", "enabled", "version", "versionName",
    "category", "currentVersionId", "creationStatus", "createTime", "updateTime",
    # 2026-09-19 追加
    "ownerUserId", "distributedByUserId", "builtin", "canEdit", "canDelete", "canDistribute", "canRevoke",
    "fileCount", "updatedAt",
}


# ---------------------------------------------------------------- 夹具
class _Viewer:
    """可切换的「当前用户」：同一个 client 依次扮演 admin / A / B。"""

    def __init__(self) -> None:
        self.user: UserContext = ADMIN


async def _fake_user_id_from_token(token: str) -> Optional[str]:
    return TOKENS.get(str(token or ""))


async def _purge_test_data() -> list[str]:
    """删掉测试用户名下的一切技能与版本（无论用例走到哪一步都能清干净），返回删掉的技能 id。"""
    async with async_session() as session:
        ids = (
            await session.execute(select(AgentSkill.id).where(AgentSkill.owner_user_id.in_(TEST_USER_IDS)))
        ).scalars().all()
        if ids:
            await session.execute(delete(AgentSkillVersion).where(AgentSkillVersion.skill_id.in_(ids)))
            await session.execute(delete(AgentSkill).where(AgentSkill.id.in_(ids)))
        await session.commit()
    return list(ids)


@asynccontextmanager
async def _client():
    app = FastAPI()
    app.include_router(agent_skill.router)
    viewer = _Viewer()
    app.dependency_overrides[current_user] = lambda: viewer.user
    # /skill/add 会起后台 LLM 生成任务：测试里不调模型，说明书由 _make_ready 直接落库
    with patch.object(agent_skill, "_spawn_background", lambda coro: coro.close()), patch.object(
        skill_catalog, "_user_id_from_token", _fake_user_id_from_token
    ):
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                yield client, viewer
        finally:
            purged = await _purge_test_data()
            if purged:
                print(f"\n[cleanup] deleted test skills: {purged}")
            await engine.dispose()


async def _make_ready(skill_id: str, content: str = "# 测试技能\n\n## 执行步骤\n1. 做事") -> str:
    """把 /skill/add 创建的 creating 技能补成 ready 的内容型技能（package_b64 为空）。"""
    async with async_session() as session:
        skill = (await session.execute(select(AgentSkill).where(AgentSkill.id == skill_id))).scalar_one()
        version = AgentSkillVersion(id=uuid.uuid4().hex, skill_id=skill_id, version_name="v1", content=content)
        session.add(version)
        skill.creation_status = "ready"
        skill.current_version_id = version.id
        await session.commit()
        return version.id


async def _add(client, viewer, user: UserContext, name: str, *, ready: bool = True) -> dict:
    viewer.user = user
    resp = await client.post("/skill/add", json={"name": name, "description": f"{name} 描述", "category": ["other"]})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    if ready:
        await _make_ready(data["id"])
    return data


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


async def _import(client, viewer, user: UserContext, name: str, *, extra: Optional[dict] = None):
    viewer.user = user
    raw = _zip({
        "skill.json": json.dumps({"name": name, "description": "导入的", "category": ["tool"]}).encode(),
        "SKILL.md": f"# {name}\n步骤".encode(),
        **(extra or {}),
    })
    return await client.post("/skill/import", files={"file": ("skill.zip", raw, "application/zip")})


async def _list_ids(client, viewer, user: UserContext, scope: Optional[str] = None) -> dict[str, dict]:
    viewer.user = user
    resp = await client.get("/skill/list", params={"scope": scope} if scope else None)
    assert resp.status_code == 200, resp.text
    return {item["id"]: item for item in resp.json()}


async def _trusted(skill_ids: list[str], user: UserContext) -> list[str]:
    from app.services.chat.turn_context_builder import _fetch_trusted_skills

    return [s["id"] for s in await _fetch_trusted_skills(skill_ids, f"tok-{user.user_id}")]


# ---------------------------------------------------------------- 用例
async def test_admin_distribute_then_revoke_full_flow():
    """任务书验证第 3 条：admin 上传 → A 看不到 → 分发 → A 看到但不可改 → 撤回 → A 看不到 → admin 删。"""
    async with _client() as (client, viewer):
        s1 = await _add(client, viewer, ADMIN, f"管理员技能-{_RUN}")
        sid = s1["id"]
        print(f"\n[ids] admin content skill = {sid}")

        # 分发前：学生 A 的广场没有它；A 直接读也 404
        assert sid not in await _list_ids(client, viewer, STUDENT_A)
        assert (await client.get("/skill/content", params={"skillId": sid})).status_code == 404
        assert await _trusted([sid], STUDENT_A) == []
        # admin 自己看：canDistribute=True（personal、自己的、admin）
        mine = (await _list_ids(client, viewer, ADMIN))[sid]
        assert mine["canDistribute"] is True and mine["canRevoke"] is False and mine["canEdit"] is True
        assert mine["fileCount"] == 0  # 内容型

        # 分发
        viewer.user = ADMIN
        resp = await client.post(f"/skill/{sid}/distribute")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source"] == "system"
        assert body["distributedByUserId"] == ADMIN.user_id
        assert body["ownerUserId"] == ADMIN.user_id  # 谁做的仍是谁的
        assert body["builtin"] is False
        # 重复分发幂等
        assert (await client.post(f"/skill/{sid}/distribute")).status_code == 200

        # 学生 A：看得到、读得到、可 @（_fetch_trusted_skills）、但不能改/删/分发/撤回
        seen = await _list_ids(client, viewer, STUDENT_A)
        assert sid in seen
        item = seen[sid]
        assert item["source"] == "system"
        assert item["canEdit"] is False and item["canDelete"] is False
        assert item["canDistribute"] is False and item["canRevoke"] is False
        assert (await client.get("/skill/content", params={"skillId": sid})).status_code == 200
        assert (await client.get("/skill/versions", params={"skillId": sid})).status_code == 200
        assert (await client.put("/skill/edit", json={"id": sid, "name": "改名"})).status_code == 403
        assert (await client.delete("/skill/delete", params={"id": sid})).status_code == 403
        assert (await client.post(f"/skill/{sid}/revoke-distribution")).status_code == 403
        assert await _trusted([sid], STUDENT_A) == [sid]
        # 内部加载：A 传 owner_user_id=A 也能取到（system 全员可用）
        assert [p["skillId"] for p in await agent_skill.load_skill_packages([sid], owner_user_id=STUDENT_A.user_id)] == [sid]
        # system 范围列表也含它
        assert sid in await _list_ids(client, viewer, STUDENT_A, scope="system")

        # admin 视角：分发出去的仍可编辑/删除，可撤回，不可再分发
        mine = (await _list_ids(client, viewer, ADMIN))[sid]
        assert mine["canEdit"] is True and mine["canDelete"] is True
        assert mine["canRevoke"] is True and mine["canDistribute"] is False
        viewer.user = ADMIN
        resp = await client.put("/skill/edit", json={"id": sid, "description": "分发后改描述"})
        assert resp.status_code == 200 and resp.json()["description"] == "分发后改描述"

        # 撤回
        resp = await client.post(f"/skill/{sid}/revoke-distribution")
        assert resp.status_code == 200, resp.text
        assert resp.json()["source"] == "personal"
        assert resp.json()["distributedByUserId"] is None
        assert resp.json()["ownerUserId"] == ADMIN.user_id
        # 再撤一次：未分发 → 400
        assert (await client.post(f"/skill/{sid}/revoke-distribution")).status_code == 400

        # 学生 A 又看不到了
        assert sid not in await _list_ids(client, viewer, STUDENT_A)
        assert (await client.get("/skill/content", params={"skillId": sid})).status_code == 404
        assert await _trusted([sid], STUDENT_A) == []

        # admin 删自己的
        viewer.user = ADMIN
        assert (await client.delete("/skill/delete", params={"id": sid})).json() == {"success": True}
        assert sid not in await _list_ids(client, viewer, ADMIN)


async def test_personal_skills_isolated_between_students():
    """A 的个人技能对 B：列表不出现、content/versions/edit/delete 全 404，内部加载为空。"""
    async with _client() as (client, viewer):
        resp = await _import(client, viewer, STUDENT_A, f"A的导入技能-{_RUN}", extra={"scripts/run.py": b"print(1)"})
        assert resp.status_code == 200, resp.text
        sa = resp.json()
        sid = sa["id"]
        print(f"\n[ids] student A imported skill = {sid}")
        assert sa["fileCount"] == 3 and sa["ownerUserId"] == STUDENT_A.user_id

        # B 的视角：一律 404
        assert sid not in await _list_ids(client, viewer, STUDENT_B)
        assert sid not in await _list_ids(client, viewer, STUDENT_B, scope="personal")
        assert (await client.get("/skill/content", params={"skillId": sid})).status_code == 404
        assert (await client.get("/skill/versions", params={"skillId": sid})).status_code == 404
        assert (await client.put("/skill/edit", json={"id": sid, "name": "B 改 A 的"})).status_code == 404
        assert (await client.delete("/skill/delete", params={"id": sid})).status_code == 404
        # 内部加载（agent 执行器 / @Skill 注入）同一套 ACL
        assert await agent_skill.load_skill_packages([sid], owner_user_id=STUDENT_B.user_id) == []
        assert await agent_skill.load_skill_contents([sid], owner_user_id=STUDENT_B.user_id) == []
        assert await _trusted([sid], STUDENT_B) == []

        # A 自己：正常
        mine = (await _list_ids(client, viewer, STUDENT_A))[sid]
        assert mine["canEdit"] is True and mine["canDelete"] is True
        assert mine["canDistribute"] is False and mine["canRevoke"] is False  # 不是管理员
        assert (await client.get("/skill/content", params={"skillId": sid})).status_code == 200
        assert [p["skillId"] for p in await agent_skill.load_skill_packages([sid], owner_user_id=STUDENT_A.user_id)] == [sid]
        assert await _trusted([sid], STUDENT_A) == [sid]
        # 普通用户不能分发/撤回（403，不查库）
        assert (await client.post(f"/skill/{sid}/distribute")).status_code == 403
        assert (await client.post(f"/skill/{sid}/revoke-distribution")).status_code == 403

        # 技能仍在（B 的删除没生效）
        assert sid in await _list_ids(client, viewer, STUDENT_A)


async def test_admin_has_no_bypass_into_student_personal_skills():
    """管理员作为读取方去碰学生的个人技能：列表不出现、读/改/删/分发/撤回全 404（隐私，无旁路）。"""
    async with _client() as (client, viewer):
        sa = await _add(client, viewer, STUDENT_A, f"学生私有技能-{_RUN}")
        sid = sa["id"]
        print(f"\n[ids] student A private skill = {sid}")

        viewer.user = ADMIN
        for scope in (None, "personal", "system", "all"):
            assert sid not in await _list_ids(client, viewer, ADMIN, scope=scope), scope
        viewer.user = ADMIN
        assert (await client.get("/skill/content", params={"skillId": sid})).status_code == 404
        assert (await client.get("/skill/versions", params={"skillId": sid})).status_code == 404
        assert (await client.put("/skill/edit", json={"id": sid, "name": "admin 改"})).status_code == 404
        assert (await client.delete("/skill/delete", params={"id": sid})).status_code == 404
        assert (await client.post(f"/skill/{sid}/distribute")).status_code == 404
        assert (await client.post(f"/skill/{sid}/revoke-distribution")).status_code == 404
        assert await agent_skill.load_skill_packages([sid], owner_user_id=ADMIN.user_id) == []
        assert await agent_skill.load_skill_contents([sid], owner_user_id=ADMIN.user_id) == []
        assert await _trusted([sid], ADMIN) == []

        # 学生的技能原封不动
        mine = (await _list_ids(client, viewer, STUDENT_A))[sid]
        assert mine["source"] == "personal" and mine["name"] == sa["name"]


async def test_builtin_skills_are_locked_even_for_admin():
    """内置技能（builtin=1）：admin 也不能撤回/删除/编辑；列表里 canXxx 全 False。"""
    async with _client() as (client, viewer):
        system_items = await _list_ids(client, viewer, ADMIN, scope="system")
        builtin_items = [i for i in system_items.values() if i["builtin"] is True]
        assert builtin_items, "启动播种的内置技能（sys-skill-* / ppt-studio）应至少有一条"
        for item in builtin_items:
            assert item["ownerUserId"] == "system"
            assert item["distributedByUserId"] is None
            assert item["canEdit"] is False and item["canDelete"] is False
            assert item["canDistribute"] is False and item["canRevoke"] is False
        target = builtin_items[0]["id"]
        viewer.user = ADMIN
        assert (await client.post(f"/skill/{target}/revoke-distribution")).status_code == 403
        assert (await client.delete("/skill/delete", params={"id": target})).status_code == 403
        assert (await client.put("/skill/edit", json={"id": target, "name": "改内置"})).status_code == 403
        assert (await client.post(f"/skill/{target}/distribute")).status_code == 400
        # 学生同样不行
        viewer.user = STUDENT_A
        assert (await client.delete("/skill/delete", params={"id": target})).status_code == 403
        assert (await client.put("/skill/edit", json={"id": target, "name": "改内置"})).status_code == 403
        # ppt-studio 是随代码发布的包：fileCount 数的是磁盘文件
        if "ppt-studio" in system_items:
            assert system_items["ppt-studio"]["builtin"] is True
            assert system_items["ppt-studio"]["fileCount"] > 0


async def test_list_contract_fields_present_for_every_item():
    async with _client() as (client, viewer):
        s1 = await _add(client, viewer, STUDENT_A, f"契约技能-{_RUN}")
        items = await _list_ids(client, viewer, STUDENT_A)
        assert s1["id"] in items
        for item in items.values():
            missing = CONTRACT_FIELDS - set(item)
            assert not missing, f"{item['id']} 缺字段 {missing}"
            assert isinstance(item["builtin"], bool)
            assert isinstance(item["fileCount"], int)
            for key in ("canEdit", "canDelete", "canDistribute", "canRevoke"):
                assert isinstance(item[key], bool), key
        mine = items[s1["id"]]
        assert mine["ownerUserId"] == STUDENT_A.user_id
        assert mine["distributedByUserId"] is None
        assert mine["updatedAt"] == mine["updateTime"]


async def test_skill_name_is_cleaned_length_limited_and_unique_per_user():
    async with _client() as (client, viewer):
        viewer.user = STUDENT_A
        base = f"名称校验-{_RUN}"
        # 控制字符被去掉、首尾空白去掉
        resp = await client.post("/skill/add", json={"name": f"  {base}\x00\x07\n  "})
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == base
        # 同一用户重名 → 409，可读文案
        resp = await client.post("/skill/add", json={"name": base})
        assert resp.status_code == 409
        assert base in resp.json()["detail"]
        # 导入包里 skill.json 的 name 重名 → 409
        resp = await _import(client, viewer, STUDENT_A, base)
        assert resp.status_code == 409
        # 超长 → 400
        resp = await client.post("/skill/add", json={"name": "长" * 129})
        assert resp.status_code == 400 and "128" in resp.json()["detail"]
        # 128 恰好可以
        ok_name = ("长" * 120) + _RUN
        assert len(ok_name) == 128
        resp = await client.post("/skill/add", json={"name": ok_name})
        assert resp.status_code == 200, resp.text
        # 改名撞上自己另一个技能 → 409；改成同名（不变）放行
        second = await _add(client, viewer, STUDENT_A, f"{base}-2")
        viewer.user = STUDENT_A
        assert (await client.put("/skill/edit", json={"id": second["id"], "name": base})).status_code == 409
        assert (await client.put("/skill/edit", json={"id": second["id"], "name": f"{base}-2"})).status_code == 200
        # 不同用户可以各自叫同一个名字（广场隔离）
        resp = await _import(client, viewer, STUDENT_B, base)
        assert resp.status_code == 200, resp.text
        # 空名 → 400
        viewer.user = STUDENT_A
        assert (await client.post("/skill/add", json={"name": "\x00\n "})).status_code == 400
