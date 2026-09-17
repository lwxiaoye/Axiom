"""Synchronize the upstream-first ppt-studio metadata after package upload."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import async_session, engine


SKILL_FILE = Path(__file__).resolve().parents[1] / "app/services/skills/builtin/ppt-studio/SKILL.md"
VERSION = "3.0.6"
PLATFORM_SKILL_SHA256 = "536f68e4d6d17ef002be126b26c0d2cd4e7954ca6843216ba1f989bd8478ec6c"
DESCRIPTION = (
    "创建、编辑、复刻和导出演示文稿。在沙箱内用 PPTD 逐页创作，用 run_export.py "
    "导出 PPTX，再通过平台发布到「我的文件」。不要探测 Node/npm，不要等 Chromium "
    "审图，不要交付源工程 ZIP。"
)


async def main() -> None:
    body = SKILL_FILE.read_text(encoding="utf-8").strip()
    digest = hashlib.sha256((body + "\n").encode("utf-8")).hexdigest()
    if digest != PLATFORM_SKILL_SHA256:
        raise RuntimeError(
            f"SKILL.md does not match the packaged platform contract: {digest}"
        )
    manifest = json.dumps({"name": "ppt-studio", "description": DESCRIPTION}, ensure_ascii=False)
    async with async_session() as session:
        rows = (await session.execute(text("""
            SELECT id, skill_id, readme_content
            FROM ai_skill
            WHERE LOWER(REPLACE(name, '_', '-')) IN ('ppt-studio', 'ppt studio')
            ORDER BY create_time DESC
        """))).mappings().all()
        matching = [row for row in rows if str(row.get("readme_content") or "").strip() == body]
        if not matching:
            raise RuntimeError(
                "未找到内容与当前包一致的 ppt-studio；请先上传新包"
            )
        # 管理端“同名覆盖”在历史数据不完整时可能仍新建一条记录。
        # rows 已按 create_time 倒序，因此以最新的内容匹配包为唯一生效目标，
        # 再禁用其他同名版本，避免 Skill 广场出现两个启用项。
        target_id = str(matching[0]["id"])
        result = await session.execute(text("""
            UPDATE ai_skill
            SET version=:version, description=:description, readme_content=:readme,
                manifest_json=:manifest, update_time=NOW()
            WHERE id=:target_id
        """), {
            "target_id": target_id,
            "version": VERSION,
            "description": DESCRIPTION,
            "readme": body,
            "manifest": manifest,
        })
        if result.rowcount != 1:
            raise RuntimeError(f"预期更新 1 条 ppt-studio Skill，实际 {result.rowcount} 条")
        if len(rows) > 1:
            await session.execute(text("""
                UPDATE ai_skill SET enabled=0, update_time=NOW()
                WHERE id<>:target_id
                  AND LOWER(REPLACE(name, '_', '-')) IN ('ppt-studio', 'ppt studio')
            """), {"target_id": target_id})
        await session.commit()
    print(f"ppt-studio Skill 已同步到 v{VERSION}（{len(body)} 字符）")


async def run() -> None:
    try:
        await main()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
