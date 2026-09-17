"""「我的文件」冒烟（ADR-047 §6.6）：REST 五端点 + 配额/TTL/ACL/惰性清理 + 模型工具。

运行（容器内）：docker exec agent-api python scripts/user_files_smoke.py
注意：配额/超限两项在服务层验证（settings 突变只影响本脚本进程，不影响运行中的 uvicorn）。
smoke 数据用本次随机 user_id，收尾只清理自己的残留，不影响并发评测/真实用户。
"""
import asyncio
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

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
    from sqlalchemy import delete as sa_delete, update as sa_update

    from app.core.config import settings
    from app.core.database import Base, async_session, engine
    from app.models import AgentUserFile
    from app.services import main_agent
    from app.services.files import user_file_service as ufs

    # 表存在性安全网（幂等；正常由 lifespan create_all 建）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    uid_a = f"smoke_uf_{uuid.uuid4().hex[:8]}"
    uid_b = f"smoke_uf_{uuid.uuid4().hex[:8]}"

    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
            # 1 上传（uploaded 永久）
            r = await client.post(
                "/files/upload", headers=hdr(uid_a),
                files={"file": ("测试报告.txt", "hello 中文内容".encode("utf-8"), "text/plain")},
            )
            check("upload 200", r.status_code == 200, r.text[:200])
            f1 = r.json()
            check("uploaded 永久无 TTL", f1.get("source") == "uploaded" and f1.get("expiresAt") is None)

            # 2 列表 + 配额
            r = await client.get("/files", headers=hdr(uid_a))
            data = r.json()
            check(
                "list 含新文件+配额计数",
                any(f["id"] == f1["id"] for f in data["files"]) and data["quota"]["usedBytes"] > 0,
            )

            # 3 下载回读
            r = await client.get(f"/files/{f1['id']}/download", headers=hdr(uid_a))
            check("download 内容一致", r.status_code == 200 and r.content.decode("utf-8") == "hello 中文内容")

            # 4 越权：B 访问 A 的文件一律 404
            r = await client.get(f"/files/{f1['id']}/download", headers=hdr(uid_b))
            check("ACL 越权下载=404", r.status_code == 404)
            r = await client.delete(f"/files/{f1['id']}", headers=hdr(uid_b))
            check("ACL 越权删除=404", r.status_code == 404)

            # 5 generated + TTL + keep（generated 无 REST 入口，服务层模拟产物入库）
            f2 = await ufs.save_file(uid_a, "产物.csv", b"a,b\n1,2\n", source="generated", thread_id="t_smoke")
            check("generated 带 TTL+来源会话", f2["expiresAt"] is not None and f2["threadId"] == "t_smoke")
            r = await client.post(f"/files/{f2['id']}/keep", headers=hdr(uid_a))
            check("keep 转永久", r.status_code == 200 and r.json().get("expiresAt") is None)

            # 6 过期惰性清理：手动置过期 → list 后记录与磁盘都消失
            f3 = await ufs.save_file(uid_a, "过期品.txt", b"stale", source="generated")
            async with async_session() as s:
                await s.execute(
                    sa_update(AgentUserFile)
                    .where(AgentUserFile.id == f3["id"])
                    .values(expires_at=datetime.now() - timedelta(days=1))
                )
                await s.commit()
            r = await client.get("/files", headers=hdr(uid_a))
            check("过期项被惰性清理", all(f["id"] != f3["id"] for f in r.json()["files"]))
            disk3 = Path(settings.USER_FILES_DIR) / uid_a / f"{f3['id']}_过期品.txt"
            check("过期项磁盘已删", not disk3.exists())

            # 7 单文件超限（服务层，本进程 settings 突变）
            orig_size = settings.USER_FILES_MAX_SIZE_MB
            settings.USER_FILES_MAX_SIZE_MB = 0
            try:
                await ufs.save_file(uid_a, "big.bin", b"xx")
                check("单文件超限=413", False)
            except ufs.UserFileError as e:
                check("单文件超限=413", e.status_code == 413)
            finally:
                settings.USER_FILES_MAX_SIZE_MB = orig_size

            # 8 数量上限
            orig_count = settings.USER_FILES_MAX_COUNT
            settings.USER_FILES_MAX_COUNT = 1
            try:
                await ufs.save_file(uid_a, "more.txt", b"x")
                check("数量上限=400", False)
            except ufs.UserFileError as e:
                check("数量上限=400", e.status_code == 400)
            finally:
                settings.USER_FILES_MAX_COUNT = orig_count

            # 9 模型工具：build_tools 注册 + 实际执行
            tools = await main_agent.build_tools(
                token="", knowledge_ids=None, web_enabled=False, user_id=uid_a
            )
            names = {t.name for t in tools}
            check("build_tools 注册 list_files/read_file", {"list_files", "read_file"} <= names)
            check("文件工具 internal 直连", all(t.internal for t in tools if t.name in ("list_files", "read_file")))
            lf = next(t for t in tools if t.name == "list_files")
            rf = next(t for t in tools if t.name == "read_file")
            out = await lf.execute({})
            check("list_files 列出清单", "测试报告.txt" in out and f1["id"] in out)
            # 归入文件夹的文件对模型仍可见（__all__ 语义 + 文件夹标注）——否则用户整理文件后 AI 反而找不到
            fold = await ufs.create_folder(uid_a, "smoke夹")
            fmv = await ufs.save_file(uid_a, "夹内文件.txt", "folder visible".encode("utf-8"))
            await ufs.move_file(uid_a, fmv["id"], fold["id"])
            out = await lf.execute({})
            check(
                "list_files 可见文件夹内文件且带标注",
                "夹内文件.txt" in out and "文件夹：smoke夹" in out,
            )
            out = await rf.execute({"file_id": f1["id"]})
            check("read_file 读到内容", "hello 中文内容" in out)
            out = await rf.execute({"file_id": "no_such_id"})
            check("read_file 不存在→友好提示不抛异常", "读取失败" in out)
            # 无 user_id 时不注册（普通检索会话不受影响）
            tools_no_uid = await main_agent.build_tools(token="", knowledge_ids=None, web_enabled=False)
            check("无 user_id 不注册文件工具", not [t for t in tools_no_uid if t.name in ("list_files", "read_file")])

            # 9.5 预览接口 + 写能力（update/create/run_code）
            r = await client.get(f"/files/{f1['id']}/content", headers=hdr(uid_a))
            check(
                "content 预览返回原文",
                r.status_code == 200 and r.json()["kind"] == "text" and r.json()["text"] == "hello 中文内容",
            )
            uf = next(t for t in tools if t.name == "update_file")
            cf = next(t for t in tools if t.name == "create_file")
            rc = next(t for t in tools if t.name == "run_code")
            out = await uf.execute({"file_id": f1["id"], "content": "改写后的新内容 v2"})
            check("update_file 覆写成功", "已更新" in out)
            _, blob2 = await ufs.read_bytes(uid_a, f1["id"])
            check("覆写后磁盘内容一致", blob2.decode("utf-8") == "改写后的新内容 v2")
            fx = await ufs.save_file(uid_a, "假文档.docx", b"PK-not-really-docx")
            out = await uf.execute({"file_id": fx["id"], "content": "x"})
            check("非文本格式拒绝覆写", "写入失败" in out and "沙箱" in out)
            # edit_file：精确替换（局部改动不回传全文；与 update_file 互补）
            ef = next(t for t in tools if t.name == "edit_file")
            fe = await ufs.create_text_file(uid_a, "编辑测试.md", "第一行 alpha\n第二行 alpha\n第三行 beta\n")
            out = await ef.execute({"file_id": fe["id"], "old_string": "第三行 beta", "new_string": "第三行 gamma"})
            check("edit_file 单处替换成功", "已完成 1 处替换" in out)
            _, eb = await ufs.read_bytes(uid_a, fe["id"])
            check("替换后磁盘内容一致", eb.decode("utf-8") == "第一行 alpha\n第二行 alpha\n第三行 gamma\n")
            out = await ef.execute({"file_id": fe["id"], "old_string": "没有这段", "new_string": "x"})
            check("edit_file 未命中→友好提示", "未找到 old_string" in out)
            out = await ef.execute({"file_id": fe["id"], "old_string": "alpha", "new_string": "omega"})
            check("edit_file 多处命中默认拒绝", "无法唯一定位" in out)
            out = await ef.execute(
                {"file_id": fe["id"], "old_string": "alpha", "new_string": "omega", "replace_all": True}
            )
            check("edit_file replace_all 全部替换", "已完成 2 处替换" in out)
            _, eb2 = await ufs.read_bytes(uid_a, fe["id"])
            check("replace_all 后内容一致", "alpha" not in eb2.decode("utf-8") and "omega" in eb2.decode("utf-8"))
            out = await ef.execute({"file_id": fx["id"], "old_string": "PK", "new_string": "x"})
            check("edit_file 非文本格式拒绝", "写入失败" in out)
            out = await cf.execute({"filename": "整理结果.md", "content": "# 标题\n正文"})
            check("create_file 新建成功", "已创建" in out and "file_id=" in out)
            out = await rc.execute({
                "code": (
                    "import pathlib\n"
                    "src = pathlib.Path('/workspace/inputs/测试报告.txt').read_text(encoding='utf-8')\n"
                    "pathlib.Path('/workspace/outputs/结果.txt').write_text(src.upper(), encoding='utf-8')\n"
                    "print('done', len(src))\n"
                ),
                "file_ids": [f1["id"]],
            })
            check("run_code 沙箱执行+产物入库", "exit_code=0" in out and "产物已存入" in out and "结果.txt" in out)
            saved_list = await ufs.list_files(uid_a)
            gen = [f for f in saved_list["files"] if f["filename"] == "结果.txt"]
            check("run_code 产物在列表中且为 generated", bool(gen) and gen[0]["source"] == "generated")
            if gen:
                _, gen_blob = await ufs.read_bytes(uid_a, gen[0]["id"])
                check("产物内容=脚本真实计算结果", gen_blob.decode("utf-8") == "改写后的新内容 V2")

            # 10 composer file_ids → 可操作附件（ADR-047 §6.6 选中文件进对话）
            # 文本文件：给内容预览 + file_id + 操作指引
            atts = await ufs.build_chat_attachments(uid_a, [f1["id"], "no_such_id"])
            check(
                "文本文件附件带 file_id + 内容预览 + 操作指引",
                len(atts) == 2
                and atts[0]["filename"] == "测试报告.txt"
                and atts[0].get("file_id") == f1["id"]
                and "改写后的新内容 v2" in atts[0]["text"]
                and ("update_file" in atts[0]["text"] or "read_file" in atts[0]["text"]),
            )
            check("失效 file_id 降级为说明文本不炸", "不可用" in atts[1]["text"])
            # 二进制文件（.docx）：不解码乱码，给 run_code 可操作指引
            batts = await ufs.build_chat_attachments(uid_a, [fx["id"]])
            check(
                "二进制文件附件走 run_code 指引（不喂乱码）",
                len(batts) == 1
                and "run_code" in batts[0]["text"]
                and f"file_ids=['{fx['id']}']" in batts[0]["text"]
                and batts[0].get("file_id") == fx["id"],
            )

            # 11 删除
            r = await client.delete(f"/files/{f1['id']}", headers=hdr(uid_a))
            check("删除 ok", r.status_code == 200)
            r = await client.get("/files", headers=hdr(uid_a))
            check("删除后列表收敛", all(f["id"] != f1["id"] for f in r.json()["files"]))
    finally:
        # 只清理本次随机 uid 的残留
        import shutil

        async with async_session() as s:
            await s.execute(sa_delete(AgentUserFile).where(AgentUserFile.user_id.in_([uid_a, uid_b])))
            await s.commit()
        for uid in (uid_a, uid_b):
            shutil.rmtree(Path(settings.USER_FILES_DIR) / uid, ignore_errors=True)

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} passed")
    raise SystemExit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
