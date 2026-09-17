/**
 * 剥掉助手正文里的网页/资料来源编号 [1][12]、[资料3]（2026-08-09）。
 * 不动 [图N]；调用方负责先拆开 code/pre 再传入。
 */
export function stripInlineSourceMarkers(seg: string): string {
  let out = String(seg || '');
  if (!out.includes('[')) return out;
  const held: string[] = [];
  out = out.replace(/\[图(\d{1,2})\]/g, (m) => {
    held.push(m);
    return `\uFFF0IMG${held.length - 1}\uFFF1`;
  });
  out = out.replace(/\[(?:资料)?(\d{1,2})\]/g, '');
  for (let i = 0; i < held.length; i += 1) {
    out = out.replace(`\uFFF0IMG${i}\uFFF1`, held[i]);
  }
  out = out.replace(/ +([，。；：、,.!?;:）\)】》」』])/g, '$1');
  out = out.replace(/([（\(【《「『]) +/g, '$1');
  out = out.replace(/ {2,}/g, ' ');
  return out;
}
