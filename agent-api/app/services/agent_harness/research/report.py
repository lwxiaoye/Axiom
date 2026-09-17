"""Compile a Deep Research report into Markdown + ChatGPT-style continuous HTML."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any

from .contracts import (
    CompiledReport,
    ReportChapter,
    ReportCover,
    ReportDocument,
    ResearchLedger,
    SourceRecord,
)


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")
_SCAFFOLD_HEADING_RE = re.compile(r"^#{1,3}\s+\S", re.M)
_FALSE_FILE_SCRUB_RE = re.compile(
    r"^文件还没有成功写入「我的文件」.*?(?:已整理的内容要点：)?",
    re.S,
)
_INLINE_SECTION_RE = re.compile(
    r"^(#{1,3})\s+(执行摘要|核心发现|证据与局限|建议|参考来源)(?:\s+|：)(.+)$"
)
_DEFAULT_SECTIONS = (
    ("exec", "执行摘要"),
    ("findings", "核心发现"),
    ("evidence", "证据与局限"),
    ("advice", "建议"),
)


_RESEARCH_CSS = """
:root { color-scheme: light; }
html, body { margin: 0; padding: 0; background: #fff; color: #0d0d0d; }
body {
  font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "PingFang SC",
    "Segoe UI", "Noto Sans SC", "Helvetica Neue", Arial, sans-serif;
}
.research-article {
  max-width: 720px;
  margin: 0 auto;
  padding: 56px 28px 96px;
  font-size: 16px;
  line-height: 1.75;
  color: #0d0d0d;
}
.research-article h1 {
  font-size: 32px;
  font-weight: 700;
  line-height: 1.25;
  letter-spacing: -0.03em;
  margin: 0 0 28px;
}
.research-article h2 {
  font-size: 22px;
  font-weight: 700;
  line-height: 1.35;
  margin: 40px 0 14px;
}
.research-article h3 {
  font-size: 17px;
  font-weight: 650;
  margin: 28px 0 10px;
}
.research-article p { margin: 0 0 16px; }
.research-article ul, .research-article ol { margin: 0 0 16px; padding-left: 1.35em; }
.research-article li { margin: 0 0 6px; }
.research-article strong { font-weight: 650; }
.research-article a { color: inherit; }
.research-article table {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0 24px;
  font-size: 14px;
  line-height: 1.55;
}
.research-article th, .research-article td {
  border: none;
  border-bottom: 1px solid #ececec;
  padding: 10px 12px;
  text-align: left;
  vertical-align: top;
}
.research-article thead th {
  background: #f7f7f8;
  font-weight: 650;
  border-bottom: 1px solid #e5e7eb;
}
.cite, a.cite, sup.cite {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 15px;
  height: 15px;
  margin: 0 1px 0 2px;
  padding: 0 4px;
  border-radius: 999px;
  background: #ececec;
  color: #6b7280;
  font-size: 10px;
  font-weight: 600;
  line-height: 1;
  text-decoration: none;
  vertical-align: super;
}
.research-refs { margin: 0; padding-left: 1.2em; }
.research-refs a { color: #2563eb; text-decoration: none; }
@media print {
  body, .research-article { background: #fff; }
  .research-article { padding: 0; max-width: none; }
}
""".strip()


_KNOWN_SECTION_RE = re.compile(
    r"(#{1,3}\s+(?:执行摘要|核心发现|证据与局限|建议|参考来源))(?=\S)"
)
_GLUED_HEADING_RE = re.compile(r"([^\n#])(#{1,3}\s+)")
_H1_BOLD_SUBTITLE_RE = re.compile(r"^(#{1,3}\s+[^\n*]+?)\*\*", re.M)
_GLUED_TABLE_SEP_RE = re.compile(r"(\|)\s+(\|\s*:?-{3,})")
_GLUED_TABLE_AFTER_SEP_RE = re.compile(r"(-{3,}\s*\|)\s+(\|)")


def unflatten_markdown_tables(src: str) -> str:
    """Restore pipe tables that were collapsed onto one physical line."""
    lines: list[str] = []
    for line in str(src or "").splitlines():
        if line.count("|") >= 4 and re.search(r"\|[\t ]*\|", line) and "-" in line:
            restored = _GLUED_TABLE_SEP_RE.sub(r"\1\n\2", line)
            restored = _GLUED_TABLE_AFTER_SEP_RE.sub(r"\1\n\2", restored)
            lines.extend(restored.splitlines())
            continue
        lines.append(line)
    return "\n".join(lines)


def _unflatten_markdown_headings(src: str) -> str:
    """Restore headings if a false-delivery scrub collapsed newlines to spaces."""
    text = str(src or "")
    if not text:
        return text
    if (
        " ## " not in text
        and " ### " not in text
        and "---" not in text
        and "##" not in text
        and "**" not in text
        and "|" not in text
    ):
        return text
    out = unflatten_markdown_tables(text)
    out = re.sub(r"\s*---+(?=#)", "\n\n", out)
    out = re.sub(r"(?:^|\n)\s*---+\s*(?=\n|$)", "\n\n", out)
    out = _GLUED_HEADING_RE.sub(r"\1\n\n\2", out)
    out = _H1_BOLD_SUBTITLE_RE.sub(r"\1\n\n**", out, count=1)
    out = _KNOWN_SECTION_RE.sub(r"\1\n\n", out)
    out = out.replace(" ## ", "\n\n## ").replace(" ### ", "\n\n### ")
    out = unflatten_markdown_tables(out)
    lines: list[str] = []
    for line in out.splitlines():
        match = _INLINE_SECTION_RE.match(line.strip())
        if match:
            lines.append(f"{match.group(1)} {match.group(2)}")
            lines.append("")
            lines.append(match.group(3).strip())
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def strip_research_scaffold(text: str) -> str:
    """Drop synthesis / false-delivery prefixes that models or scrubs put before the first heading."""
    src = str(text or "").strip()
    if src.startswith("文件还没有成功写入"):
        heading = re.search(r"(?:^|\s)(#{1,3}\s+\S)", src)
        if heading:
            src = src[heading.start():].lstrip()
        else:
            src = _FALSE_FILE_SCRUB_RE.sub("", src).strip()
    src = _unflatten_markdown_headings(src)
    match = _SCAFFOLD_HEADING_RE.search(src)
    if match and match.start() > 0:
        prefix = src[: match.start()]
        if (
            len(prefix) < 400
            and "|" not in prefix
            and re.search(
                r"现在合成完整研究报告|已掌握.{0,40}独立来源|文件还没有成功写入",
                prefix,
            )
        ):
            return src[match.start():].strip()
    return re.sub(r"^(?:---+\s*)+", "", src).strip()


_REPORT_SECTION_RE = re.compile(
    r"执行摘要|研究报告|核心发现|证据与局限|参考来源|调研报告|深度调研",
)


def looks_like_research_report(text: str) -> bool:
    """True when the answer already has a research-report skeleton worth compiling."""
    cleaned = strip_research_scaffold(text)
    if not re.search(r"^#{1,3}\s+\S", cleaned, re.M):
        return False
    if _REPORT_SECTION_RE.search(cleaned):
        return True
    headings = re.findall(r"^#{1,3}\s+\S", cleaned, re.M)
    return len(headings) >= 2 and len(cleaned) >= 400


def _safe_filename(title: str) -> str:
    compact = re.sub(r"[\\\\/:*?\"<>|]+", " ", title or "研究报告").strip()
    compact = re.sub(r"\s+", " ", compact)[:48] or "研究报告"
    return compact


def _today_label() -> str:
    now = datetime.now(timezone.utc).astimezone()
    return f"{now.year} 年 {now.month} 月 {now.day} 日"


def _split_chapters(markdown: str) -> list[tuple[str, str]]:
    text = str(markdown or "").strip()
    if not text:
        return []
    lines = text.splitlines()
    chunks: list[tuple[str, list[str]]] = []
    current_title = ""
    buf: list[str] = []
    for line in lines:
        match = _HEADING_RE.match(line.strip())
        if match and len(match.group(1)) <= 2:
            if buf or current_title:
                chunks.append((current_title, buf))
            current_title = match.group(2).strip()
            buf = []
            continue
        buf.append(line)
    if buf or current_title:
        chunks.append((current_title, buf))
    out: list[tuple[str, str]] = []
    for title, body_lines in chunks:
        body = "\n".join(body_lines).strip()
        if not title and not body:
            continue
        out.append((title or "正文", body))
    return out


def _inline_md(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2" rel="noreferrer">\1</a>', escaped)
    escaped = re.sub(
        r"\[\^(\d{1,3})\]",
        r'<a class="cite" href="#ref-\1">\1</a>',
        escaped,
    )
    escaped = re.sub(
        r"\[(\d{1,3})\]",
        r'<a class="cite" href="#ref-\1">\1</a>',
        escaped,
    )
    return escaped


def _looks_like_table_row(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and stripped.count("|") >= 2


def _is_table_sep(line: str) -> bool:
    compact = re.sub(r"[|\s]", "", line)
    return bool(compact) and set(compact) <= set("-:")


def _render_table(lines: list[str]) -> str:
    rows: list[list[str]] = []
    for line in lines:
        if _is_table_sep(line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    header, body = rows[0], rows[1:]
    head = "".join(f"<th>{_inline_md(cell)}</th>" for cell in header)
    body_html = "".join(
        "<tr>" + "".join(f"<td>{_inline_md(cell)}</td>" for cell in row) + "</tr>"
        for row in body
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body_html}</tbody></table>"


def _block_md(markdown: str) -> str:
    lines = str(markdown or "").splitlines()
    parts: list[str] = []
    para: list[str] = []
    list_buf: list[str] = []
    list_tag = ""

    def flush_para() -> None:
        nonlocal para
        if para:
            parts.append(f"<p>{_inline_md(' '.join(para))}</p>")
            para = []

    def flush_list() -> None:
        nonlocal list_buf, list_tag
        if list_buf and list_tag:
            items = "".join(f"<li>{_inline_md(item)}</li>" for item in list_buf)
            parts.append(f"<{list_tag}>{items}</{list_tag}>")
        list_buf, list_tag = [], ""

    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        if not line.strip():
            flush_para()
            flush_list()
            index += 1
            continue
        if re.match(r"^\[\^\d{1,3}\]:", line.strip()):
            index += 1
            continue
        if _looks_like_table_row(line):
            flush_para()
            flush_list()
            table_lines = []
            while index < len(lines) and _looks_like_table_row(lines[index].rstrip()):
                table_lines.append(lines[index].rstrip())
                index += 1
            rendered = _render_table(table_lines)
            if rendered:
                parts.append(rendered)
            continue
        heading = _HEADING_RE.match(line.strip())
        if heading:
            flush_para()
            flush_list()
            level = min(len(heading.group(1)) + 1, 3)
            parts.append(f"<h{level}>{_inline_md(heading.group(2).strip())}</h{level}>")
            index += 1
            continue
        unordered = re.match(r"^[-*+]\s+(.+)$", line.strip())
        ordered = re.match(r"^\d+[.)]\s+(.+)$", line.strip())
        if unordered or ordered:
            flush_para()
            tag = "ul" if unordered else "ol"
            if list_tag and list_tag != tag:
                flush_list()
            list_tag = tag
            list_buf.append((unordered or ordered).group(1))
            index += 1
            continue
        flush_list()
        para.append(line.strip())
        index += 1
    flush_para()
    flush_list()
    return "\n".join(parts) or "<p></p>"


def _title_from_markdown(markdown: str, fallback: str) -> str:
    for line in str(markdown or "").splitlines():
        match = _HEADING_RE.match(line.strip())
        if not match:
            continue
        title = match.group(2).strip()[:200]
        if "文件还没有成功写入" in title:
            continue
        return title
    compact = re.sub(r"\s+", " ", str(markdown or "")).strip()
    if compact and "文件还没有成功写入" not in compact:
        return compact[:80]
    return fallback[:200] or "研究报告"


def _references_from_ledger(ledger: ResearchLedger) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in ledger.sources:
        if item.url in seen:
            continue
        seen.add(item.url)
        rows.append({"title": item.title or item.url, "url": item.url})
    return rows[:80]


def build_document(
    *,
    markdown: str,
    ledger: ResearchLedger,
    query: str = "",
) -> ReportDocument:
    title = _title_from_markdown(markdown, query or "研究报告")
    chapters_raw = _split_chapters(markdown)
    if not chapters_raw:
        chapters_raw = [("执行摘要", markdown or "（暂无正文）")]
    # Drop a duplicate H1 cover title from the first chapter body if it matches.
    if chapters_raw and chapters_raw[0][0] == title and not chapters_raw[0][1].strip():
        chapters_raw = chapters_raw[1:] or chapters_raw
    chapters: list[ReportChapter] = []
    for index, (heading, body) in enumerate(chapters_raw):
        chapter_title = heading if heading != title else (heading if index else "执行摘要")
        if index == 0 and heading == title:
            chapter_title = "执行摘要"
            body = body or markdown
        chapters.append(ReportChapter(
            id=f"ch-{index + 1}",
            title=chapter_title[:200],
            markdown=body,
            source_urls=ledger.unique_urls()[:12],
        ))
    if len(chapters) == 1 and chapters[0].title in {title, "正文"}:
        body = chapters[0].markdown
        rebuilt: list[ReportChapter] = []
        for key, label in _DEFAULT_SECTIONS:
            rebuilt.append(ReportChapter(
                id=key, title=label, markdown=body if key == "exec" else "",
                source_urls=ledger.unique_urls()[:12],
            ))
        if rebuilt:
            rebuilt[0] = rebuilt[0].model_copy(update={"markdown": body})
            chapters = rebuilt
    toc = [chapter.title for chapter in chapters]
    date_label = f"截至 {_today_label()}"
    return ReportDocument(
        cover=ReportCover(
            title=title,
            subtitle="深度研究报告",
            date=date_label,
            query=query or ledger.query,
        ),
        toc=toc,
        chapters=chapters,
        references=_references_from_ledger(ledger),
    )


def render_markdown(document: ReportDocument) -> str:
    parts = [f"# {document.cover.title}", ""]
    if document.cover.date:
        parts.append(f"*{document.cover.date}*")
        parts.append("")
    for chapter in document.chapters:
        parts.append(f"## {chapter.title}")
        parts.append("")
        parts.append(chapter.markdown.strip() or "（本节暂无正文）")
        parts.append("")
    if document.references:
        parts.append("## 参考来源")
        parts.append("")
        for index, row in enumerate(document.references, start=1):
            parts.append(f"{index}. [{row.get('title') or row.get('url')}]({row.get('url')})")
        parts.append("")
    return "\n".join(parts).strip() + "\n"


def render_html(document: ReportDocument, *, markdown_source: str = "") -> str:
    parts = [f"<h1>{html.escape(document.cover.title)}</h1>"]
    for chapter in document.chapters:
        heading = chapter.title.strip()
        if heading and heading != document.cover.title:
            parts.append(
                f'<h2 id="{html.escape(chapter.id)}">{html.escape(heading)}</h2>'
            )
        parts.append(_block_md(chapter.markdown))
    if document.references:
        parts.append('<h2 id="refs">参考来源</h2>')
        items = []
        for index, row in enumerate(document.references, start=1):
            title = html.escape(row.get("title") or row.get("url") or "")
            url = html.escape(row.get("url") or "")
            items.append(
                f'<li id="ref-{index}"><a href="{url}" rel="noreferrer">'
                f'<span class="cite">{index}</span> {title}</a></li>'
            )
        parts.append(f'<ol class="research-refs">{"".join(items)}</ol>')
    md_block = html.escape(markdown_source or render_markdown(document))
    return (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(document.cover.title)}</title>"
        f"<style>{_RESEARCH_CSS}</style></head>"
        f'<body class="research-report" data-kind="research-report">'
        f'<article class="research-article" data-kind="research-report">'
        f'{"".join(parts)}</article>'
        f'<script type="text/plain" id="research-markdown">{md_block}</script>'
        "</body></html>"
    )


def compile_report(
    *,
    answer_markdown: str,
    ledger: ResearchLedger,
    query: str = "",
) -> CompiledReport:
    cleaned = strip_research_scaffold(answer_markdown)
    document = build_document(
        markdown=cleaned, ledger=ledger, query=query or ledger.query,
    )
    markdown = render_markdown(document)
    html_doc = render_html(document, markdown_source=markdown)
    return CompiledReport(
        title=document.cover.title,
        markdown=markdown,
        html=html_doc,
        document=document,
    )


def filename_stem(title: str) -> str:
    return _safe_filename(title)


def ledger_from_state(raw: Any) -> ResearchLedger:
    if isinstance(raw, ResearchLedger):
        return raw
    if isinstance(raw, dict):
        try:
            normalized = dict(raw)
            # v1.114 前 Research 会在台账里挂自动保存的报告文件。新契约只交付
            # 对话内蓝框报告；读取旧运行状态时丢弃这两个展示字段，不丢研究台账。
            normalized.pop("report_file_ids", None)
            normalized.pop("report_fingerprint", None)
            return ResearchLedger.model_validate(normalized)
        except Exception:  # noqa: BLE001
            pass
    return ResearchLedger()


def sources_as_citations(sources: list[SourceRecord]) -> list[dict[str, str]]:
    return [{"title": item.title or item.url, "url": item.url} for item in sources]
