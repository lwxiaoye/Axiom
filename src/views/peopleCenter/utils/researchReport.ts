/** Parse a Deep Research HTML report into a continuous article for the exhibition viewer. */

export type ResearchReportPage = {
  id: string;
  title: string;
  html: string;
};

export type ParsedResearchReport = {
  title: string;
  pages: ResearchReportPage[];
  markdown: string;
  articleHtml: string;
};

export function isResearchReportHtml(html: string | undefined | null): boolean {
  const src = String(html || '');
  return src.includes('data-kind="research-report"') || src.includes('class="research-report"')
    || src.includes('class="research-article"');
}

export function isResearchReportFile(file: {
  filename?: string;
  origin?: { tool?: string } | null;
  source?: string;
} | null | undefined): boolean {
  if (!file) return false;
  return file.origin?.tool === 'research' || file.source === 'research';
}

/** Depth-research answers that should keep Markdown + [n] cites in the chat body. */
export function isResearchTurn(message: {
  agentMode?: string;
  researchProgress?: unknown;
  content?: string;
  generatedFiles?: Array<{ filename?: string; source?: string; origin?: { tool?: string } | null }>;
} | null | undefined): boolean {
  if (!message) return false;
  // agent_mode 是本轮显式 Profile 的权威事实源。旧后端曾对所有
  // search_web / update_plan 都发 research.progress，因此已明确是 standard/plan
  // 的历史轮次绝不能再被这个污染事件改判为 Research。
  if (message.agentMode) return message.agentMode === 'research';
  // 旧历史还没有 agent_mode 投影时，仅保留结构化研究事实的兼容回放。
  if (message.researchProgress) return true;
  return (message.generatedFiles || []).some((file) => isResearchReportFile(file));
}

const RESEARCH_SECTION_RE = /执行摘要|研究报告|核心发现|证据与局限|参考来源|调研报告|深度调研/;

export function looksLikeResearchMarkdown(content: string | undefined | null): boolean {
  const cleaned = stripResearchScaffold(String(content || ''));
  if (!/^#{1,3}\s+\S/m.test(cleaned)) return false;
  if (RESEARCH_SECTION_RE.test(cleaned)) return true;
  const headingCount = (cleaned.match(/^#{1,3}\s+\S/gm) || []).length;
  return headingCount >= 2 && cleaned.length >= 400;
}

export function researchStructureMarkdown(message: {
  content?: string;
} | null | undefined): string {
  const cleaned = stripResearchScaffold(String(message?.content || ''));
  return looksLikeResearchMarkdown(cleaned) ? cleaned : '';
}

export function researchStructureTitle(markdown: string, fallback = '研究报告'): string {
  const heading = String(markdown || '').match(/^#{1,3}\s+(.+)$/m);
  return sanitizeResearchTitle(heading?.[1] || '', fallback);
}

const INLINE_SECTION_RE = /^(#{1,3})\s+(执行摘要|核心发现|证据与局限|建议|参考来源)(?:\s+|：)(.+)$/;
const KNOWN_SECTION_RE = /(#{1,3}\s+(?:执行摘要|核心发现|证据与局限|建议|参考来源))(?=\S)/g;
const GLUED_HEADING_RE = /([^\n#])(#{1,3}\s+)/g;
const H1_BOLD_SUBTITLE_RE = /^(#{1,3}\s+[^\n*]+?)\*\*/m;

function unflattenMarkdownHeadings(src: string): string {
  if (
    !src.includes(' ## ')
    && !src.includes(' ### ')
    && !src.includes('---')
    && !src.includes('##')
    && !src.includes('**')
    && !src.includes('|')
  ) {
    return src;
  }
  let restored = unflattenMarkdownTables(src);
  restored = restored
    .replace(/\s*---+(?=#)/g, '\n\n')
    .replace(/(^|\n)\s*---+\s*(?=\n|$)/g, '\n\n')
    .replace(GLUED_HEADING_RE, '$1\n\n$2');
  restored = restored.replace(H1_BOLD_SUBTITLE_RE, '$1\n\n**');
  restored = restored.replace(KNOWN_SECTION_RE, '$1\n\n');
  restored = restored.replace(/ ## /g, '\n\n## ').replace(/ ### /g, '\n\n### ');
  restored = restored
    .split('\n')
    .map((line) => {
      const match = line.trim().match(INLINE_SECTION_RE);
      if (!match) return line;
      return `${match[1]} ${match[2]}\n\n${match[3].trim()}`;
    })
    .join('\n');
  return unflattenMarkdownTables(restored).trim();
}

function unflattenMarkdownTables(src: string): string {
  return src
    .split('\n')
    .flatMap((line) => {
      if (line.split('|').length < 5 || !/\|[\t ]*\|/.test(line) || !line.includes('-')) {
        return [line];
      }
      return line
        .replace(/(\|)\s+(\|\s*:?-{3,})/g, '$1\n$2')
        .replace(/(-{3,}\s*\|)\s+(\|)/g, '$1\n$2')
        .split('\n');
    })
    .join('\n');
}

/** Drop synthesis / false-delivery prefixes that models or scrubs put before the first heading. */
export function stripResearchScaffold(text: string): string {
  let src = String(text || '').trim();
  if (src.startsWith('文件还没有成功写入')) {
    const heading = src.search(/(?:^|\s)#{1,3}\s+\S/);
    src = heading >= 0 ? src.slice(heading).trim() : src.replace(/^文件还没有成功写入「我的文件」[\s\S]*?(?:已整理的内容要点：)?/, '').trim();
  }
  src = unflattenMarkdownHeadings(src);
  const heading = src.search(/^#{1,3}\s+\S/m);
  if (heading > 0) {
    const prefix = src.slice(0, heading);
    if (
      prefix.length < 400
      && !prefix.includes('|')
      && /现在合成完整研究报告|已掌握.{0,40}独立来源|文件还没有成功写入/.test(prefix)
    ) {
      return src.slice(heading).trim();
    }
  }
  return src.replace(/^(?:---+\s*)+/, '').trim();
}

export function countResearchSearches(message: {
  researchProgress?: { searchCalls?: number } | null;
  agentSteps?: Array<{ name?: string }> | null;
}): number {
  const fromSteps = (message.agentSteps || []).filter((step) => {
    const name = String(step.name || '');
    return name === 'search_web' || name.startsWith('search_');
  }).length;
  if (fromSteps > 0) return fromSteps;
  return Math.max(0, Number(message.researchProgress?.searchCalls) || 0);
}

/** sourcesFound is the persisted, team-wide URL-deduplicated ledger total. */
export function researchCompletionStats(
  message: {
    researchProgress?: { sourcesFound?: number } | null;
  },
  durationText = '',
): string {
  const pages = message.researchProgress?.sourcesFound;
  const pageText = typeof pages === 'number' && Number.isSafeInteger(pages) && pages >= 0
    ? `共搜索到 ${pages} 个网页`
    : '网页总数未记录';
  return `${durationText ? `耗时 ${durationText}` : '耗时未记录'} · ${pageText}`;
}

export function sanitizeResearchTitle(title: string, fallback = '研究报告'): string {
  const raw = String(title || '').replace(/\.(html?|research\.md)$/i, '').trim();
  if (!raw) return fallback;
  if (/文件还没有成功写入|未能完成交付/.test(raw)) {
    const heading = raw.match(/#\s+([^\n#]+)/);
    if (heading?.[1]) return heading[1].trim().slice(0, 80);
    return fallback;
  }
  return raw.slice(0, 120);
}

const LEADING_ATX_HEADING_RE = /^(#{1,3})[ \t]+(.+?)(?:[ \t]+#+\s*)?(?:\r?\n+|$)/;

function normalizeHeadingText(text: string): string {
  return String(text || '')
    .replace(/[*_`]+/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}

function headingMatchesTitle(heading: string, title: string): boolean {
  const a = normalizeHeadingText(heading);
  const b = normalizeHeadingText(title);
  if (!a || !b) return false;
  if (a === b) return true;
  const shorter = a.length <= b.length ? a : b;
  const longer = a.length <= b.length ? b : a;
  return shorter.length >= 20 && longer.startsWith(shorter);
}

/** Drop the document title from the body when the card chrome or fullscreen sheet already shows it. */
export function stripLeadingTitleHeadings(markdown: string, title?: string): string {
  let src = String(markdown || '').replace(/^\uFEFF/, '').replace(/^\s+/, '');
  const wanted = String(title || '').trim();
  if (!wanted || !src) return src;
  let removed = false;
  while (src) {
    const match = src.match(LEADING_ATX_HEADING_RE);
    if (!match) break;
    if (!headingMatchesTitle(match[2], wanted)) break;
    src = src.slice(match[0].length).replace(/^\s+/, '');
    removed = true;
  }
  return removed ? src : String(markdown || '').replace(/^\uFEFF/, '').replace(/^\s+/, '');
}

/** Turn leftover [n] markers into the same cite chips the compiled HTML report uses. */
export function linkResearchCites(html: string): string {
  return String(html || '').replace(
    /\[(?:资料)?(\d{1,3})\]/g,
    '<a class="cite-chip" href="#ref-$1">$1</a>',
  );
}

function headingText(node: Element): string {
  return String(node.textContent || '').trim();
}

export function parseResearchReport(html: string): ParsedResearchReport | null {
  const src = String(html || '');
  if (!isResearchReportHtml(src) || typeof DOMParser === 'undefined') return null;
  const doc = new DOMParser().parseFromString(src, 'text/html');
  const article = doc.querySelector('article.research-article, article.research-report, .research-article');
  const pageNodes = Array.from(doc.querySelectorAll('.research-page'));
  const articleHtml = article
    ? article.innerHTML
    : pageNodes.map((node) => node.innerHTML).join('\n');
  if (!String(articleHtml || '').trim()) return null;

  const markdown = String(doc.querySelector('#research-markdown')?.textContent || '').trim();
  const title = sanitizeResearchTitle(
    String(
      doc.querySelector('title')?.textContent
      || article?.querySelector('h1')?.textContent
      || doc.querySelector('h1')?.textContent
      || '',
    ).trim(),
    sanitizeResearchTitle(markdown.match(/^#\s+(.+)$/m)?.[1] || '', '研究报告'),
  );

  const headingRoot = article || doc.body;
  const pages: ResearchReportPage[] = [];
  headingRoot.querySelectorAll('h2').forEach((node, index) => {
    const heading = headingText(node);
    if (!heading) return;
    pages.push({
      id: String(node.getAttribute('id') || `section-${index + 1}`),
      title: heading,
      html: node.outerHTML,
    });
  });
  if (!pages.length) {
    pages.push({
      id: 'body',
      title: title || '研究报告',
      html: articleHtml,
    });
  }
  return {
    title: title || pages[0]?.title || '研究报告',
    pages,
    markdown,
    articleHtml,
  };
}

export function reportFilenameStem(title: string, filename?: string): string {
  const raw = sanitizeResearchTitle(title, sanitizeResearchTitle(filename || '', '研究报告'))
    .replace(/[\\/:*?"<>|]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return raw.slice(0, 80) || '研究报告';
}

export function downloadTextFile(filename: string, text: string, mime: string) {
  const blob = new Blob([text], { type: mime });
  downloadBlob(filename, blob);
}

export function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
