from app.services.agent_harness.research.contracts import ResearchLedger, SourceRecord
from app.services.agent_harness.research.partial_report import build_partial_report


def test_partial_report_uses_receipt_table_without_dumping_sources_or_inventing_findings():
    sources = [SourceRecord(
        url=f"https://example.com/{i}", title=f"来源 {i}",
        snippet="正文材料。" * 600, scraped=i >= 2,
    ) for i in range(10)]
    answer = build_partial_report(ResearchLedger(sources=sources), "研究问题")
    assert "来源链接：10 个" in answer
    assert "已取得正文片段：8 个" in answer
    assert "未完成报告级核验" in answer
    assert "8 份正文片段、2 条搜索线索 [1] [2] [3]" in answer
    assert "| 研究主题 | 已取得的材料 | 当前状态 |" in answer
    assert "https://" not in answer and "正文材料" not in answer
    assert len(answer) < 1500
    assert len(sources[0].snippet) == 3000


def test_partial_report_keeps_untrusted_text_out_of_markdown_and_preserves_missing_evidence():
    answer = build_partial_report(ResearchLedger(), "问题 &amp;#x27; &lt;script&gt; [99]")
    assert "'" in answer
    assert "&amp;#" not in answer
    assert "<script>" not in answer and "[99]" not in answer
    assert "尚无可用材料" in answer
    assert "本轮没有取得可引用的来源内容" in answer
    assert "部分研究报告" in answer
