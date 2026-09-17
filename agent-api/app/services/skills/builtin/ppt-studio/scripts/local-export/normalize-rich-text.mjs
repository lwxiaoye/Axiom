/** Normalize PPTD v2 rich-text shorthand before calling the official WASM. */

const ENTITY_AT = /^&(?:#[0-9]+|#x[0-9a-f]+|[a-z][a-z0-9]+);/i;

function tagAt(value, start) {
  if (value[start] !== '<') return null;
  let cursor = start + 1;
  if (value[cursor] === '/') cursor += 1;
  if (!/[A-Za-z]/.test(value[cursor] || '')) return null;

  let quote = null;
  for (let index = cursor + 1; index < value.length; index += 1) {
    const char = value[index];
    if (quote) {
      if (char === quote) quote = null;
      continue;
    }
    if (char === '"' || char === "'") {
      quote = char;
      continue;
    }
    if (char === '<') return null;
    if (char === '>') return value.slice(start, index + 1);
  }
  return null;
}

function escapeText(value) {
  let output = '';
  for (let index = 0; index < value.length;) {
    if (value[index] === '&') {
      const entity = ENTITY_AT.exec(value.slice(index));
      if (entity) {
        output += entity[0];
        index += entity[0].length;
        continue;
      }
      output += '&amp;';
    } else if (value[index] === '<') {
      output += '&lt;';
    } else if (value[index] === '>') {
      output += '&gt;';
    } else {
      output += value[index];
    }
    index += 1;
  }
  return output;
}

function tokenize(value) {
  const tokens = [];
  let textStart = 0;
  let hasTag = false;
  for (let index = 0; index < value.length;) {
    const tag = tagAt(value, index);
    if (!tag) {
      index += 1;
      continue;
    }
    if (index > textStart) tokens.push({ type: 'text', value: value.slice(textStart, index) });
    tokens.push({ type: 'tag', value: tag });
    hasTag = true;
    index += tag.length;
    textStart = index;
  }
  if (textStart < value.length) tokens.push({ type: 'text', value: value.slice(textStart) });
  return { tokens, hasTag };
}

export function normalizeRichText(value) {
  if (typeof value !== 'string') return value;
  const normalized = value.replace(/\r\n?/g, '\n');
  const { tokens, hasTag } = tokenize(normalized);
  if (!hasTag) {
    return normalized.split('\n').map((line) => `<p>${escapeText(line)}</p>`).join('');
  }
  return tokens.map((token) => (
    token.type === 'tag' ? token.value : escapeText(token.value)
  )).join('');
}

export function normalizePptdRichTextForWasm(pptd) {
  for (const page of pptd?.pages ?? []) {
    for (const element of page?.elements ?? []) {
      if (element?.elementType === 'text' && typeof element.content?.text === 'string') {
        element.content.text = normalizeRichText(element.content.text);
      }
      if (element?.elementType === 'table') {
        for (const row of element.rows ?? []) {
          for (const cell of row ?? []) {
            if (cell && typeof cell.text === 'string') cell.text = normalizeRichText(cell.text);
          }
        }
      }
    }
  }
  return pptd;
}
