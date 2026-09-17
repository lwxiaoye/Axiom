from app.services.sandbox import visual_review


# 2026-07-28：本文件原有三个断言 `app/services/skills/builtin/pptx/SKILL.md` 内容的测试，
# 已随该文件一并删除——那份 SKILL.md 是死文件（生产代码零引用，只有这里读它），
# 技能内容统一由 Skill 广场的技能包承载，不再放进源码树。
# 下面三个测的是真实生产代码 visual_review，保留。


def test_ppt_visual_review_treats_text_collisions_as_hard_errors() -> None:
    prompt = visual_review._PPT_VISUAL_PROMPT

    assert "一律标 severity=error、passed=false" in prompt
    assert "1700 显示成 170 + 0" in prompt
    assert "时间轴标签压住说明文字" in prompt
    assert "KPI 卡、时间轴、图表数据标签和页脚" in prompt
    assert "不评价构图品味" in prompt
    assert "visual_score" not in prompt
    assert "素材库水印、网站角标、新闻字幕/截图 UI" in prompt
    assert "高级感" in prompt


def test_visual_review_parser_does_not_emit_scores() -> None:
    verdict = visual_review._parse_visual_verdict(
        '{"passed":true,"visual_score":99,"professional_score":99,'
        '"issues":[],"next_actions":[]}'
    )

    assert verdict == {"passed": True, "issues": [], "next_actions": []}


def test_requirement_parser_does_not_emit_coverage_score() -> None:
    verdict = visual_review._parse_requirement_verdict(
        '{"passed":true,"requirement_coverage":100,'
        '"requirement_checks":[],"issues":[],"next_actions":[]}'
    )

    assert verdict == {
        "passed": True,
        "requirement_checks": [],
        "issues": [],
        "next_actions": [],
    }


def test_ppt_contact_sheet_keeps_text_legible() -> None:
    script = visual_review._CONTACT_SHEET_SCRIPT

    assert "cell_w, cell_h, label_h, gap = 960, 540, 36, 18" in script
    assert "quality=88" in script
    assert visual_review._PPT_CONTACT_SHEET_BATCH_SIZE == 2


def test_common_ppt_decks_are_reviewed_page_by_page() -> None:
    assert visual_review._review_page_limit("方案.pptx", 6) == 16
    assert visual_review._sample_pages(12, 16) == list(range(1, 13))
    assert visual_review._review_page_limit("报告.docx", 6) == 6


def test_visual_batch_merge_preserves_objective_errors_without_scores() -> None:
    merged = visual_review._merge_visual_batch_verdicts([
        {
            "passed": True,
            "issues": [],
            "next_actions": [],
        },
        {
            "passed": False,
            "issues": [{"severity": "error", "message": "第 7 页标题溢出"}],
            "next_actions": ["修正第 7 页"],
        },
    ])
    assert merged is not None
    assert merged["passed"] is False
    assert not any(key.endswith("_score") for key in merged)
    assert merged["issues"] == [{"severity": "error", "message": "第 7 页标题溢出"}]


def test_visual_batch_prompt_receives_only_matching_page_outline() -> None:
    outline = """[第 1 页]
封面
[第 2 页]
600 → <100
[第 3 页]
收尾
"""
    selected = visual_review._outline_for_pages(outline, [2])
    assert "600 → <100" in selected
    assert "封面" not in selected
    assert "收尾" not in selected
