"""Receipt-based partial delivery without presenting scraped pages as analysis."""
from __future__ import annotations

import html
import re

from .engine import synthesis_sources


def _text(value, limit=700):
    # Material is displayed as quoted plain text, never as executable Markdown
    # or a source-supplied citation that could change the report's numbering.
    value = str(value or "")
    for _ in range(2):
        value = html.unescape(value)
    value = " ".join(value.split())
    if len(value) > limit:
        value = value[:limit].rstrip() + "…"
    value = value.replace("[", "［").replace("]", "］")
    return re.sub(r"([\\`*_{}#!|])", r"\\\1", html.escape(value, quote=False))


def build_partial_report(ledger, query):
    sources = synthesis_sources(ledger)
    bodies = sum(bool(s.scraped and s.snippet.strip()) for s in sources)
    lines = [f"# {_text(ledger.query or query, 180)}｜部分研究报告", "",
        "## 执行摘要", "",
        "本轮未完成报告核验，暂时无法交付完整结论。已取得的研究材料与待核实问题如下；"
        "材料收集不等同于结论得到证实，未经核验的草稿不会作为正式报告发布。", "",
        "### 材料概况", "",
        f"- 来源链接：{len(sources)} 个。",
        f"- 已取得正文片段：{bodies} 个；其余仅为搜索线索。",
        "- 核验状态：未完成报告级核验，来源数量不代表独立验证的数量。", "",
        "## 研究范围与材料进度", "",
        "| 研究主题 | 已取得的材料 | 当前状态 |",
        "| --- | --- | --- |"]
    topics = [(topic.title, [i for i, source in enumerate(sources) if source.applies_to(topic.topic_id)])
              for topic in ledger.topics]
    if not topics:
        topics = [(ledger.query or query or "本次研究", list(range(len(sources))))]
    assigned = {i for _, indices in topics for i in indices}
    unassigned = [i for i in range(len(sources)) if i not in assigned]
    if unassigned:
        topics.append(("其他检索材料", unassigned))
    for title, indices in topics:
        read = sum(bool(sources[i].scraped and sources[i].snippet.strip()) for i in indices)
        refs = " ".join(f"[{i + 1}]" for i in indices[:3])
        material = f"{read} 份正文片段、{len(indices) - read} 条搜索线索" if indices else "尚无可用材料"
        lines.append(f"| {_text(title, 200)} | {material} {refs} | 尚未完成结论核验 |")
    lines.extend(["", "## 证据与局限", "",
        "目前只能确认材料的取得情况，不能据此确认问题本身的最终结论。"
        "正文片段可能缺少上下文；仅有搜索线索的材料尚未核验全文，不能替代对原文的核对。"
        "同一内容的转载也不能算作独立验证。", ""])
    if not sources:
        lines.extend(["本轮没有取得可引用的来源内容，无法对问题作出有证据支持的判断。", ""])
    lines.extend(["## 尚待解决的问题", "",
        "以下是本轮研究问题的核验待办，不能将检索过或读过材料等同于已经完成结论核验。", ""])
    for title, indices in topics:
        detail = "核对材料能否支持结论、适用条件及相反证据" if indices else "补充可读取的一手材料，再形成判断"
        lines.append(f"- **{_text(title, 200)}**：{detail}。")
    lines.extend(["", "## 后续建议", "",
        "优先补齐影响核心结论的缺口，再整合成完整报告。"
        "涉及版本、日期、性能或方案推荐时，需要进一步核对原始来源和适用条件。"])
    return "\n".join(lines)
