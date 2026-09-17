"""Harness acceptance, process scrub, and product budget guards."""
from pathlib import Path

from app.services.chat.turn_finalizer import scrub_process_narration_when_delivered
from app.services.agent_harness.orchestrator import _goal_next_action_text
from app.services.agent_harness.model_driver import _goal_forbids_research


def _src(rel: str) -> str:
    return Path(__file__).resolve().parents[1].joinpath(rel).read_text(encoding="utf-8")


def test_precreated_run_skips_duplicate_next_action():
    src = _src("app/services/agent_harness/orchestrator.py")
    assert "if not precreated_run:" in src
    assert "_goal_next_action_text" in src


def test_goal_next_action_word_is_empty_model_driven():
    text = _goal_next_action_text(
        "用Word写一份很短的三要点简报，直接交付，不要调研",
        pure_qa=False,
    )
    assert text == ""


def test_goal_forbids_research_structural():
    assert _goal_forbids_research("用Word写很短三要点简报，直接交付，不要调研")
    assert _goal_forbids_research("直接生成文档，不用搜索")
    assert _goal_forbids_research("写一份简报，不要做多余调研，最多搜一次就写")
    assert not _goal_forbids_research("查一下今天漳州天气")
    assert not _goal_forbids_research("写一份需要最新数据的行业报告")


def test_product_no_research_guard_present():
    src = _src("app/services/agent_harness/model_driver.py")
    assert "product_block_search_no_research" in src
    assert "_goal_forbids_research" in src


def test_scrub_process_narration_when_delivered():
    raw = (
        "首次搜索未返回内容，换更短关键词再试一次。"
        "检索未返回结果（网络检索无内容）。按用户要求不换词空转，改为基于既有行业认知整理简报，并在文中明确标注未经实时核验。现在直接生成 docx。"
        "已交付：Agent市场三要点简报.docx（位于「我的文件」）。\n\n"
        "文档为简短三要点简报，包含市场规模。"
    )
    out = scrub_process_narration_when_delivered(raw, tools_delivered=True)
    assert "已交付" in out
    assert "首次搜索" not in out
    assert "现在直接生成" not in out
    # without deliverable flag, leave raw
    keep = scrub_process_narration_when_delivered(raw, tools_delivered=False)
    assert "首次搜索" in keep
