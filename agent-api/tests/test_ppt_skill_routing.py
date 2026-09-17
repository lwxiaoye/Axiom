from app.services.skills.ppt_policy import (
    effective_ppt_skill_ids,
    find_ppt_skill_id,
    is_ppt_artifact_request,
)


RECORDS = [
    {"skillId": "random-ppt-id", "name": "pptx", "description": "SVG 转 PPTX", "enabled": 1},
    {"skillId": "frontend", "name": "前端设计", "description": "网页", "enabled": 1},
]


def test_detects_ppt_creation_and_editing() -> None:
    assert is_ppt_artifact_request("帮我生成一份产品介绍 PPT")
    assert is_ppt_artifact_request("把这份演示文稿美化一下")
    assert is_ppt_artifact_request("把这个文件优化得更高级", ["季度汇报.pptx"])


def test_does_not_route_ppt_questions_or_unrelated_artifacts() -> None:
    assert not is_ppt_artifact_request("为什么我们的 PPT 看起来不高级？")
    assert not is_ppt_artifact_request("帮我制作一个 HTML 仪表盘")
    assert not is_ppt_artifact_request("这份ppt做的怎么样", ["库里生涯讲解.pptx"])
    assert not is_ppt_artifact_request("帮我看看这份课件质量如何", ["物理.pptx"])
    from app.services.skills.ppt_policy import is_document_review_request
    assert is_document_review_request("这份ppt做的怎么样", ["库里生涯讲解.pptx"])
    assert is_ppt_artifact_request("把这个文件优化得更高级", ["季度汇报.pptx"])


def test_finds_randomized_platform_ppt_skill_id() -> None:
    assert find_ppt_skill_id(RECORDS) == "random-ppt-id"


def test_preparation_does_not_auto_add_a_ppt_skill() -> None:
    ids = effective_ppt_skill_ids("制作一份融资路演 PPT", ["web-search"], RECORDS)
    assert ids == ["web-search"]


def test_non_ppt_request_keeps_explicit_selection_only() -> None:
    assert effective_ppt_skill_ids("总结一下这篇文章", ["writer"], RECORDS) == ["writer"]


# 2026-07-24 bug：目录里有两个 PPT 技能，用户 @ 选了 A，却被自动追加评分更高的 B（模型改用 B）。
TWO_PPT_RECORDS = [
    {"skillId": "ppt-a", "name": "Powerpoint / PPTX",
     "description": "Create, inspect, and edit Microsoft PowerPoint presentations and PPTX decks",
     "enabled": 1},
    {"skillId": "ppt-b", "name": "ppt skill",
     "description": "用 HTML 设计系统逐页生成演示文稿，一键编译成可编辑的原生 PPTX，支持自由 SVG",
     "enabled": 1},
    {"skillId": "frontend", "name": "前端设计", "description": "网页", "enabled": 1},
]


def test_explicit_ppt_skill_selection_not_overridden_by_platform_default() -> None:
    """用户显式选了某个 PPT 技能，就完全尊重——即便平台默认打分更高的是另一个 PPT 技能，
    也绝不追加它把用户的选择顶掉（否则两个 SKILL.md 同时加载、模型会用错那个）。"""
    # 选 A（评分较低那个）→ 只保留 A，不追加 B
    assert effective_ppt_skill_ids("制作一份融资路演 PPT", ["ppt-a"], TWO_PPT_RECORDS) == ["ppt-a"]
    # 选 B → 只保留 B
    assert effective_ppt_skill_ids("做个 PPT", ["ppt-b"], TWO_PPT_RECORDS) == ["ppt-b"]
    # 用户没选 PPT 技能时，也不由 preparation 静默补平台默认；模型可通过 use_skill 发现并选择。
    auto = effective_ppt_skill_ids("做个 PPT", ["frontend"], TWO_PPT_RECORDS)
    assert auto == ["frontend"]


def test_explicit_custom_named_ppt_skill_not_overridden() -> None:
    """守卫必须宽松识别 PPT 技能：用户选了一个名字里不含 ppt/powerpoint、但描述明确是做幻灯片的
    自定义技能，也算「用户已自己选了 PPT 技能」，不能再追加平台默认把它顶掉。
    （2026-07-24：narrow scorer 只认特定名字，自定义命名会漏识别 → 追加平台默认 → 模型用错技能。）"""
    custom = [
        {"skillId": "deck-master", "name": "演示文稿大师",
         "description": "逐页设计幻灯片并导出可编辑文件", "enabled": 1},
        {"skillId": "ppt-b", "name": "ppt skill",
         "description": "用 HTML 设计系统逐页生成演示文稿，编译成原生 pptx", "enabled": 1},
    ]
    assert effective_ppt_skill_ids("做一份融资 PPT", ["deck-master"], custom) == ["deck-master"]




# ---------- 模块边界：只做路由，不知道技能内部怎么实现（2026-07-27 用户拍板） ----------

def test_ppt_policy_knows_nothing_about_skill_internals() -> None:
    """「主对话只负责在沙箱中跑 skill，不要在源代码做关于 skill 的内容」。

    这里原先有个 `ppt_pipeline_violation()`，靠正则匹配 `skills/*/svg_to_pptx.py`、
    `build_deck.py` 判定"有没有走技能管线"——那是平台自研 ppt-html 的内部文件名。
    用户换成第三方技能（ppt-studio）后，这套判据既拦不住该拦的、又会误判照 SKILL.md
    照做的代码，而写死这些名字的提示词更是在命令模型去跑不存在的脚本。

    这条断言守的是**边界本身**：路由模块里不许再出现任何具体技能的内部文件名。
    """
    import inspect

    from app.services.skills import ppt_policy

    src = inspect.getsource(ppt_policy)
    # 注释/文档里可以解释历史，但**可执行代码**里不许再出现这些名字
    code_lines = [
        ln for ln in src.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    code = "\n".join(code_lines)
    # 掐掉模块 docstring（它解释了为什么删，必然提到这些名字）
    if '"""' in code:
        first = code.index('"""')
        second = code.index('"""', first + 3)
        code = code[:first] + code[second + 3:]
    for internal in ("svg_to_pptx", "html_to_pptx", "build_deck", "TEMPLATES.md",
                     "spec_lock", "svg_output"):
        assert internal not in code, (
            f"{internal!r} 是某个具体技能的内部实现，不该出现在平台路由模块里"
            "——换技能就变成假指令/误判据"
        )


def test_ppt_policy_still_routes() -> None:
    """删门禁不能连路由一起删掉：意图识别 + 技能选取仍是平台的职责。"""
    from app.services.skills.ppt_policy import is_ppt_artifact_request

    assert is_ppt_artifact_request("帮我做一份节能减排的 PPT") is True
    assert is_ppt_artifact_request("什么是 PPT") is False
    assert is_ppt_artifact_request("这份 ppt 做的怎么样") is False


# ---- 模型自主发现与显式选择边界（2026-08-19）----
# find_ppt_skill_id 仍服务于 use_skill 的候选解析和历史兼容调用；
# effective_ppt_skill_ids 不再把它当成 preparation 阶段的自动路由器。
_STUDIO = {"skillId": "ppt-studio", "name": "ppt studio",
           "description": "用 HTML 设计系统逐页生成演示文稿", "enabled": 1}
_HTML = {"skillId": "ppt-html", "name": "ppt-html",
         "description": "PPT 演示文稿生成", "enabled": 1}
_OTHER = {"skillId": "ppt-x", "name": "ppt-x",
          "description": "PPT 演示文稿生成", "enabled": 1}


def test_ppt_studio_wins_deterministically_against_any_other_ppt_skill() -> None:
    """候选解析仍保持确定性，但 preparation 不会静默加载候选。"""
    for rival in (_HTML, _OTHER, RECORDS[0]):
        assert find_ppt_skill_id([_STUDIO, rival]) == "ppt-studio"
        assert find_ppt_skill_id([rival, _STUDIO]) == "ppt-studio"
        assert effective_ppt_skill_ids("帮我做一份PPT", [], [_STUDIO, rival]) == []


def test_tied_ppt_skills_remain_candidates_but_are_not_loaded() -> None:
    """同分候选仍可供 use_skill 解析；preparation 不替模型做选择。"""
    picked = find_ppt_skill_id([_HTML, _OTHER])
    assert picked in {"ppt-html", "ppt-x"}, "同分时不能返回 None"
    assert effective_ppt_skill_ids("帮我做一份PPT", [], [_HTML, _OTHER]) == []


def test_tie_break_is_stable_across_input_order() -> None:
    """确定性：同一组技能不管以什么顺序传进来，选中的必须是同一个。"""
    assert find_ppt_skill_id([_HTML, _OTHER]) == find_ppt_skill_id([_OTHER, _HTML])


def test_user_explicit_choice_still_beats_the_platform_default() -> None:
    """用户自己选了 PPT 技能就完全尊重——ppt studio 的高分不该顶掉显式选择。"""
    assert effective_ppt_skill_ids("帮我做一份PPT", ["ppt-html"], [_STUDIO, _HTML]) == ["ppt-html"]


def test_fuzzy_lookup_keeps_ambiguity_visible_to_the_model() -> None:
    """两个场景的正确降级方向是相反的，别让路由的修复漏进模糊匹配。

    preparation 不做自动路由；use_skill 模糊匹配（tie_breaks=False）：模型自己拿含糊名来找，返回 None 让调用方
    把候选列给它二选一——不存在静默失败，替它选反倒剥夺了它的判断。
    """
    assert find_ppt_skill_id([_HTML, _OTHER], tie_breaks=False) is None
    assert find_ppt_skill_id([_HTML, _OTHER], tie_breaks=True) is not None
    # 不同分时两种模式一致：有明确赢家就没有歧义可言
    assert find_ppt_skill_id([_STUDIO, _HTML], tie_breaks=False) == "ppt-studio"
