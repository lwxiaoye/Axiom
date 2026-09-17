/**
 * Lossless presentation compiler for assistant answers.
 *
 * Some models occasionally ignore the Markdown layout contract and return an
 * entire long answer on one physical line. The persisted answer remains the
 * source of truth; this compiler may only insert presentation tokens between
 * source characters. It never replaces, removes, or reorders source text.
 */

export interface AnswerLayoutFragment {
  kind: 'source' | 'layout';
  text: string;
}

export interface CompiledAnswerLayout {
  markdown: string;
  /** Reconstructed only from source fragments; useful for enforcing losslessness. */
  sourceText: string;
  applied: boolean;
  fragments: readonly AnswerLayoutFragment[];
}

interface BoundaryLayout {
  breaks: number;
  openStrong: boolean;
  closeStrong: boolean;
}

interface SectionSpan {
  start: number;
  end: number;
}

const MIN_WALL_LENGTH = 280;
const SECTION_LABEL =
  /核心结论|核心变化|验证结果|仍需注意|主要发现|关键发现|推荐方案|主要风险|注意事项|值得关注的点|数据来源与局限|来源与局限|局限说明|时间线速览|工具图谱/g;
const CTA_START = /需要的话[，,]我可以|如果你愿意[，,]我可以/g;

function candidateBulletPositions(source: string): number[] {
  const positions: number[] = [];
  for (let index = source.indexOf('- '); index >= 0; index = source.indexOf('- ', index + 2)) {
    const previous = source[index - 1] || '';
    const next = source[index + 2] || '';
    if (!next || /\s/.test(next)) continue;
    // A spaced mathematical/prose dash ("A - B") is not a list marker. The
    // flattened form produced by affected models normally glues the marker to
    // the preceding sentence or heading.
    if (previous && /\s/.test(previous)) continue;
    positions.push(index);
  }
  return positions;
}

function alreadyReadable(source: string): boolean {
  const lines = source.split('\n');
  const longestLine = lines.reduce((max, line) => Math.max(max, line.length), 0);
  if (longestLine < MIN_WALL_LENGTH) return true;

  const meaningfulBreaks = (source.match(/\n\s*\n/g) || []).length;
  return meaningfulBreaks >= 2 && longestLine < 520;
}

function extendParentheticalTitle(source: string, end: number): number {
  if (source[end] !== '（') return end;
  const closing = source.indexOf('）', end + 1);
  if (closing < 0 || closing - end > 80) return end;
  return closing + 1;
}

function collectSectionSpans(source: string): SectionSpan[] {
  const spans: SectionSpan[] = [];
  const add = (start: number, end: number) => {
    if (start < 0 || end <= start) return;
    if (source[end] === '：' || source[end] === ':') end += 1;
    spans.push({ start, end });
  };

  // These labels are especially common in research answers and remain useful
  // even when the model glues them directly to the previous list item.
  for (const pattern of [/模型端时间线/g, /(?:harness|Harness)\s*工具图谱/g]) {
    for (const match of source.matchAll(pattern)) {
      const start = match.index ?? -1;
      add(start, extendParentheticalTitle(source, start + match[0].length));
    }
  }

  for (const match of source.matchAll(SECTION_LABEL)) {
    const start = match.index ?? -1;
    const previous = source[start - 1] || '';
    const stickyResearchLabel = /^(?:值得关注的点|数据来源与局限|来源与局限|局限说明)$/.test(
      match[0],
    );
    if (stickyResearchLabel || start === 0 || /[。！？；：:\n）)]/.test(previous)) {
      add(start, extendParentheticalTitle(source, start + match[0].length));
    }
  }

  // 旧交付提示曾强制「要点/说明」，已落库的弱模型回答可能把它们
  // 粘在上一句后。只在句子边界上识别，避免把「这说明…」拆成标题。
  for (const match of source.matchAll(/要点|说明/g)) {
    const start = match.index ?? -1;
    const previous = source[start - 1] || '';
    const next = source[start + match[0].length] || '';
    if ((start === 0 || /[。！？；\n]/.test(previous)) && next && !/[，,。！？\s]/.test(next)) {
      add(start, start + match[0].length);
    }
  }

  // "结论" is useful as a heading only when it begins a new sentence and is
  // immediately followed by body text. Phrases such as "先给结论：" stay put.
  for (const match of source.matchAll(/结论/g)) {
    const start = match.index ?? -1;
    const previous = source[start - 1] || '';
    const next = source[start + match[0].length] || '';
    if ((start === 0 || /[。！？；\n]/.test(previous)) && next && !/[：:，,。！？\s]/.test(next)) {
      add(start, start + match[0].length);
    }
  }

  return spans
    .sort((a, b) => a.start - b.start || b.end - a.end)
    .filter((span, index, all) => index === 0 || span.start >= all[index - 1].end);
}

function addBoundary(
  boundaries: Map<number, BoundaryLayout>,
  index: number,
  patch: Partial<BoundaryLayout>,
) {
  const current = boundaries.get(index) || { breaks: 0, openStrong: false, closeStrong: false };
  boundaries.set(index, {
    breaks: Math.max(current.breaks, patch.breaks || 0),
    openStrong: current.openStrong || Boolean(patch.openStrong),
    closeStrong: current.closeStrong || Boolean(patch.closeStrong),
  });
}

function addParagraphFallback(source: string, boundaries: Map<number, BoundaryLayout>) {
  const sortedBoundaries = () => [...boundaries.keys()].sort((a, b) => a - b);
  let paragraphStart = 0;
  let lastSentenceEnd = -1;

  for (let index = 0; index < source.length; index += 1) {
    if (boundaries.has(index)) paragraphStart = index;
    if (!/[。！？!?]/.test(source[index])) continue;
    lastSentenceEnd = index + 1;
    if (lastSentenceEnd - paragraphStart < 150) continue;

    addBoundary(boundaries, lastSentenceEnd, { breaks: 2 });
    paragraphStart = lastSentenceEnd;
  }

  // A very long final sentence still benefits from the last safe sentence
  // boundary; never split on commas, numbers, URLs, or arbitrary character counts.
  const finalBoundaries = sortedBoundaries();
  const lastBoundary = finalBoundaries[finalBoundaries.length - 1] || 0;
  if (source.length - lastBoundary > 360 && lastSentenceEnd > lastBoundary) {
    addBoundary(boundaries, lastSentenceEnd, { breaks: 2 });
  }
}

function insertionText(layout: BoundaryLayout): string {
  return `${layout.closeStrong ? '**' : ''}${'\n'.repeat(layout.breaks)}${
    layout.openStrong ? '**' : ''
  }`;
}

function assemble(source: string, boundaries: Map<number, BoundaryLayout>): CompiledAnswerLayout {
  const fragments: AnswerLayoutFragment[] = [];
  const positions = [...boundaries.keys()]
    .filter((position) => position >= 0 && position <= source.length)
    .sort((a, b) => a - b);
  let cursor = 0;

  for (const position of positions) {
    if (position > cursor) {
      fragments.push({ kind: 'source', text: source.slice(cursor, position) });
    }
    const token = insertionText(boundaries.get(position)!);
    if (token) fragments.push({ kind: 'layout', text: token });
    cursor = position;
  }
  if (cursor < source.length) fragments.push({ kind: 'source', text: source.slice(cursor) });

  const sourceText = fragments
    .filter((fragment) => fragment.kind === 'source')
    .map((fragment) => fragment.text)
    .join('');
  if (sourceText !== source) {
    return {
      markdown: source,
      sourceText: source,
      applied: false,
      fragments: [{ kind: 'source', text: source }],
    };
  }

  return {
    markdown: fragments.map((fragment) => fragment.text).join(''),
    sourceText,
    applied: fragments.some((fragment) => fragment.kind === 'layout'),
    fragments,
  };
}

export function compileAnswerLayout(input: string): CompiledAnswerLayout {
  const source = String(input || '');
  const unchanged = (): CompiledAnswerLayout => ({
    markdown: source,
    sourceText: source,
    applied: false,
    fragments: [{ kind: 'source', text: source }],
  });

  if (!source.trim() || alreadyReadable(source) || source.includes('```')) return unchanged();

  const bullets = candidateBulletPositions(source);
  const sections = collectSectionSpans(source);
  const longestLine = source.split('\n').reduce((max, line) => Math.max(max, line.length), 0);
  if (longestLine < MIN_WALL_LENGTH) return unchanged();

  const boundaries = new Map<number, BoundaryLayout>();
  for (const section of sections) {
    addBoundary(boundaries, section.start, {
      breaks: section.start > 0 ? 2 : 0,
      openStrong: true,
    });
    addBoundary(boundaries, section.end, { breaks: 2, closeStrong: true });
  }

  bullets.forEach((position, index) => {
    const beginsSectionList = sections.some((section) => section.end === position);
    addBoundary(boundaries, position, { breaks: index === 0 || beginsSectionList ? 2 : 1 });
  });

  for (const match of source.matchAll(CTA_START)) {
    const start = match.index ?? -1;
    if (start > 0 && /[。！？]/.test(source[start - 1])) {
      addBoundary(boundaries, start, { breaks: 2 });
    }
  }

  if (bullets.length < 2 && sections.length < 2) {
    addParagraphFallback(source, boundaries);
  }

  return assemble(source, boundaries);
}
