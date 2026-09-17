"""WS2/WS3/WS4 冒烟：发布审批状态机 + 版本历史/回滚 + 后台跨用户管理。

本地开发鉴权走 X-User-Id/X-Username 头（GATEWAY_IDENTITY_SIGNATURE_REQUIRED=False）。
运行：python scripts/ws_publish_review_smoke.py
"""
import asyncio
import json

import httpx

BASE = "http://localhost:8000/agent-api"
AUTHOR = {"X-User-Id": "ws_author", "X-Username": "ws_author", "Content-Type": "application/json"}
ADMIN = {"X-User-Id": "admin", "X-Username": "admin", "Content-Type": "application/json"}

START_ID = "workflowStartNodeId"


def _wf(answer_text: str) -> str:
    nodes = [
        {"nodeId": START_ID, "name": "流程开始", "flowNodeType": "workflowStart", "inputs": [], "outputs": []},
        {"nodeId": "a1", "name": "回复", "flowNodeType": "answerNode",
         "inputs": [{"key": "text", "value": answer_text}], "outputs": []},
    ]
    edges = [{"source": START_ID, "sourceHandle": f"{START_ID}-source-right",
              "target": "a1", "targetHandle": "a1-target-left"}]
    # ensure_ascii=False：与前端 JSON.stringify 一致，画布 JSON 存字面 UTF-8 而非 \uXXXX 转义
    return json.dumps({"version": "0.1", "nodes": [], "edges": [], "fastgpt": {"nodes": nodes, "edges": edges}}, ensure_ascii=False)


PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results = []


def check(name: str, ok: bool, detail: str = ""):
    results.append(ok)
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail and not ok else ""))


async def main():
    async with httpx.AsyncClient(timeout=30, base_url=BASE) as c:
        # 0) 建应用（author）
        r = await c.post("/workflow/app/add", headers=AUTHOR,
                         json={"aiAppType": "workflow", "name": "WS冒烟应用"})
        app_id = r.json()["id"]
        print(f"app_id={app_id}")

        # 1) 能力信息：审核权限暂不启用，但后台管理仍需 admin
        cap_author = (await c.get("/workflow/me/capabilities", headers=AUTHOR)).json()
        cap_admin = (await c.get("/workflow/me/capabilities", headers=ADMIN)).json()
        check("author 非平台管理员", cap_author["isPlatformAdmin"] is False, str(cap_author))
        check("admin 是审核员+平台管理员", cap_admin["isReviewer"] and cap_admin["isPlatformAdmin"], str(cap_admin))
        check("强制审批开启", cap_author["approvalRequired"] is True)

        # 2) 提交发布 → pending，不即时上线
        r = await c.post("/workflow/definition/submitReview", headers=AUTHOR,
                         json={
                             "appId": app_id,
                             "workflowJson": _wf("v1回复"),
                             "changeNote": "首版",
                             "visibleRoleIds": ["role-a"],
                             "visibleDeptIds": ["dept-a"],
                         })
        check("提交发布返回 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
        sub = r.json()
        v1 = sub.get("version", {})
        check("版本进入 pending_review", v1.get("status") == "pending_review", str(v1))
        check("版本保存可见角色/部门", v1.get("visibleRoleIds") == ["role-a"] and v1.get("visibleDeptIds") == ["dept-a"], str(v1))
        check("提交后仍未上线（definition 无 publishedVersion）",
              (await c.get("/workflow/definition/queryByAppInfoId", headers=AUTHOR,
                           params={"appId": app_id})).json().get("publishedVersion") == 0)

        # 3) 审核台暂不做权限拦截：author/admin 都能看到 pending
        r_author = await c.get("/workflow/review/page", headers=AUTHOR)
        check("author 可访问审核台", r_author.status_code == 200, str(r_author.status_code))
        review = (await c.get("/workflow/review/page", headers=ADMIN, params={"status": "pending_review"})).json()
        mine = [x for x in review["records"] if x["appId"] == app_id]
        check("admin 审核台可见该待审版本", len(mine) == 1, str(review["total"]))

        # 4) 通过 → 上线，definition.publishedVersion=1
        r = await c.post("/workflow/review/approve", headers=ADMIN,
                         json={"versionId": v1["id"], "comment": "OK"})
        check("审核通过 200", r.status_code == 200, r.text[:200])
        dfn = (await c.get("/workflow/definition/queryByAppInfoId", headers=AUTHOR, params={"appId": app_id})).json()
        check("通过后线上版本=1", dfn.get("publishedVersion") == 1, str(dfn.get("publishedVersion")))
        check("published_json = v1", "v1回复" in (dfn.get("publishedJson") or ""))

        # 5) 再发一版 v2 并通过 → 线上=2
        r = await c.post("/workflow/definition/submitReview", headers=AUTHOR,
                         json={"appId": app_id, "workflowJson": _wf("v2回复"), "changeNote": "二版"})
        v2 = r.json()["version"]
        await c.post("/workflow/review/approve", headers=ADMIN, json={"versionId": v2["id"]})
        dfn = (await c.get("/workflow/definition/queryByAppInfoId", headers=AUTHOR, params={"appId": app_id})).json()
        check("v2 通过后线上=2 且内容切 v2",
              dfn.get("publishedVersion") == 2 and "v2回复" in (dfn.get("publishedJson") or ""))

        # 6) 版本历史：至少 2 条 approved，liveVersion=2
        vp = (await c.get("/workflow/version/page", headers=AUTHOR, params={"appId": app_id})).json()
        approved = [x for x in vp["records"] if x["status"] == "approved"]
        check("版本历史含 2 条 approved", len(approved) >= 2 and vp["liveVersion"] == 2, str(vp["liveVersion"]))

        # 7) 回滚到 v1（admin）→ 新线上版本 v3，内容回到 v1
        r = await c.post("/workflow/admin/app/rollback", headers=ADMIN,
                         json={"appId": app_id, "versionNo": 1})
        check("回滚 200", r.status_code == 200, r.text[:200])
        dfn = (await c.get("/workflow/definition/queryByAppInfoId", headers=AUTHOR, params={"appId": app_id})).json()
        check("回滚后内容=v1 且线上版本号递增为 3",
              "v1回复" in (dfn.get("publishedJson") or "") and dfn.get("publishedVersion") == 3,
              str(dfn.get("publishedVersion")))

        # 8) 驳回流程：提交 v4，admin 驳回（必须带意见）
        r = await c.post("/workflow/definition/submitReview", headers=AUTHOR,
                         json={"appId": app_id, "workflowJson": _wf("v4回复")})
        v4 = r.json()["version"]
        r_nocomment = await c.post("/workflow/review/reject", headers=ADMIN, json={"versionId": v4["id"]})
        check("驳回无意见被拒 400", r_nocomment.status_code == 400, str(r_nocomment.status_code))
        r = await c.post("/workflow/review/reject", headers=ADMIN,
                         json={"versionId": v4["id"], "comment": "缺少说明"})
        check("带意见驳回 200 且状态 rejected", r.status_code == 200 and r.json()["version"]["status"] == "rejected")
        dfn = (await c.get("/workflow/definition/queryByAppInfoId", headers=AUTHOR, params={"appId": app_id})).json()
        check("驳回不影响线上（仍为回滚后的 v1 内容）", "v1回复" in (dfn.get("publishedJson") or ""))

        # 9) 后台跨用户管理门禁 + 可见性
        r_author = await c.get("/workflow/admin/app/page", headers=AUTHOR)
        check("author 访问后台管理被拒 403", r_author.status_code == 403, str(r_author.status_code))
        admin_page = (await c.get("/workflow/admin/app/page", headers=ADMIN, params={"keyword": "WS冒烟"})).json()
        check("admin 跨用户可见该应用", any(x["id"] == app_id for x in admin_page["records"]), str(admin_page["total"]))

        # 清理
        await c.delete("/workflow/admin/app/delete", headers=ADMIN, params={"id": app_id})

    ok = sum(results)
    print(f"\n{ok}/{len(results)} 通过")
    return ok == len(results)


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
