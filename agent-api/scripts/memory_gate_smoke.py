"""记忆模块整改冒烟（docker exec agent-api python scripts/memory_gate_smoke.py）。

覆盖（2026-07-14 记忆专项评审整改，真 PG、测试数据用后即清）：
1. 总开关硬门禁：关闭后 store_memory 一律拒写（工具/手动/NL 全路径的存储层兜底）；
2. update_memory 真编辑：相似小编辑同 id 原地改、记忆不消失（旧「先加后删」的消失 bug）；
3. update_memory 校验：敏感/非法类型 ValueError、不存在/跨用户 KeyError；
4. 幽灵摘要清理：AgentThreadSummary 行可被 delete_for_thread 删除（truncate 分支调用它）。
"""
import asyncio
import sys

sys.path.insert(0, "/app")

from app.services.memory import context_service, memory_service as ms  # noqa: E402

PASS = 0
FAIL = 0
UID = "e2e-mem-gate"


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


async def cleanup():
    from sqlalchemy import delete as sa_delete
    from app.core.runtime_db import runtime_session
    from app.runtime_models import AgentUserMemory, AgentUserSetting
    factory = runtime_session()
    if factory is None:
        return
    async with factory() as session:
        await session.execute(sa_delete(AgentUserMemory).where(AgentUserMemory.user_id == UID))
        await session.execute(sa_delete(AgentUserSetting).where(AgentUserSetting.user_id == UID))
        await session.commit()


async def main():
    from app.core.runtime_db import runtime_session
    if runtime_session() is None:
        print("Runtime 库未配置，无法冒烟")
        sys.exit(1)
    await cleanup()
    try:
        print("[1] 总开关硬门禁")
        await ms.set_enabled(UID, False)
        mid, is_new = await ms.store_memory(
            user_id=UID, mem_type="preference", content="关着也想记", return_new=True)
        check("关闭后 store_memory 拒写", mid is None and is_new is False, f"mid={mid}")
        await ms.set_enabled(UID, True)
        mid2, new2 = await ms.store_memory(
            user_id=UID, mem_type="preference", content="用户偏好简洁的回答", return_new=True)
        check("开启后写入成功", bool(mid2) and new2 is True)

        print("[2] update_memory 真编辑（相似小编辑不消失）")
        out = await ms.update_memory(UID, mid2, content="用户偏好简洁的回答风格")
        check("同 id 原地改", out["id"] == mid2 and out["content"] == "用户偏好简洁的回答风格")
        rows = await ms.list_memories(UID)
        check("记忆仍在且只有一条", len(rows) == 1 and rows[0]["id"] == mid2,
              f"count={len(rows)}")
        out2 = await ms.update_memory(UID, mid2, mem_type="fact")
        check("单改类型", out2["type"] == "fact" and out2["content"] == "用户偏好简洁的回答风格")

        print("[3] update_memory 校验")
        try:
            await ms.update_memory(UID, mid2, content="工资是 5 万")
            check("敏感内容应拒", False)
        except ValueError:
            check("敏感内容 ValueError", True)
        try:
            await ms.update_memory(UID, mid2, mem_type="hacker")
            check("非法类型应拒", False)
        except ValueError:
            check("非法类型 ValueError", True)
        try:
            await ms.update_memory(UID, "no-such-id", content="x")
            check("不存在应 KeyError", False)
        except KeyError:
            check("不存在 KeyError", True)
        try:
            await ms.update_memory("other-user", mid2, content="越权改")
            check("跨用户应 KeyError", False)
        except KeyError:
            check("跨用户 KeyError", True)

        print("[5] 复审修订：关闭开关后 PUT 编辑也被拒（改成新信息=记录新信息）")
        await ms.set_enabled(UID, False)
        try:
            await ms.update_memory(UID, mid2, content="关着也想改成新信息")
            check("关闭后 update_memory 应拒", False)
        except ValueError as e:
            check("关闭后 update_memory ValueError", "已关闭" in str(e))
        await ms.set_enabled(UID, True)

        print("[6] 复审修订：is_enabled 读取失败 fail-closed 且不缓存")
        class _BadCtx:
            async def __aenter__(self):
                raise RuntimeError("PG 瞬断")
            async def __aexit__(self, *a):
                return False
        orig_rs = ms.runtime_session
        ms._ENABLED_CACHE.clear()
        ms.runtime_session = lambda: (lambda: _BadCtx())
        try:
            check("读取失败返回 False（fail-closed）", await ms.is_enabled(UID) is False)
        finally:
            ms.runtime_session = orig_rs
        check("故障不缓存：恢复后立即读到真值 True", await ms.is_enabled(UID) is True)

        print("[7] 复审修订：PUT 排除自身的去重（不制造重复记忆）")
        midB, _ = await ms.store_memory(
            user_id=UID, mem_type="fact", content="用户在计算机学院工作", return_new=True)
        try:
            await ms.update_memory(UID, mid2, content="用户在计算机学院工作")
            check("改成另一条的相同内容应拒", False)
        except ValueError as e:
            check("精确重复 ValueError", "相同" in str(e))
        rows2 = await ms.list_memories(UID)
        check("两条记忆都还在（无重复无丢失）", len(rows2) == 2, f"count={len(rows2)}")
        out_self = await ms.update_memory(UID, mid2, content="用户偏好简洁的回答风格！")
        check("与自身相似的小编辑正常放行", out_self["id"] == mid2)

        print("[8] 复审修订：set_enabled 未配置必须抛（不再假装成功）")
        ms.runtime_session = lambda: None
        try:
            await ms.set_enabled(UID, False)
            check("未配置应抛 ValueError", False)
        except ValueError:
            check("未配置 ValueError", True)
        finally:
            ms.runtime_session = orig_rs

        print("[9] 复审修订：delete_for_thread strict 失败必须抛")
        orig_cs_rs = context_service.runtime_session
        context_service.runtime_session = lambda: (lambda: _BadCtx())
        try:
            await context_service.delete_for_thread("t-x")  # 非 strict：静默
            check("非 strict 失败静默", True)
            try:
                await context_service.delete_for_thread("t-x", strict=True)
                check("strict 失败应抛", False)
            except RuntimeError:
                check("strict RuntimeError", True)
        finally:
            context_service.runtime_session = orig_cs_rs

        print("[4] 幽灵摘要清理（delete_for_thread 行为）")
        from app.core.runtime_db import runtime_session as rs
        from app.runtime_models import AgentThreadSummary
        tid = "e2e-mem-gate-thread"
        factory = rs()
        async with factory() as session:
            existing = await session.get(AgentThreadSummary, tid)
            if existing is None:
                session.add(AgentThreadSummary(
                    thread_id=tid, summary="旧分支的幽灵内容", covered_message_id=1))
                await session.commit()
        await context_service.delete_for_thread(tid)
        async with factory() as session:
            gone = await session.get(AgentThreadSummary, tid)
        check("摘要行已删除", gone is None)
    finally:
        await cleanup()

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
