"""诊断「对话内推荐智能体」为什么不出卡片。

链路：_retrieve_agents 向量检索 → 命中则把智能体目录写进 system prompt →
模型在回复末尾输出 [[RECOMMEND:...]] → 前端渲染卡片。
检索返回空（None）时，system prompt 不含推荐规则，模型不会推荐。

vector_service.search 没有相似度阈值，只取 top_k 最近邻，所以只要
集合非空时就一定有结果。因此推荐出不来必然命中以下之一，
本脚本逐项检查并直接报出问题所在：

  1. 没有 enabled=1 & is_default=1 的默认 Embedding 模型
  2. Qdrant 集合为空（向量没回填）
  3. embedding 调用本身失败（Key / 网关 / 维度）

运行（服务器 agent-api 容器内）：
    docker compose -f agent-api/docker-compose.yml exec agent-api \\
        python scripts/diagnose_recommend.py
    # 可选：用某条真实 query 实测一次检索
    docker compose -f agent-api/docker-compose.yml exec agent-api \\
        python scripts/diagnose_recommend.py --query "我要保修"
"""

import argparse
import asyncio
import os
import sys

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import async_session  # noqa: E402
from app.models import EmbeddingModel, NewApiUserKey  # noqa: E402
from app.services import vector_service  # noqa: E402
from app.services.knowledge import embedding_service

def ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def bad(msg: str) -> None:
    print(f"  ❌ {msg}")


def info(msg: str) -> None:
    print(f"  ·  {msg}")


async def main(args: argparse.Namespace) -> None:
    problems: list[str] = []

    # 1) 默认 embedding 模型
    print("\n[1] 默认 Embedding 模型")
    async with async_session() as s:
        emb = (
            await s.execute(
                select(EmbeddingModel)
                .where(EmbeddingModel.enabled == 1, EmbeddingModel.is_default == 1)
                .order_by(EmbeddingModel.id.asc())
            )
        ).scalars().first()
    if not emb:
        bad("没有 enabled=1 且 is_default=1 的 Embedding 模型 → 检索直接跳过")
        problems.append("缺少默认 Embedding 模型（ai_embedding_model）")
        collection = None
    else:
        collection = vector_service.collection_name(emb.model_id, emb.dimension)
        ok(f"model_id={emb.model_id} dimension={emb.dimension}")
        info(f"对应 Qdrant 集合：{collection}")

    # 2) Qdrant 集合点数
    print("\n[2] Qdrant 向量集合")
    if collection:
        try:
            count = await vector_service.count_collection(collection)
            if count == 0:
                bad(f"集合 {collection} 为空 → 检索永远返回空 → 不推荐")
                problems.append("Qdrant 集合为空，需要跑 backfill_agents.py 回填")
            else:
                ok(f"集合 {collection} 有 {count} 个向量")
        except Exception as e:
            bad(f"读取集合失败（集合可能不存在）：{e}")
            problems.append("Qdrant 集合不存在/不可读，需要回填")
    else:
        info("跳过（无默认模型）")

    # 3) 可选：实测检索
    if args.query:
        print(f"\n[3] 实测检索 query={args.query!r}")
        if not emb:
            bad("无默认模型，无法实测")
        else:
            async with async_session() as s:
                key_row = (
                    await s.execute(
                        select(NewApiUserKey)
                        .where(NewApiUserKey.api_key.isnot(None))
                        .order_by(NewApiUserKey.token_id.desc())
                    )
                ).scalars().first()
            if not key_row or not key_row.api_key:
                bad("new_api_user_key 里没有可用 api_key，无法做 embedding 实测")
            else:
                try:
                    vec = await embedding_service.embed_query(
                        args.query, emb.model_id, key_row.api_key
                    )
                    if len(vec) != emb.dimension:
                        bad(f"embedding 维度 {len(vec)} != 模型维度 {emb.dimension}")
                        problems.append("embedding 维度与模型配置不一致")
                    else:
                        ok(f"embedding 成功，维度 {len(vec)}")
                        hits = await vector_service.search(
                            collection=collection,
                            query_vec=vec,
                            user_role_ids=[],
                            user_dept_ids=[],
                            is_admin_user=False,
                            top_k=15,
                            # 租户过滤已在 Qdrant 侧执行：默认 '0' 只看得到全局应用，
                            # 要复现某租户用户的真实召回必须显式 --tenant
                            tenant_id=args.tenant,
                        )
                        if hits:
                            ok(f"检索到 {len(hits)} 个智能体（前 5）：")
                            for h in hits[:5]:
                                info(f"id={h['id']} name={h['name']}")
                        else:
                            bad(f"检索返回空：租户 {args.tenant} 下无已发布公开智能体")
                            problems.append("检索无结果（未回填 / 租户不匹配 / ACL 不匹配）")
                except Exception as e:
                    bad(f"embedding/检索异常：{e}")
                    problems.append(f"embedding/检索调用失败：{e}")

    # 结论
    print("\n" + "=" * 60)
    if problems:
        print("发现问题：")
        for p in problems:
            print(f"  ❌ {p}")
    else:
        print("✅ 各项检查通过。若仍不推荐，多半是模型没遵守 [[RECOMMEND:...]] 指令，")
        print("   或该 query 语义上确实没有合适智能体（属正常）。")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="诊断对话内推荐链路")
    parser.add_argument("--query", type=str, default=None, help="实测检索的 query")
    parser.add_argument(
        "--tenant", type=str, default="0",
        help="按哪个租户检索（默认 0=只看全局应用；查某租户用户的真实召回请传其 tenant_id）",
    )
    args = parser.parse_args()
    asyncio.run(main(args))
