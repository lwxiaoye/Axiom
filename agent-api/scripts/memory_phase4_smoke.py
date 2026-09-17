"""Phase 4 记忆召回综合分 + 重复强化冒烟（docker exec agent-api python scripts/memory_phase4_smoke.py）。

覆盖：
1. _grams/_keyword_score 纯函数（中文 2-gram + ASCII 整词）；
2. _rank_by_relevance 综合分（打桩 _embed_many，确定性）：关键词救回低余弦实体查询、
   置信度/新鲜度乘性折扣只降序不救场；
3. 真 PG 重复强化：同内容二次写入 confidence +5、updated_at 刷新（用后即清）。
"""
import asyncio
import math
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, "/app")

from app.services.memory import memory_service as ms  # noqa: E402

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


def test_grams() -> None:
    print("[1] 关键词纯函数")
    g = ms._grams("Zephyr 项目的进展")
    check("ASCII 整词", "zephyr" in g)
    check("中文 2-gram", "项目" in g and "进展" in g)
    qg = ms._grams("Zephyr 项目")
    check("全命中覆盖率=1", ms._keyword_score(qg, "zephyr 项目用 mysql") == 1.0)
    check("零命中=0", ms._keyword_score(qg, "周末去爬山") == 0.0)
    check("空查询=0", ms._keyword_score(set(), "任意") == 0.0)


def _vec(cos: float):
    return [cos, math.sqrt(max(0.0, 1 - cos * cos))]


async def test_ranking() -> None:
    print("[2] 综合分排序（打桩 embedding）")
    now = datetime.now()
    r_entity = SimpleNamespace(content="Zephyr 项目用 MySQL 数据库", confidence=100, updated_at=now)
    r_plain = SimpleNamespace(content="周末喜欢去爬山看电影", confidence=100, updated_at=now)
    r_stale = SimpleNamespace(content="周末常去公园散步遛狗", confidence=50,
                              updated_at=now - timedelta(days=180))
    r_noise = SimpleNamespace(content="完全无关的一句话", confidence=100, updated_at=now)
    # 余弦：实体条 0.25（裸余弦低于 0.30 下限）、普通/陈旧条 0.50、噪音条 0.10
    cos_map = {r_entity.content: 0.25, r_plain.content: 0.50,
               r_stale.content: 0.50, r_noise.content: 0.10}

    orig = ms._embed_many

    async def fake_embed(texts):
        return [[1.0, 0.0]] + [_vec(cos_map[t]) for t in texts[1:]]

    ms._embed_many = fake_embed
    try:
        ranked = await ms._rank_by_relevance(
            "Zephyr 项目", [r_entity, r_plain, r_stale, r_noise])
    finally:
        ms._embed_many = orig

    contents = [r.content for r in ranked]
    check("关键词救回低余弦实体条", r_entity.content in contents,
          f"got {contents}")
    check("噪音条被下限过滤", r_noise.content not in contents)
    check("同余弦下 低置信+陈旧 排在 满置信+新鲜 之后",
          contents.index(r_plain.content) < contents.index(r_stale.content)
          if r_plain.content in contents and r_stale.content in contents else False,
          f"got {contents}")


async def test_reinforce() -> None:
    print("[3] 真 PG 重复强化")
    from sqlalchemy import delete as sa_delete
    from app.core.runtime_db import runtime_session
    from app.runtime_models import AgentUserMemory

    factory = runtime_session()
    if factory is None:
        print("  - Runtime 库未配置，跳过（其余用例不受影响）")
        return
    uid = "e2e-phase4-mem"
    content = "用户在 Phase4 冒烟中偏好简洁回答"
    try:
        mid1, new1 = await ms.store_memory(
            user_id=uid, mem_type="preference", content=content,
            confidence=ms._EXTRACT_CONFIDENCE, return_new=True)
        check("首次写入 is_new=True", bool(mid1) and new1 is True)
        async with factory() as session:
            row = await session.get(AgentUserMemory, mid1)
            t1, c1 = row.updated_at, row.confidence
        check("抽取路径 confidence=80", c1 == ms._EXTRACT_CONFIDENCE, f"got {c1}")

        await asyncio.sleep(1.1)  # 保证 updated_at 可分辨
        mid2, new2 = await ms.store_memory(
            user_id=uid, mem_type="preference", content=content, return_new=True)
        check("二次写入命中同条且 is_new=False", mid2 == mid1 and new2 is False)
        async with factory() as session:
            row = await session.get(AgentUserMemory, mid1)
            check("confidence +%d" % ms._REINFORCE_BUMP,
                  row.confidence == c1 + ms._REINFORCE_BUMP, f"got {row.confidence}")
            check("updated_at 已刷新", t1 is not None and row.updated_at > t1,
                  f"{t1} -> {row.updated_at}")
    finally:
        async with factory() as session:
            await session.execute(
                sa_delete(AgentUserMemory).where(AgentUserMemory.user_id == uid))
            await session.commit()


async def main() -> None:
    test_grams()
    await test_ranking()
    await test_reinforce()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
