"""产物审查 v1 冒烟（实施说明 Phase C §4.2 硬校验）。

运行（容器内，需 local 沙箱可用）：docker exec agent-api python scripts/output_review_smoke.py
覆盖：正常 docx/txt 通过、空文件/坏 pptx/非法 json 判 failed、回执含修复指引、
同名产物修复重跑原地更新（不堆重名副本）、文件卡 meta 带 review。
smoke 数据用本次随机 user_id，收尾清理自己的残留。
"""
import asyncio
import sys
import uuid

sys.path.insert(0, "/app")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    ok = bool(ok)
    results.append(ok)
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail and not ok else ""))


async def main():
    from sqlalchemy import delete as sa_delete

    from app.core.database import Base, async_session, engine
    from app.models import AgentUserFile
    from app.core.config import settings
    from app.services import main_agent
    from app.services.files import user_file_service as ufs
    from app.services.sandbox import code_runner

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    uid = f"smoke_rv_{uuid.uuid4().hex[:8]}"
    thread = f"smoke_rv_thread_{uuid.uuid4().hex[:6]}"
    # 本脚本只验结构硬校验；独立视觉/需求审查由 visual_review_smoke 单独覆盖，避免两个
    # 冒烟相互依赖外部多模态端点与主观评分。
    original_visual_review = settings.SANDBOX_VISUAL_REVIEW_ENABLED
    settings.SANDBOX_VISUAL_REVIEW_ENABLED = False

    try:
        # 1 直接内核：一次产出 好docx / 好txt / 空文件 / 坏pptx / 非法json，逐个断言审查结论
        code = (
            "import docx, pathlib\n"
            "d = docx.Document(); d.add_paragraph('审查冒烟正文'); d.save('/workspace/outputs/好文档.docx')\n"
            "pathlib.Path('/workspace/outputs/正常.txt').write_text('ok', encoding='utf-8')\n"
            "pathlib.Path('/workspace/outputs/空文件.docx').write_bytes(b'')\n"
            "pathlib.Path('/workspace/outputs/坏演示.pptx').write_bytes(b'not-a-zip-at-all')\n"
            "pathlib.Path('/workspace/outputs/坏数据.json').write_text('{oops', encoding='utf-8')\n"
        )
        res = await code_runner.run_code(code, fetch_output_bytes=True)
        check("脚本本身执行成功", res.ok and res.exit_code == 0, f"{res.error or res.stderr[:200]}")
        rv = {f["name"]: (f.get("review") or {}).get("status") for f in res.output_files}
        check("好 docx 审查通过", rv.get("好文档.docx") == "passed", str(rv))
        check("正常 txt 审查通过", rv.get("正常.txt") == "passed", str(rv))
        check("空文件判 failed", rv.get("空文件.docx") == "failed", str(rv))
        check("坏 pptx 判 failed（打不开）", rv.get("坏演示.pptx") == "failed", str(rv))
        check("非法 json 判 failed", rv.get("坏数据.json") == "failed", str(rv))
        check("总体结论 failed", (res.review or {}).get("status") == "failed")
        text = res.to_tool_text()
        check("回执含检查段", "产物检查" in text and "✗ 未通过" in text)
        # 2026-07-15 拍板：定向修复语义——只修列出的错误、同名重跑、坏版本已存草稿
        check("回执含定向修复指引（同名重跑）", "相同文件名" in text and "只修复" in text)

        # 2 工具层：同名产物修复重跑 → 原地更新不堆副本；文件卡 meta 带 review
        meta: dict = {}
        tools = await main_agent.build_tools(
            token="", knowledge_ids=None, web_enabled=False,
            user_id=uid, thread_id=thread, tool_meta_sink=meta,
        )
        rc = next(t for t in tools if t.name == "run_code")
        out1 = await rc.execute({
            "code": "import pathlib\npathlib.Path('/workspace/outputs/报告.txt').write_text('v1', encoding='utf-8')\n"
        })
        check("第一次产出并入库", "报告.txt" in out1 and "产物已存入" in out1)
        out2 = await rc.execute({
            "code": "import pathlib\npathlib.Path('/workspace/outputs/报告.txt').write_text('v2 修复后', encoding='utf-8')\n"
        })
        check("重跑同名 → 原地更新", "已原地更新" in out2)
        files = (await ufs.list_files(uid, "__all__"))["files"]
        same = [f for f in files if f["filename"] == "报告.txt"]
        check("同名产物只有一份", len(same) == 1, f"{len(same)} 份")
        if same:
            _, blob = await ufs.read_bytes(uid, same[0]["id"])
            check("内容为修复后版本", blob.decode("utf-8") == "v2 修复后")
        mfiles = (meta.get("run_code") or {}).get("files") or []
        check(
            "文件卡 meta 带 review 状态",
            bool(mfiles) and (mfiles[0].get("review") or {}).get("status") == "passed",
            str(mfiles)[:200],
        )
        check("meta 带总体 review_status", (meta.get("run_code") or {}).get("review_status") == "passed")
    finally:
        settings.SANDBOX_VISUAL_REVIEW_ENABLED = original_visual_review
        async with async_session() as session:
            rows = (await session.execute(
                AgentUserFile.__table__.select().where(AgentUserFile.user_id == uid)
            )).fetchall()
            for r in rows:
                try:
                    await ufs.delete_file(uid, r.id)
                except Exception:  # noqa: BLE001
                    pass
            await session.execute(sa_delete(AgentUserFile).where(AgentUserFile.user_id == uid))
            await session.commit()

    await engine.dispose()  # 收口连接池：避免事件循环关闭后 aiomysql 的 __del__ 噪音
    total, ok = len(results), sum(results)
    print(f"\n{ok}/{total} passed")
    sys.exit(0 if ok == total else 1)


asyncio.run(main())
