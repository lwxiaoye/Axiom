"""Run 级沙箱复用真机冒烟（2026-07-22）：真 docker 容器，不是 mock。

验证四件事：跨调用文件真的还在 / 容器真的只建一个 / outputs 增量识别正确 / 作用域关闭后容器真的没了。
运行（agent-api 容器内，需挂 docker socket + provider=local）：
  docker exec agent-api python -u scripts/sandbox_session_reuse_smoke.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SKILL_SANDBOX_PROVIDER", "local")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail and not ok else ""))


async def _containers() -> set:
    """当前存活的沙箱容器名集合（走 docker-py，与 local adapter 同一后端）。"""
    import docker
    client = docker.from_env()
    return {
        c.name for c in await asyncio.to_thread(client.containers.list)
        if c.name.startswith("agent-sbx-")
    }


async def main():
    from app.core.config import settings
    from app.services.sandbox import session_pool
    from app.services.sandbox.code_runner import run_code

    settings.SANDBOX_SESSION_REUSE_ENABLED = True
    settings.SANDBOX_OUTPUT_REVIEW_ENABLED = False   # 冒烟只测生命周期，不跑审查链路
    settings.SANDBOX_VISUAL_REVIEW_ENABLED = False
    scope = "smoke-run-1"
    before = await _containers()

    print("1) 第一次调用：写一个中间文件（不进 outputs）")
    r1 = await run_code(
        "open('/workspace/step1.txt','w').write('第一步的中间结果')\n"
        "print('step1 done')",
        session_key=scope,
    )
    check("第一次执行成功", r1.ok, r1.to_tool_text())
    check("第一次不是复用", r1.reused is False)
    live_after_1 = await _containers() - before
    check("确实起了一个容器且没被拆", len(live_after_1) == 1, str(live_after_1))

    print("2) 第二次调用：读上一次留下的中间文件——这是整个改造的核心")
    r2 = await run_code(
        "print(open('/workspace/step1.txt').read())", session_key=scope,
    )
    check("第二次标记为复用", r2.reused is True)
    check("上一次的中间文件还在", r2.ok and "第一步的中间结果" in r2.stdout, r2.to_tool_text())
    check("没有再建新容器", (await _containers() - before) == live_after_1)

    print("3) 第三次调用：产出一个 outputs 文件")
    r3 = await run_code(
        "open('/workspace/outputs/报告.txt','w').write('正文')\nprint('ok')",
        session_key=scope,
    )
    check("产物被收到", [f["name"] for f in r3.output_files] == ["报告.txt"], r3.to_tool_text())

    print("4) 第四次调用：只做检查、不动产物 → 不能重复回收")
    r4 = await run_code(
        "import os; print(os.listdir('/workspace/outputs'))", session_key=scope,
    )
    check("本次无新产物", r4.output_files == [], str(r4.output_files))
    check("既有产物如实标为未改动", r4.unchanged_outputs == ["报告.txt"], str(r4.unchanged_outputs))
    check("回执告诉模型不必重做", "不必重做" in r4.to_tool_text())

    print("5) 第五次调用：同名覆盖 → 重新算作本次产物")
    r5 = await run_code(
        "open('/workspace/outputs/报告.txt','w').write('修好的正文')\nprint('ok')",
        session_key=scope,
    )
    check("改动过的产物重新被收", [f["name"] for f in r5.output_files] == ["报告.txt"], r5.to_tool_text())

    print("6) 作用域关闭：容器必须真的消失")
    closed = await session_pool.close_scope(scope)
    check("关闭了 1 个会话", closed == 1, str(closed))
    await asyncio.sleep(0.5)
    check("容器已销毁", (await _containers() - before) == set(), str(await _containers() - before))

    print("7) 关掉开关：完全退回用完即弃")
    settings.SANDBOX_SESSION_REUSE_ENABLED = False
    r7 = await run_code("print('one-shot')", session_key="smoke-run-2")
    check("一次性执行成功", r7.ok and "one-shot" in r7.stdout, r7.to_tool_text())
    check("没有留下会话", session_pool.live_count() == 0)
    await asyncio.sleep(0.5)
    check("没有留下容器", (await _containers() - before) == set())

    ok = all(results)
    print(f"\n{'全部通过' if ok else '存在失败'}：{sum(results)}/{len(results)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
