"""附件当轮制回归（docker exec agent-api python scripts/attachment_scope_smoke.py）。

坐实修复：build_file_context 只注入**本轮**所选文件，不再跨轮合并线程历史附件
（旧 bug：turn1 传 A、turn2 只传 B，模型却收到 A+B）。
用桩替换 session_file_service.retrieve_relevant，令注入内容可断言；不依赖 Runtime 库。
"""
import asyncio
import sys

sys.path.insert(0, "/app")

from app.services.files import session_file_service, thread_attachment_service as tas  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


async def main() -> None:
    # 桩：检索原样回吐文件文本，便于断言注入了哪些文件
    orig = session_file_service.retrieve_relevant

    async def fake_retrieve(query, text):
        return text

    session_file_service.retrieve_relevant = fake_retrieve
    try:
        A = [{"filename": "fileA.txt", "text": "AAA 内容甲"}]
        B = [{"filename": "fileB.txt", "text": "BBB 内容乙"}]

        print("[1] turn1 只传 A → 注入 A")
        ctx1 = await tas.build_file_context(thread_id="t1", user_id="u1", query="问A", attachments=A)
        check("含 A", "fileA.txt" in ctx1 and "AAA 内容甲" in ctx1)

        print("[2] turn2 只传 B → 只注入 B，不含 A（核心修复）")
        ctx2 = await tas.build_file_context(thread_id="t1", user_id="u1", query="问B", attachments=B)
        check("含 B", "fileB.txt" in ctx2 and "BBB 内容乙" in ctx2)
        check("不含 A（不再跨轮合并）", "fileA.txt" not in ctx2 and "AAA 内容甲" not in ctx2,
              f"ctx2={ctx2!r}")

        print("[3] 本轮不传文件 → 返回空（当轮制，不复注上一轮）")
        ctx3 = await tas.build_file_context(thread_id="t1", user_id="u1", query="随便聊", attachments=None)
        check("空串", ctx3 == "")

        print("[4] 本轮传多文件 → 都注入")
        ctx4 = await tas.build_file_context(
            thread_id="t1", user_id="u1", query="问BC", attachments=B + [{"filename": "fileC.md", "text": "CCC"}])
        check("含 B 与 C", "fileB.txt" in ctx4 and "fileC.md" in ctx4)

        print("[5] 空文本附件被过滤")
        ctx5 = await tas.build_file_context(
            thread_id="t1", user_id="u1", query="x", attachments=[{"filename": "empty", "text": ""}])
        check("空文本→空串", ctx5 == "")

        print("[6] delete_for_thread 在无 Runtime 库时不抛异常")
        await tas.delete_for_thread("t1")
        check("delete_for_thread 安全", True)
    finally:
        session_file_service.retrieve_relevant = orig

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
