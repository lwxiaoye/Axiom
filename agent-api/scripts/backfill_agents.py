"""手动回填 app_info 智能体到向量库（一次性 / 修复用）。

核心逻辑在 app/services/backfill_service.py，与「启动自动回填」共用同一份代码。
日常增量同步由 Java 回调 /internal/agents/bulk-sync 完成；本脚本用于首次部署、
迁移服务器、或 Qdrant 卷被重建后补齐存量向量。

role_ids/dept_ids 一律留空 → 智能体按「公开」入库（所有用户可检索）。

运行（agent-api 容器内）：
    docker compose -f agent-api/docker-compose.yml exec agent-api \\
        python scripts/backfill_agents.py --dry-run
    docker compose -f agent-api/docker-compose.yml exec agent-api \\
        python scripts/backfill_agents.py

参数：
    --dry-run            只打印将要同步的智能体，不写向量库
    --include-disabled   连同 status!=1 的一起送（默认只送启用的）
    --batch N            每批条数（默认 200，上限 200）
    --limit N            最多处理 N 条（调试用）
"""

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.platform.backfill_service import run_backfill  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


async def _main(args: argparse.Namespace) -> None:
    result = await run_backfill(
        include_disabled=args.include_disabled,
        limit=args.limit,
        batch=args.batch,
        dry_run=args.dry_run,
    )
    print("结果：", result)


def main() -> None:
    parser = argparse.ArgumentParser(description="回填 app_info 智能体到向量库")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写入")
    parser.add_argument("--include-disabled", action="store_true", help="包含未启用的智能体")
    parser.add_argument("--batch", type=int, default=200, help="每批条数（<=200）")
    parser.add_argument("--limit", type=int, default=None, help="最多处理 N 条（调试）")
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
