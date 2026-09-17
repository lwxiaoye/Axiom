"""ChatGPT-style continuous research report schema and HTML compiler."""
from app.services.agent_harness.research.contracts import ResearchLedger, SourceRecord
from app.services.agent_harness.research.report import compile_report, strip_research_scaffold


MARKDOWN = """# 折叠屏手机怎么选

## 执行摘要
当前高价位折叠屏已经能日常使用，关键看铰链与售后。[^1]

## 核心发现
- 国内机型维修网络更完整。[^2]
- 海外机型系统更新更稳。

## 建议
先看售后覆盖，再比重量和价格。

| 成熟度 | 核心能力 | 风险 |
| --- | --- | --- |
| 规划 | 可修订计划 | 计划空转 |
"""


def test_compile_report_builds_continuous_article_with_cites_and_tables():
    ledger = ResearchLedger(
        query="折叠屏手机怎么选",
        sources=[
            SourceRecord(url="https://a.example/review", title="评测 A"),
            SourceRecord(url="https://b.example/official", title="官方 B"),
        ],
    )
    compiled = compile_report(answer_markdown=MARKDOWN, ledger=ledger, query="折叠屏手机怎么选")
    doc = compiled.document
    assert doc.cover.title
    assert "执行摘要" in doc.toc
    assert len(doc.chapters) >= 2
    assert doc.references
    html = compiled.html
    assert 'data-kind="research-report"' in html
    assert 'class="research-article"' in html
    assert "research-page research-cover" not in html
    assert 'data-page="toc"' not in html
    assert 'id="research-markdown"' in html
    assert 'class="cite"' in html
    assert "<table>" in html
    assert "<th>" in html
    assert compiled.markdown.startswith("# ")
    assert "评测 A" in compiled.markdown or "https://a.example/review" in compiled.markdown


def test_compile_report_recovers_false_file_scrub_flatten():
    raw = (
        "文件还没有成功写入「我的文件」，目前没有可下载的交付文档。"
        "生成过程可能中断或只完成了中间步骤，本轮未能完成交付。"
        " 已整理的内容要点：所有步骤的搜索证据已完备。现在交付最终研究报告。 --- "
        "# 2026年折叠屏手机选购研究报告 ## 执行摘要 铰链耐用是第一门槛。[1] "
        "## 核心发现 旗舰普遍宣称 40 万次以上开合。"
    )
    assert strip_research_scaffold(raw).startswith("# 2026年折叠屏")
    ledger = ResearchLedger(
        query="折叠屏怎么选",
        sources=[SourceRecord(url="https://a.example/review", title="评测 A")],
    )
    compiled = compile_report(answer_markdown=raw, ledger=ledger, query="折叠屏怎么选")
    assert compiled.document.cover.title.startswith("2026年折叠屏")
    assert "文件还没有成功写入" not in compiled.html
    assert "<h2" in compiled.html
    assert "执行摘要" in compiled.html
    assert "现在交付最终研究报告" not in compiled.document.cover.title


def test_compile_report_strips_synthesis_scaffold_before_title():
    raw = (
        "已掌握足够多角度的独立来源，现在合成完整研究报告。\n"
        "---\n"
        "# 斯蒂芬·库里生涯数据\n\n"
        "## 执行摘要\n库里改变了联盟。[1]\n"
    )
    assert strip_research_scaffold(raw).startswith("# 斯蒂芬·库里")
    ledger = ResearchLedger(query="库里")
    compiled = compile_report(answer_markdown=raw, ledger=ledger, query="库里")
    assert compiled.document.cover.title.startswith("斯蒂芬·库里")
    assert "现在合成完整研究报告" not in compiled.html


def test_compile_report_unflattens_glued_headings_from_live_reports():
    from app.services.agent_harness.research.report import looks_like_research_report

    curry = (
        "已掌握足够多角度的独立来源，现在合成完整研究报告。"
        "---# 斯蒂芬·库里（Stephen Curry）生涯数据深度调研报告"
        "## 执行摘要斯蒂芬·库里改变了联盟。"
    )
    cleaned = strip_research_scaffold(curry)
    assert cleaned.startswith("# 斯蒂芬·库里")
    assert "\n## 执行摘要\n" in cleaned
    assert "现在合成完整研究报告" not in cleaned
    assert looks_like_research_report(curry)
    assert looks_like_research_report(
        "# Kimi K3 能力边界\n\n## 核心发现\nK3 仍处早期评测。\n\n## 参考来源\n- 公开报道"
    )

    kimi = (
        "# Kimi K3 调研报告**月之暗面旗舰大模型的架构、性能、定价与市场影响全景分析**"
        "---## 执行摘要Kimi K3 是月之暗面发布的旗舰大模型。"
    )
    cleaned_kimi = strip_research_scaffold(kimi)
    assert cleaned_kimi.startswith("# Kimi K3 调研报告")
    assert "\n## 执行摘要\n" in cleaned_kimi
    compiled = compile_report(
        answer_markdown=curry,
        ledger=ResearchLedger(query="库里"),
        query="库里",
    )
    assert compiled.document.cover.title.startswith("斯蒂芬·库里")
    assert "执行摘要" in compiled.html


def test_compile_report_unflattens_glued_markdown_tables():
    from app.services.agent_harness.research.report import unflatten_markdown_tables

    glued = "| 成熟度 | 核心能力 | 风险 | | --- | --- | --- | | 规划 | 可修订计划 | 计划空转 |"
    restored = unflatten_markdown_tables(glued)
    assert restored.count("\n") >= 2
    compiled = compile_report(
        answer_markdown="# 库里\n\n## 核心发现\n" + glued,
        ledger=ResearchLedger(query="库里"),
        query="库里",
    )
    assert "<table>" in compiled.html
    assert "<th>" in compiled.html
