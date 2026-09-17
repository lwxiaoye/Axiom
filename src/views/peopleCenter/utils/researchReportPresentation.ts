/** Continuous report typography over already sanitized HTML; saved evidence is unchanged. */
export function presentResearchReport(html: string): string {
  if (typeof DOMParser === 'undefined') return html;
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const partial = /报告生成或核验未能在本轮时间预算内完成|本轮未完成报告核验/.test(doc.body.textContent || '');
  let omittedLevel = 0;
  for (const node of Array.from(doc.body.children)) {
    const level = /^H[1-6]$/.test(node.tagName) ? Number(node.tagName[1]) : 0;
    if (level && level <= omittedLevel) omittedLevel = 0;
    const title = (node.textContent || '').trim();
    if (level && (/^(?:\d+[.、\s]*)?(参考来源|参考文献|引用来源|来源链接)[：:]?$/.test(title)
      || (partial && /^(已取得的材料|来源摘录)$/.test(title)))) {
      omittedLevel = level;
    }
    if (omittedLevel) node.remove();
  }
  // Sources remain in the source panel; inline labels retain their meaning.
  for (const link of Array.from(doc.body.querySelectorAll('a'))) {
    if (link.closest('pre, code')) continue;
    if (link.classList.contains('cite-chip')) {
      const cite = doc.createElement('sup');
      cite.className = 'research-cite';
      cite.textContent = link.textContent;
      link.replaceWith(cite);
    } else if (/^https?:\/\//i.test(link.textContent?.trim() || '')) {
      link.remove();
    } else {
      link.replaceWith(doc.createTextNode(link.textContent || ''));
    }
  }
  for (const paragraph of Array.from(doc.body.querySelectorAll('p'))) {
    if (!paragraph.textContent?.trim() && !paragraph.querySelector('img')) paragraph.remove();
  }
  for (const table of Array.from(doc.body.querySelectorAll('table'))) {
    if (table.parentElement?.classList.contains('research-table-scroll')) continue;
    const wrapper = doc.createElement('div');
    wrapper.className = 'research-table-scroll';
    wrapper.tabIndex = 0;
    wrapper.setAttribute('role', 'region');
    wrapper.setAttribute('aria-label', '报告对比表格');
    table.replaceWith(wrapper);
    wrapper.append(table);
  }
  return doc.body.innerHTML;
}
