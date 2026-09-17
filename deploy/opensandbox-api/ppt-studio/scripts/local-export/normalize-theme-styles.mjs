/**
 * Normalize legacy bare theme style references before the PPTD object reaches
 * the official WASM writer.
 *
 * Canonical PPTD uses "$name".  Older/non-Kimi generators often emitted
 * "name"; the writer ignores that value and silently falls back to its default
 * typography.  The publish lint rejects new bare references, while this bridge
 * keeps existing editable projects from flattening when exported directly.
 */
export function normalizePptdThemeStyleRefsForWasm(pptd) {
  if (!pptd || typeof pptd !== 'object') return pptd;
  const styles = pptd?.theme?.textStyles;
  if (!styles || typeof styles !== 'object' || Array.isArray(styles)) return pptd;

  const normalize = (holder) => {
    if (!holder || typeof holder !== 'object') return;
    const value = holder.style;
    if (typeof value !== 'string' || !value || value.startsWith('$')) return;
    if (Object.prototype.hasOwnProperty.call(styles, value)) holder.style = `$${value}`;
  };

  for (const page of pptd.pages ?? []) {
    for (const element of page?.elements ?? []) {
      if (element?.elementType === 'text') normalize(element.content);
      if (element?.elementType === 'table') {
        for (const row of element.rows ?? []) {
          for (const cell of row ?? []) normalize(cell);
        }
      }
    }
  }
  return pptd;
}
