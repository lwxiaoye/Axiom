"""文件版本管理冒烟（实施说明 Phase B §3 + §4.3.4 草稿版）。

运行（容器内）：docker exec agent-api python scripts/file_versions_smoke.py
覆盖规格验收：连续编辑得 v1/v2/v3；任意旧版本可下载；恢复 v1 后当前正确且 v2/v3 仍在；
存量文件覆写自动补录基线；手工草稿不转正；run_code 不合格中间版静默拦截；删除级联清版本；REST 三端点。
"""
import asyncio
import sys
import uuid

sys.path.insert(0, "/app")

import httpx

BASE = "http://localhost:8000/agent-api"
PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    ok = bool(ok)
    results.append(ok)
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail and not ok else ""))


def hdr(uid):
    return {"X-User-Id": uid, "X-Username": uid}


async def main():
    from sqlalchemy import delete as sa_delete, select

    from app.core.database import Base, async_session, engine
    from app.models import AgentUserFile, AgentUserFileVersion
    from app.services import main_agent
    from app.services.files import user_file_service as ufs

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    uid = f"smoke_ver_{uuid.uuid4().hex[:8]}"
    thread = f"smoke_ver_th_{uuid.uuid4().hex[:6]}"

    try:
        # 1 上传 = v1；连续编辑 = v2/v3（验收场景①）
        f1 = await ufs.save_file(uid, "报告.md", "版本一".encode("utf-8"))
        check("上传即 v1", f1.get("versionNo") == 1, str(f1.get("versionNo")))
        s2 = await ufs.update_file_content(uid, f1["id"], "版本二", change_summary="全文覆写")
        check("覆写得 v2", s2.get("versionNo") == 2, str(s2.get("versionNo")))
        s3 = await ufs.update_file_content(uid, f1["id"], "版本三", change_summary="精确替换 1 处")
        check("再写得 v3", s3.get("versionNo") == 3)

        data = await ufs.list_versions(uid, f1["id"])
        vers = data["versions"]
        check("版本历史 3 条（新→旧）", len(vers) == 3 and [v["versionNo"] for v in vers] == [3, 2, 1])
        check("修改说明保留", vers[1]["changeSummary"] == "全文覆写")

        # 2 任意旧版本可下载（验收场景②）
        v1 = next(v for v in vers if v["versionNo"] == 1)
        _, blob = await ufs.read_version_bytes(uid, f1["id"], v1["id"])
        check("v1 快照字节正确", blob.decode("utf-8") == "版本一")

        # 3 恢复 v1 → 生成 v4(restored)，当前=版本一，v2/v3 仍在（验收场景③）
        restored = await ufs.restore_version(uid, f1["id"], v1["id"])
        check("恢复生成 v4", restored.get("versionNo") == 4)
        _, cur = await ufs.read_bytes(uid, f1["id"])
        check("当前内容=v1", cur.decode("utf-8") == "版本一")
        vers2 = (await ufs.list_versions(uid, f1["id"]))["versions"]
        check(
            "v2/v3 未被删除且 v4=restored",
            [v["versionNo"] for v in vers2] == [4, 3, 2, 1]
            and next(v for v in vers2 if v["versionNo"] == 4)["source"] == "restored",
        )

        # 4 存量文件（无版本记录）覆写 → 自动补录基线 v1 再挂 v2（§3.2 规则 2）
        f2 = await ufs.save_file(uid, "存量.txt", "旧内容".encode("utf-8"))
        async with async_session() as session:
            await session.execute(
                sa_delete(AgentUserFileVersion).where(AgentUserFileVersion.file_id == f2["id"])
            )
            await session.commit()  # 模拟 Phase B 之前的存量文件
        s = await ufs.overwrite_file(uid, f2["id"], "新内容".encode("utf-8"), change_summary="沙箱重新生成")
        lv = (await ufs.list_versions(uid, f2["id"]))["versions"]
        base = next((v for v in lv if v["versionNo"] == 1), None)
        check(
            "存量覆写补录基线 v1 + 新 v2",
            s.get("versionNo") == 2 and base and base["changeSummary"] == "历史版本（自动补录）",
        )
        _, oldb = await ufs.read_version_bytes(uid, f2["id"], base["id"])
        check("基线 v1 = 覆写前旧内容", oldb.decode("utf-8") == "旧内容")

        # 5 草稿版（§4.3.4）：不动当前指针
        d = await ufs.save_draft_version(uid, f2["id"], "坏产物".encode("utf-8"), change_summary="审查未通过")
        _, cur2 = await ufs.read_bytes(uid, f2["id"])
        dv = (await ufs.list_versions(uid, f2["id"]))["versions"][0]
        check("草稿版挂上（status=draft）", d.get("draft") and dv["status"] == "draft")
        check("草稿不动当前内容", cur2.decode("utf-8") == "新内容")

        # 6 run_code 审查未通过 → 交付前静默拦截，不进入用户版本历史
        fj = await ufs.create_text_file(uid, "数据.json", '{"ok": 1}', thread_id=thread)
        tools = await main_agent.build_tools(
            token="", knowledge_ids=None, web_enabled=False, user_id=uid, thread_id=thread,
        )
        rc = next(t for t in tools if t.name == "run_code")
        out = await rc.execute({
            "code": "import pathlib\npathlib.Path('/workspace/outputs/数据.json').write_text('{oops', encoding='utf-8')\n",
            "file_ids": [fj["id"]],
        })
        # 2026-07-15 拍板：坏版本必须真实存为草稿（「草稿在版本历史」不能是假话）
        check("内部回执明示已存草稿", "草稿版本" in out and "需修正" in out)
        _, curj = await ufs.read_bytes(uid, fj["id"])
        check("原文件保持上一版（合法 JSON）", curj.decode("utf-8") == '{"ok": 1}')
        jvers = (await ufs.list_versions(uid, fj["id"]))["versions"]
        check("失败中间版已存为草稿版本", any(v["status"] == "draft" for v in jvers))

        # 6.1 首次生成就失败：也要真实落地（当前版即草稿语义），修复重跑同名自动覆盖
        bad_new_name = "首次失败产物.json"
        out_new = await rc.execute({
            "code": (
                "import pathlib\n"
                f"pathlib.Path('/workspace/outputs/{bad_new_name}').write_text('{{oops', encoding='utf-8')\n"
            ),
            "file_ids": [],
        })
        bad_new = await ufs.find_generated_file(uid, bad_new_name, thread)
        check("首次失败产物回执明示已保存待修复", "已保存" in out_new and "重跑覆盖" in out_new)
        check("首次失败产物已真实创建（可被同名重跑覆盖）", bad_new is not None, str(bad_new))

        # 6.5 并发写防护（第二轮评审 P1）：8 路并发覆写同一文件 → 行锁串行化，版本号不重号
        from app.core.config import settings
        fc = await ufs.save_file(uid, "并发.txt", "v0".encode("utf-8"))
        results_c = await asyncio.gather(
            *[ufs.update_file_content(uid, fc["id"], f"内容{i}") for i in range(8)],
            return_exceptions=True,
        )
        oks = [r for r in results_c if isinstance(r, dict)]
        errs = [r for r in results_c if not isinstance(r, dict)]
        check("并发 8 写全部成功（FOR UPDATE 串行化）", len(oks) == 8, f"errs={errs[:2]}")
        cv = (await ufs.list_versions(uid, fc["id"]))["versions"]
        nos = [v["versionNo"] for v in cv]
        check(
            "版本号无重复且连续 1..9",
            len(nos) == len(set(nos)) and sorted(nos) == list(range(1, 10)),
            str(sorted(nos)),
        )

        # 6.6 每文件版本数上限修剪（快照不计配额，上限防磁盘无限增长）
        orig_cap = settings.USER_FILES_MAX_VERSIONS_PER_FILE
        settings.USER_FILES_MAX_VERSIONS_PER_FILE = 5
        try:
            for i in range(4):
                await ufs.update_file_content(uid, fc["id"], f"prune{i}")
            pv = (await ufs.list_versions(uid, fc["id"]))["versions"]
            pn = sorted(v["versionNo"] for v in pv)
            check("修剪后版本数=上限", len(pv) == 5, str(len(pv)))
            check("保留最新、剪最旧", pn == list(range(9, 14)), str(pn))
        finally:
            settings.USER_FILES_MAX_VERSIONS_PER_FILE = orig_cap

        # 7 REST 三端点（归属 + 越权）
        async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
            r = await client.get(f"/files/{f1['id']}/versions", headers=hdr(uid))
            check("GET versions 200", r.status_code == 200 and len(r.json()["versions"]) == 4)
            r = await client.get(
                f"/files/{f1['id']}/versions/{v1['id']}/download", headers=hdr(uid)
            )
            check("GET version download 内容一致", r.status_code == 200 and r.content.decode() == "版本一")
            r = await client.post(
                f"/files/{f1['id']}/versions/{v1['id']}/restore", headers=hdr(uid)
            )
            check("POST restore 得 v5", r.status_code == 200 and r.json().get("versionNo") == 5)
            r = await client.get(f"/files/{f1['id']}/versions", headers=hdr("other_user"))
            check("越权查版本=404", r.status_code == 404)

        # 8 删除级联：版本记录清空
        await ufs.delete_file(uid, f2["id"])
        async with async_session() as session:
            left = (
                await session.execute(
                    select(AgentUserFileVersion).where(AgentUserFileVersion.file_id == f2["id"])
                )
            ).scalars().all()
        check("删除文件级联清版本", not left)
    finally:
        async with async_session() as session:
            rows = (
                await session.execute(select(AgentUserFile).where(AgentUserFile.user_id == uid))
            ).scalars().all()
        for r in rows:
            try:
                await ufs.delete_file(uid, r.id)
            except Exception:  # noqa: BLE001
                pass

    await engine.dispose()  # 收口连接池：避免事件循环关闭后 aiomysql 的 __del__ 噪音
    total, ok = len(results), sum(results)
    print(f"\n{ok}/{total} passed")
    sys.exit(0 if ok == total else 1)


asyncio.run(main())
