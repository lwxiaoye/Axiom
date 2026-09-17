"""深度研究报告编译 E2E（不打模型，证明阶段机 + 报告 schema + Research 内核闭环）。

运行：
  docker exec agent-api python scripts/research_report_compile_e2e.py

覆盖：
1. 查询改写 ≥2 角度；
2. 证据不足时阶段机停在 researching，交叉验证过门后才 synthesizing；
3. 对话 Markdown 编译为连续文稿 HTML（data-kind=research-report，无封面/目录分页）；
4. Research 走 research.kernel，但仍先 seed_goal_contract；Standard/Plan 仍走 main_tool_turn。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/app")

MARKDOWN = """# 折叠屏手机怎么选

## 执行摘要
当前高价位折叠屏已经能日常使用，关键看铰链、重量和售后。[^1]

## 核心发现
- 国内机型维修网络更完整。[^2]
- 海外机型系统更新周期更稳。

## 证据与局限
公开评测样本偏旗舰，中低价位数据不足。

## 建议
先确认本地售后覆盖，再在重量和价格之间取舍。
"""


def main() -> int:
    from app.services.agent_harness.research.contracts import (
        ResearchLedger,
        ResearchStage,
        ResearchTopic,
        SourceRecord,
    )
    from app.services.agent_harness.research.engine import advance_stage
    from app.services.agent_harness.research.plan import rewrite_queries
    from app.services.agent_harness.research.report import compile_report

    variants = rewrite_queries("折叠屏手机怎么选")
    assert len(variants) >= 2, variants
    print(f"✓ query rewrite: {variants}")

    thin = ResearchLedger(
        query="折叠屏手机怎么选",
        topics=[ResearchTopic(topic_id="t1", title="国内评测", queries=variants)],
        search_calls=1,
        sources=[SourceRecord(url="https://a.example/1", topic_id="t1")],
    )
    assert advance_stage(thin).stage is ResearchStage.RESEARCHING
    print("✓ evidence gate holds at researching")

    ready = thin.model_copy(update={
        "search_calls": 3,
        "sources": [
            SourceRecord(url="https://a.example/1", title="评测 A", topic_id="t1"),
            SourceRecord(url="https://b.example/2", title="官方 B", topic_id="t1"),
        ],
    })
    done = advance_stage(ready)
    assert done.stage is ResearchStage.SYNTHESIZING, done.stage
    print("✓ cross-validation advances to synthesizing")

    compiled = compile_report(
        answer_markdown=MARKDOWN, ledger=done, query="折叠屏手机怎么选",
    )
    html = compiled.html
    assert 'data-kind="research-report"' in html
    assert 'class="research-article"' in html
    assert "research-page research-cover" not in html
    assert "执行摘要" in html
    assert 'id="research-markdown"' in html
    print(f"✓ compiled continuous report title={compiled.title!r}")

    orch = Path("/app/app/services/agent_harness/orchestrator.py").read_text(encoding="utf-8")
    assert "seed_goal_contract(" in orch
    assert "run_research_turn" in orch
    assert "async for payload in main_tool_turn.run_agent_turn(env):" in orch
    print("✓ research kernel dispatches after seed_goal_contract; Standard/Plan stay on main_tool_turn")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
