/** aicss Thinking+Reasoning 几何：2 行 × 20px，视口封顶 180px。 */
export const THOUGHT_SENT_H = 40;
export const THOUGHT_GAP = 4;
export const THOUGHT_MAX_H = 180;
export const THOUGHT_FADE = 16;

const TERMINATOR = /[。！？!?]/;

/**
 * 把流式思考正文拆成展示句。已完成的句子保持稳定，最后一句未结束时就地更新。
 * 不注入「正在组织思路…」之类占位句；空正文就是空列表。
 */
export function splitThoughtSentences(text: string): string[] {
  const raw = String(text || '').replace(/\r/g, '');
  if (!raw.trim()) return [];

  const parts: string[] = [];
  let start = 0;
  const n = raw.length;

  const isAsciiPeriod = (i: number): boolean => {
    if (raw[i] !== '.') return false;
    const prev = raw[i - 1];
    const next = raw[i + 1];
    if (prev && /\d/.test(prev) && next && /\d/.test(next)) return false;
    if (next && /[A-Za-z]/.test(next)) return false;
    return true;
  };

  const consumeTail = (from: number): number => {
    let end = from;
    while (
      end < n
      && (TERMINATOR.test(raw[end]) || raw[end] === '.' || raw[end] === '"' || raw[end] === '”' || raw[end] === '’')
    ) {
      end += 1;
    }
    return end;
  };

  for (let i = 0; i < n; i += 1) {
    const ch = raw[i];
    if (ch === '\n') {
      const line = raw.slice(start, i).replace(/\s+/g, ' ').trim();
      if (line) parts.push(line);
      start = i + 1;
      continue;
    }
    if (TERMINATOR.test(ch) || isAsciiPeriod(i)) {
      const end = consumeTail(i + 1);
      const line = raw.slice(start, end).replace(/\s+/g, ' ').trim();
      if (line) parts.push(line);
      start = end;
      i = end - 1;
    }
  }

  const rest = raw.slice(start).replace(/\s+/g, ' ').trim();
  if (rest) parts.push(rest);
  return parts;
}

/**
 * 思考正文按空行切段，对应 Cursor 展开态的段落间距。
 * 单换行留在段内（pre-wrap），不把一句拆成多块。
 */
export function splitThoughtParagraphs(text: string): string[] {
  const raw = String(text || '').replace(/\r/g, '').trim();
  if (!raw) return [];
  return raw.split(/\n{2,}/).map((part) => part.trim()).filter(Boolean);
}

/** 思考流式追赶：与正文 typewriter 同帧率，步长略小，避免整段喷出。 */
export const THOUGHT_STREAM_FRAME_MS = 32;
export const THOUGHT_STREAM_MAX_STEP = 24;
export const THOUGHT_STREAM_CATCHUP_RATIO = 0.22;

export function thoughtStreamCatchupStep(backlog: number, finishing = false): number {
  const n = Math.max(0, Number(backlog) || 0);
  if (!(n > 0)) return 0;
  if (finishing) return n;
  return Math.min(THOUGHT_STREAM_MAX_STEP, Math.max(1, Math.ceil(n * THOUGHT_STREAM_CATCHUP_RATIO)));
}

export function formatThoughtSeconds(seconds?: number, live = false): string {
  if (seconds == null || Number.isNaN(Number(seconds))) return '';
  const n = Math.max(0, Number(seconds) || 0);
  if (live) return `${Math.floor(n)}s`;
  if (!(n > 0)) return '';
  return `${Math.max(1, Math.round(n))}s`;
}

export function thoughtStreamMetrics(
  count: number,
  opts: {
    done: boolean;
    open: boolean;
    fadeTop: boolean;
    fadeBottom: boolean;
  },
): {
  contentH: number;
  capped: boolean;
  viewH: number;
  scrollable: boolean;
  translate: number;
  mask: string;
} {
  const contentH = count > 0 ? count * THOUGHT_SENT_H + (count - 1) * THOUGHT_GAP : 0;
  const capped = contentH > THOUGHT_MAX_H;
  const viewH = capped ? THOUGHT_MAX_H : contentH;
  const scrollable = opts.done && opts.open;
  const translate = scrollable ? 0 : capped ? THOUGHT_MAX_H - THOUGHT_FADE - contentH : 0;
  const showTop = scrollable ? opts.fadeTop : capped;
  const showBottom = scrollable ? opts.fadeBottom : capped;
  const mask = capped
    ? `linear-gradient(to bottom, transparent 0, #000 ${showTop ? THOUGHT_FADE : 0}px, #000 calc(100% - ${showBottom ? THOUGHT_FADE : 0}px), transparent 100%)`
    : 'none';
  return { contentH, capped, viewH, scrollable, translate, mask };
}
