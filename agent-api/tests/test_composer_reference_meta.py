"""用户气泡引用标签：Skill / 知识库 / @智能体 / 网页搜索写入 attachments_json。"""
from app.services.chat.turn_context_builder import (
    composer_reference_meta,
    merge_composer_reference_meta,
)


def test_composer_reference_meta_includes_all_referenced_chips():
    cards = composer_reference_meta(
        selected_skills=[{"id": "s1", "name": "PPT 助手"}, {"name": "PPT 助手"}],
        selected_knowledge=[{"id": "k1", "name": "制度库"}],
        subagent_name="面试助手",
        web_search=True,
    )
    assert [(c["kind"], c["filename"]) for c in cards] == [
        ("skill", "PPT 助手"),
        ("knowledge", "制度库"),
        ("subagent", "面试助手"),
        ("web", "网页搜索"),
    ]
    assert cards[0]["reference_id"] == "s1"


def test_merge_keeps_file_cards_and_appends_missing_chips():
    attachments = [
        {"filename": "简历.pdf", "kind": "pdf", "status": "ok", "file_id": "f1"},
        {"filename": "PPT 助手", "kind": "skill"},
    ]
    merged = merge_composer_reference_meta(
        attachments,
        selected_skills=[{"id": "s1", "name": "PPT 助手"}],
        selected_knowledge=[{"name": "制度库"}],
        subagent_name="面试助手",
        web_search=False,
    )
    kinds = [(item["kind"], item["filename"]) for item in merged]
    assert kinds[0] == ("pdf", "简历.pdf")
    assert ("skill", "PPT 助手") in kinds
    assert kinds.count(("skill", "PPT 助手")) == 1
    assert next(item for item in merged if item["kind"] == "skill")["reference_id"] == "s1"
    assert ("knowledge", "制度库") in kinds
    assert ("subagent", "面试助手") in kinds
    assert ("web", "网页搜索") not in kinds


def test_empty_names_are_dropped():
    assert composer_reference_meta(
        selected_skills=[{"name": "  "}],
        selected_knowledge=[{}],
        subagent_name="",
        web_search=False,
    ) == []
