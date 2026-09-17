const MIN_DUPLICATE_TEXT_CHARS = 220;
const NGRAM_SIZE = 3;
const MIN_OVERLAP_RATIO = 0.72;

function normalizeComparableText(value: string): string {
  return String(value || '')
    .toLowerCase()
    .replace(/[^0-9a-z\u3400-\u9fff]+/gu, '');
}

function ngrams(value: string): Set<string> {
  const out = new Set<string>();
  for (let index = 0; index <= value.length - NGRAM_SIZE; index += 1) {
    out.add(value.slice(index, index + NGRAM_SIZE));
  }
  return out;
}

/**
 * Historical fallback for a backend bug that persisted a complete answer draft as commentary
 * before persisting the authoritative final answer. Short progress updates are never touched.
 */
export function commentaryRepeatsFinalAnswer(commentary: string, finalAnswer: string): boolean {
  const note = normalizeComparableText(commentary);
  const final = normalizeComparableText(finalAnswer);
  if (Math.min(note.length, final.length) < MIN_DUPLICATE_TEXT_CHARS) return false;
  if (note === final || note.includes(final) || final.includes(note)) return true;

  const noteNgrams = ngrams(note);
  const finalNgrams = ngrams(final);
  const denominator = Math.min(noteNgrams.size, finalNgrams.size);
  if (!denominator) return false;

  let overlap = 0;
  const smaller = noteNgrams.size <= finalNgrams.size ? noteNgrams : finalNgrams;
  const larger = smaller === noteNgrams ? finalNgrams : noteNgrams;
  for (const token of smaller) {
    if (larger.has(token)) overlap += 1;
  }
  return overlap / denominator >= MIN_OVERLAP_RATIO;
}
