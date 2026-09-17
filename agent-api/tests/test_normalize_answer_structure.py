"""终答结构整形：粘连标题/列表拆行，且不误伤词内「要点」。"""
from __future__ import annotations

import re

from app.services.chat.turn_finalizer import (
    normalize_answer_structure,
    scrub_contradictory_completion,
)


def test_does_not_split_compound_要点_before_period():
    """「可验收要点。」不得拆成「要点」+ 孤行「。」。"""
    raw = "4. 七步实施：每步含可验收要点。\n5. 避坑重点：门槛。"
    out = normalize_answer_structure(raw)
    assert "可验收要点。" in out
    assert not re.search(r"(?m)^\s*。\s*$", out)
    assert "5. 避坑重点" in out


def test_section_title_after_sentence_breaks_before_list():
    raw = "检索质量是成败关键。要点\n1. 架构六层：评估。\n2. 技术选型：轻量。"
    out = normalize_answer_structure(raw)
    assert "关键。" in out
    assert re.search(r"关键。\s*\n\s*\n要点\s*\n", out)
    assert "1. 架构六层" in out
    assert "2. 技术选型" in out


def test_stuck_要点1_splits_title_and_list():
    out = normalize_answer_structure("关键。要点1. 架构六层")
    assert "要点" in out
    assert "1. 架构六层" in out
    assert "要点1." not in out.replace(" ", "")


def test_说明_after_period_becomes_section():
    out = normalize_answer_structure("建议先 POC 后全量。说明报告含 3 张对比表。")
    assert re.search(r"全量。\s*\n\s*\n说明\s*\n", out)
    assert "报告含 3 张" in out


def test_inline_numbered_list_after_period():
    out = normalize_answer_structure("评估完成。2. 技术选型：轻量。3. 国内外产品。")
    assert "\n2. 技术选型" in out
    assert "\n3. 国内外产品" in out


def test_plain_guidance_sections_and_inline_bullets_are_restored():
    """真机截图：标题无 Markdown、列表压进句末后不能退化为一堵文字。"""
    raw = (
        "前两周掉秤快主要是排水，不是脂肪。核心原理热量缺口是一切减重方法共同的作用机制。"
        "饮食策略蛋白质按每公斤体重约 1.6 克安排。- 主食优先粗粮，保留适量碳水。"
        "- 用餐盘法则简化执行。运动策略有氧和力量结合效果最持久。"
        "常见误区极端节食短期掉秤最快，但最容易反弹。说明需结合个人情况调整。"
    )
    out = normalize_answer_structure(raw)
    for title in ("核心原理", "饮食策略", "运动策略", "常见误区", "说明"):
        assert re.search(rf"(?m)^{title}$", out), title
    assert re.search(r"(?m)^- 主食优先粗粮", out)
    assert re.search(r"(?m)^- 用餐盘法则", out)


def test_does_not_false_split_mid_sentence_要点():
    raw = "本段讨论了三个要点和相关说明，不必拆开。"
    out = normalize_answer_structure(raw)
    assert out == raw


def test_strips_hash_headings_outside_fences():
    raw = "已保存到「我的文件」。## 结论\n内容好。## 要点1. 一条"
    out = normalize_answer_structure(raw)
    assert "##" not in out
    assert "结论" in out
    assert "1. 一条" in out


def test_preserves_code_fence_hash():
    raw = "见代码：\n```\n# 结论\n1. a\n```\n外面要点1. b"
    out = normalize_answer_structure(raw)
    assert "```\n# 结论\n1. a\n```" in out
    assert "要点" in out
    assert "1. b" in out


def test_full_research_summary_shape():
    raw = (
        "知识库建设的最优路径是成败关键。要点\n"
        "1. 架构六层：评估。\n"
        "2. 技术选型：轻量。\n"
        "3. 国内外产品：均已纳入对比表。\n"
        "4. 七步实施：每步含可验收要点。\n"
        "5. 避坑重点：门槛。\n"
        "6. 中台适配：建议先 POC 后全量。说明\n"
        "报告含 3 张对比表。需要我转 PPT，随时说。"
    )
    out = normalize_answer_structure(raw)
    assert not re.search(r"(?m)^\s*。\s*$", out)
    assert "可验收要点。" in out
    assert re.search(r"\n要点\n", out)
    assert re.search(r"\n说明\n", out)
    for n in range(1, 7):
        assert f"{n}." in out


def test_capability_wall_text_splits_sections_and_bullets():
    """「你可以干什么」横铺：分类标题 + 行内 - 列表必须拆开。"""
    raw = (
        "我是你的智能助手，主要能做这些事：信息与调研- 联网搜索实时信息：新闻、天气、"
        "政策、产品动态等- 按需整理成简报、要点清单或对比分析文档与办公- 生成和编辑 "
        "Word、Excel、PPT、PDF 等文件- 做演示文稿（可交付可编辑的 PPTX）、周报、表格看板、"
        "海报开发与自动化- 写代码、调试脚本、处理数据- 做可运行的网页、小游戏、交互组件等"
        "网页浏览- 读取指定网页内容、操作页面（翻页、点击、填写等）技能扩展- 平台内置 PPT "
        "制作、周报生成、市场研究、营销策略、前端设计等专业技能，按需启用你有什么任务直接说"
        "就行，比如「查一下最近的 AI 政策」「把这份数据做成 PPT」「帮我写一个计算器页面」"
        "——我来动手做。"
    )
    out = normalize_answer_structure(raw)
    assert "主要能做这些事：" in out
    for title in ("信息与调研", "文档与办公", "开发与自动化", "网页浏览", "技能扩展"):
        assert re.search(rf"(?m)^{title}\s*$", out), title
    assert re.search(r"(?m)^- 联网搜索", out)
    assert re.search(r"(?m)^- 生成和编辑", out) or "生成和编辑" in out
    assert "你有什么任务" in out
    # 不再是零换行的一整团
    assert out.count("\n") >= 8
    assert "10-20" not in raw  # 样例无连字符数字；另测不误伤
    assert normalize_answer_structure("温度约 10-20℃，属于正常区间。") == (
        "温度约 10-20℃，属于正常区间。"
    )


def test_inline_named_markdown_bullets_from_real_message_are_restored():
    """真机原文：`- **分类**：` 横铺时必须在入库边界恢复为真列表。"""
    raw = (
        "我可以帮你完成各类实际任务，比如：- **写文档做表格**：生成 Word、Excel、PPT，"
        "整理资料- **查信息**：搜索最新资讯，附上来源- **做网页和小工具**：生成可运行文件"
        "- **处理文件**：读取、编辑和 OCR- **设计研究**：市场分析这类工作"
        "你直接把想要的结果告诉我。"
    )
    out = normalize_answer_structure(raw)
    assert "比如：\n\n- **写文档做表格**：" in out
    for title in ("查信息", "做网页和小工具", "处理文件", "设计研究"):
        assert re.search(rf"(?m)^- \*\*{title}\*\*：", out)
    assert "\n\n你直接把想要的结果告诉我" in out


def test_inline_named_bullet_repair_does_not_touch_single_dash_or_ranges():
    raw = "温度为 -1℃，年份 2025-2026，可选 - **A 方案**：保守。"
    assert normalize_answer_structure(raw) == raw


def test_custom_section_title_stuck_to_list_1():
    """自定义小节标题 + 1. 列表粘连（调研墙文真机口径）。"""
    raw = (
        "用户评价呈现「能坐、不耐久」的整体画像。"
        "市场概况1. 大盘数据：规模约百亿。"
        "2. 低价段结构性特征：工学功能缺失。"
        "3. 竞争格局：品牌集中。"
        "品牌与对应口碑1. 西昊——声量最大。"
        "2. 黑白调——性价比。"
        "用户评价总结1. 好评集中点：门槛低。"
        "2. 差评集中点：护腰有限。"
    )
    out = normalize_answer_structure(raw)
    for title in ("市场概况", "品牌与对应口碑", "用户评价总结"):
        assert re.search(rf"(?m)^{title}\s*$", out), title
    assert re.search(r"(?m)^1\. 大盘数据", out)
    assert re.search(r"(?m)^1\. 西昊", out)
    assert re.search(r"(?m)^1\. 好评集中点", out)
    assert re.search(r"(?m)^2\. 低价段", out)
    assert "市场概况1." not in out.replace(" ", "")
    assert "口碑1." not in out.replace(" ", "")
    assert "总结1." not in out.replace(" ", "")


def test_结论_stuck_to_digit_body():
    """「。结论200元…」不得整段粘死（旧 lookahead 排除数字会漏）。"""
    raw = "以下是完整调研结果。结论200元以内价位段是真空区。"
    out = normalize_answer_structure(raw)
    assert re.search(r"结果。\s*\n\s*\n结论\s*\n", out)
    assert "200元以内" in out
    # 词内「先给结论：」不拆
    assert normalize_answer_structure("先给结论：200元以内买不到。") == (
        "先给结论：200元以内买不到。"
    )


def test_transition_phrase_not_promoted_as_section_title():
    """句末后过渡语 + 列表：不要把「这是因为」抬成节标题。"""
    out = normalize_answer_structure("评估完成。这是因为\n1. 架构清晰。")
    assert "这是因为" in out
    # 过渡语应留在句内/段内，不能独占成节标题行（两侧空行包起来那种）
    assert not re.search(r"(?m)^\s*这是因为\s*$", out)
    assert re.search(r"(?m)^1\. 架构清晰", out)


def test_research_wall_text_full_shape():
    """完整调研墙文：结论/自定义小节/编号列表都要拆开，不再挤成一团。"""
    raw = (
        "调研完成。先给结论：200元以内基本买不到严格意义的「人体工学椅」。"
        "以下是完整调研结果。结论200元以内价位段是人体工学椅的「真空区」。"
        "因此该预算下市场主要由白牌走量款占据，用户评价呈现「能坐、不耐久」的整体画像。"
        "市场概况1. 大盘数据：2024年规模约百亿 [24]。"
        "2. 低价段结构性特征：工学功能基本缺失 [17]。"
        "3. 竞争格局：品牌集中在西昊、黑白调 [77]。"
        "品牌与对应口碑1. 西昊——低价档第一梯队 [75]。"
        "2. 黑白调——性价比 [60]。"
        "用户评价总结1. 好评集中点：门槛低 [8]。"
        "2. 差评集中点：护腰有限 [17]。"
        "说明本次检索以测评文为主。若核心诉求是护腰，建议上 300–500 元档。"
    )
    out = normalize_answer_structure(raw)
    assert re.search(r"(?m)^结论\s*$", out)
    assert re.search(r"(?m)^市场概况\s*$", out)
    assert re.search(r"(?m)^品牌与对应口碑\s*$", out)
    assert re.search(r"(?m)^用户评价总结\s*$", out)
    assert re.search(r"(?m)^说明\s*$", out)
    assert re.search(r"(?m)^1\. 大盘数据", out)
    assert re.search(r"(?m)^1\. 西昊", out)
    assert out.count("\n") >= 12
    # 仍不误伤词内结论 / 小数
    assert "先给结论：200元" in out
    assert normalize_answer_structure("共约2.3亿元规模。") == "共约2.3亿元规模。"


def test_structure_markers_colon_paren_circled_fullwidth():
    """结构标记族：冒号小节 / 括号编号 / 圆圈数字 / 全角数字 / 无空格列表。"""
    colon = normalize_answer_structure(
        "画像。市场概况：规模约百亿。竞争格局：西昊领先。"
    )
    assert re.search(r"(?m)^市场概况\s*$", colon)
    assert re.search(r"(?m)^竞争格局\s*$", colon)
    assert "规模约百亿" in colon

    paren = normalize_answer_structure("结论如下：（1）规模大（2）品牌少（3）耐久差。")
    assert re.search(r"(?m)^1\. 规模大", paren)
    assert re.search(r"(?m)^2\. 品牌少", paren)
    assert re.search(r"(?m)^3\. 耐久差", paren)

    circled = normalize_answer_structure("要点：①规模大②品牌少③耐久差。")
    assert re.search(r"(?m)^1\. 规模大", circled)
    assert re.search(r"(?m)^2\. 品牌少", circled)

    fullwidth = normalize_answer_structure("完成。１．大盘数据。２．结构特征。")
    assert re.search(r"(?m)^1\. 大盘数据", fullwidth)
    assert re.search(r"(?m)^2\. 结构特征", fullwidth)

    no_space = normalize_answer_structure("完成。1.大盘2.结构3.品牌")
    assert re.search(r"(?m)^1\. 大盘", no_space)
    assert re.search(r"(?m)^2\. 结构", no_space)
    assert re.search(r"(?m)^3\. 品牌", no_space)

    half_paren = normalize_answer_structure("如下1）先搜2）再比3）下单")
    assert re.search(r"(?m)^1\. 先搜", half_paren)
    assert re.search(r"(?m)^2\. 再比", half_paren)


def test_custom_colon_does_not_split_先给结论():
    raw = "调研完成。先给结论：200元以内买不到。"
    assert normalize_answer_structure(raw) == raw


def test_title_after_comma_and_long_suffix_title():
    comma = normalize_answer_structure("能坐、不耐久，市场概况1. 大盘数据。")
    assert re.search(r"(?m)^市场概况\s*$", comma)
    assert re.search(r"(?m)^1\. 大盘数据", comma)

    long = normalize_answer_structure(
        "完成。低价人体工学椅市场与用户口碑综合分析1. 大盘。"
    )
    assert re.search(r"(?m)^低价人体工学椅市场与用户口碑综合分析\s*$", long)
    assert re.search(r"(?m)^1\. 大盘", long)


def test_preserves_em_dash_inside_list_item():
    """列表项里的「西昊——第一」不能被当成 bullet 横线拆开。"""
    out = normalize_answer_structure("1. 西昊——第一梯队，声量最大。")
    assert "西昊——第一梯队" in out
    assert "\n- 第一" not in out


def test_bold_research_sections_and_flattened_table_are_restored():
    """真机水杯调研：加粗小节、压扁表格与具名子主题不能再挤成一段。"""
    raw = (
        "调研完成，结论如下。**结论摘要**大众愿意为安全与实用买单。消费者也看重便携。"
        "**市场规模：盘子大、在升级**市场规模持续增长。行业转向价值升级。"
        "**功能需求分三层（核心发现）**| 层级 | 功能 | 证据 ||---|---|---|"
        "| 底线刚需 | 材质安全 | 调查 [1] || 体验加分 | 清洗便捷 | 调查 [2] |"
        "**溢价功能的三个突破口**健康管理（智能水杯）：提醒饮水。"
        "高端材质（钛杯）：强调安全。场景化功能：匹配户外。"
        "**给大众产品设计的落地建议**先保证四项标配，再做差异化。"
        "**局限说明**样本口径不一，仅供参考。"
    )
    out = normalize_answer_structure(raw)

    for title in (
        "结论摘要", "市场规模：盘子大、在升级", "功能需求分三层（核心发现）", "局限说明",
    ):
        assert f"**{title}**\n\n" in out
    assert "| 层级 | 功能 | 证据 |\n|---|---|---|" in out
    assert "|底线刚需 | 材质安全 | 调查 [1] |\n|体验加分" in out
    assert "\n\n**高端材质（钛杯）**：强调安全。" in out
    assert "\n\n**场景化功能**：匹配户外。" in out


def test_short_inline_bold_emphasis_is_not_promoted():
    raw = "推荐选择 **材质安全** 的款式，不必追求功能堆叠。"
    assert normalize_answer_structure(raw) == raw


def test_well_spaced_streamed_report_is_preserved_byte_for_byte():
    """灰色阶段已经排好的正文，完成态不得重新整形或误拆 16:8。"""
    raw = (
        "**结论**\n\n温和热量缺口最可持续。\n\n"
        "**核心机制**\n\n每日摄入低于消耗。\n\n"
        "**证据与局限**\n\n轻断食（16:8）证据有限，不能替代长期习惯。"
    )
    assert normalize_answer_structure(raw) == raw


def test_completion_scrub_keeps_markdown_line_breaks_without_meta_claim():
    raw = (
        "**结论**\n\n第一段。\n\n"
        "**饮食怎么改**\n\n第二段。\n\n"
        "**证据与局限**\n\n仍有未完成的长期研究，但本轮结论可用。"
    )
    assert scrub_contradictory_completion(raw) == raw


def test_hash_any_heading_not_only_fixed_titles():
    raw = "已保存到「我的文件」。## 市场概况\n内容A。## 采购建议\n内容B。"
    out = normalize_answer_structure(raw)
    assert "##" not in out
    assert re.search(r"(?m)^市场概况\s*$", out) or "市场概况" in out
    assert "采购建议" in out
    assert "内容A" in out
