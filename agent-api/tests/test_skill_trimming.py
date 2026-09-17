"""v3.0 Skill 上下文裁剪：frontmatter 剥离、多技能预算分摊、节选标注。"""

from app.services.chat import turn_context_builder as tcb


def test_strip_frontmatter():
    md = (
        "---\nname: 写报告\nallowed-tools: bash\n---\n\n# 能力\n按模板写报告\n"
    )
    assert tcb._strip_skill_frontmatter(md).startswith("# 能力")
    assert "allowed-tools" not in tcb._strip_skill_frontmatter(md)


def test_strip_frontmatter_no_frontmatter():
    md = "# 能力\n直接开始\n"
    assert tcb._strip_skill_frontmatter(md) == md.strip()


def test_strip_frontmatter_malformed():
    md = "---\nname: 没闭合\n正文\n"
    assert "正文" in tcb._strip_skill_frontmatter(md)


def _content(s: dict) -> str:
    """剥离节选标记后的纯正文（预算按正文计，标记不计入）。"""
    return str(s.get("instructions") or "").replace("…（其余内容因篇幅省略）", "").strip()


def test_budget_allocation_single():
    skills = [
        {"id": "a", "name": "A", "instructions": "x" * 20000},
        {"id": "b", "name": "B", "instructions": "y" * 3000},
    ]
    out = tcb._apply_skill_instruction_budget(skills)
    total = len(_content(out[0])) + len(_content(out[1]))
    assert total <= tcb._SKILL_INSTRUCTIONS_TOTAL_MAX
    assert out[0]["truncated"] is True
    # 预算耗尽后第二个技能同样被截断（只留节选标记）
    assert out[1]["truncated"] is True
    assert len(_content(out[1])) == 0
    assert "其余内容因篇幅省略" in out[0]["instructions"]


def test_budget_allocation_within_limit():
    skills = [
        {"id": "a", "name": "A", "instructions": "x" * 1000},
        {"id": "b", "name": "B", "instructions": "y" * 1000},
    ]
    out = tcb._apply_skill_instruction_budget(skills)
    assert out[0]["truncated"] is False
    assert out[1]["truncated"] is False
    assert out[0]["instructions"] == "x" * 1000


def test_ppt_skill_receives_bounded_budget_for_complete_upstream_workflow():
    skills = [
        {
            "id": "ppt",
            "name": "ppt-studio",
            "instructions": "x" * 21000,
            "is_ppt_skill": True,
        },
        {"id": "b", "name": "B", "instructions": "y" * 3000},
    ]
    out = tcb._apply_skill_instruction_budget(skills)
    assert out[0]["truncated"] is False
    assert len(_content(out[0])) == 21000
    assert out[1]["truncated"] is False


def test_format_skill_block_truncated_note():
    block = tcb._format_skill_block({
        "id": "s1", "name": "节选技能",
        "description": "d", "instructions": "i", "truncated": True,
    })
    assert "说明为节选（因篇幅省略其余内容）" in block
    assert "按当前 Skill 自己的说明针对性读取对应资源" in block

    full = tcb._format_skill_block({
        "id": "s1", "name": "完整技能",
        "description": "d", "instructions": "i", "truncated": False,
    })
    assert "说明为节选" not in full


def test_trimming_hooked_into_fetch():
    """frontmatter 剥离与预算分摊必须接入 _fetch_trusted_skills。"""
    src = open("app/services/chat/turn_context_builder.py", encoding="utf-8").read()
    assert "_strip_skill_frontmatter(readme)" in src
    assert "_apply_skill_instruction_budget(trusted)" in src
    assert "_SKILL_INSTRUCTIONS_TOTAL_MAX" in src
