import { stripInlineSourceMarkers } from './stripInlineSourceMarkers';

describe('stripInlineSourceMarkers', () => {
  it('removes [N] chips and keeps prose readable', () => {
    const raw =
      '结论先说：英雄联盟值得尝试[3]，尤其 2026 赛季[1]。核心优点[7][8]。';
    const out = stripInlineSourceMarkers(raw);
    expect(out).not.toMatch(/\[\d+\]/);
    expect(out).toContain('值得尝试');
    expect(out).toContain('2026 赛季');
    expect(out).toContain('核心优点');
  });

  it('does not touch image markers', () => {
    expect(stripInlineSourceMarkers('见图 [图1] 与正文[2]。')).toBe('见图 [图1] 与正文。');
  });

  it('trims space before Chinese punctuation left by chips', () => {
    expect(stripInlineSourceMarkers('版本 [3] 。')).toBe('版本。');
  });

  it('strips knowledge-style markers', () => {
    const out = stripInlineSourceMarkers('依据[资料1]与[2]可得结论。');
    expect(out).not.toContain('[资料1]');
    expect(out).not.toContain('[2]');
    expect(out).toContain('依据');
  });
});
