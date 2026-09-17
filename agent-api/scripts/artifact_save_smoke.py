"""产物→我的文件打通冒烟（docker exec agent-api python scripts/artifact_save_smoke.py）。

坐实 save_generated_artifact 幂等语义（真 PG，用后即清）：
1. 首存 → 新建 generated 文件（带 thread、versionNo=1）；
2. 同内容重存 → unchanged=True、同 file_id、不长版本；
3. 内容变化 → 同 file_id 原地新版本（versionNo=2）；
4. 不同会话同名 → 各自独立文件；
5. 空内容拒绝。
"""
import asyncio
import sys

sys.path.insert(0, "/app")

from app.services.files import user_file_service as ufs  # noqa: E402

PASS = 0
FAIL = 0
UID = "e2e-artifact-save"


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


async def main() -> None:
    html1 = "<!DOCTYPE html><html><head><title>测试页</title></head><body>v1</body></html>"
    html2 = html1.replace("v1", "v2")
    created = []
    try:
        print("[1] 首存新建")
        a = await ufs.save_generated_artifact(UID, "测试页.html", html1, thread_id="t-art-1")
        created.append(a["id"])
        check("新建成功且 generated", bool(a.get("id")) and a.get("source") == "generated", str(a))
        check("versionNo=1 且 unchanged=False", a.get("versionNo") == 1 and a.get("unchanged") is False)

        print("[2] 同内容重存 → 幂等跳过")
        b = await ufs.save_generated_artifact(UID, "测试页.html", html1, thread_id="t-art-1")
        check("同 file_id 且 unchanged=True", b["id"] == a["id"] and b.get("unchanged") is True, str(b))
        vers = await ufs.list_versions(UID, a["id"])
        check("版本数仍为 1", len(vers["versions"]) == 1, f"got {len(vers['versions'])}")

        print("[3] 内容变化 → 原地新版本")
        c = await ufs.save_generated_artifact(UID, "测试页.html", html2, thread_id="t-art-1")
        check("同 file_id 且 versionNo=2", c["id"] == a["id"] and c.get("versionNo") == 2, str(c))

        print("[4] 不同会话同名 → 独立文件")
        d = await ufs.save_generated_artifact(UID, "测试页.html", html1, thread_id="t-art-2")
        created.append(d["id"])
        check("新 file_id", d["id"] != a["id"])

        print("[5] 空内容拒绝")
        try:
            await ufs.save_generated_artifact(UID, "空.html", "", thread_id="t-art-1")
            check("应抛 UserFileError", False)
        except ufs.UserFileError:
            check("空内容抛 UserFileError", True)
    finally:
        for fid in created:
            try:
                await ufs.delete_file(UID, fid)
            except Exception as e:  # noqa: BLE001
                print(f"  (清理 {fid} 失败: {e})")

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
