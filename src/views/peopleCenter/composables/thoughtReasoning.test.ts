import {
  formatThoughtSeconds,
  splitThoughtParagraphs,
  splitThoughtSentences,
  thoughtStreamCatchupStep,
  thoughtStreamMetrics,
  THOUGHT_FADE,
  THOUGHT_MAX_H,
  THOUGHT_SENT_H,
} from './thoughtReasoning';

describe('splitThoughtSentences', () => {
  it('空正文不造占位句', () => {
    expect(splitThoughtSentences('')).toEqual([]);
    expect(splitThoughtSentences('   \n')).toEqual([]);
  });

  it('未结束的单句原样保留，供就地更新', () => {
    expect(splitThoughtSentences('正在核对方案')).toEqual(['正在核对方案']);
  });

  it('中文句号切开，最后半句单独成行', () => {
    expect(splitThoughtSentences('第一句。第二句。')).toEqual(['第一句。', '第二句。']);
    expect(splitThoughtSentences('第一句。未完成')).toEqual(['第一句。', '未完成']);
  });

  it('换行也切句，英文句号在空白处切开，小数点不切', () => {
    expect(splitThoughtSentences('Hello. World')).toEqual(['Hello.', 'World']);
    expect(splitThoughtSentences('a\nb')).toEqual(['a', 'b']);
    expect(splitThoughtSentences('use 1.2s delay。ok')).toEqual(['use 1.2s delay。', 'ok']);
  });
});

describe('splitThoughtParagraphs', () => {
  it('空正文不造段', () => {
    expect(splitThoughtParagraphs('')).toEqual([]);
    expect(splitThoughtParagraphs('  \n\n  ')).toEqual([]);
  });

  it('按空行切段，段内单换行保留', () => {
    expect(splitThoughtParagraphs('第一段\n\n第二段')).toEqual(['第一段', '第二段']);
    expect(splitThoughtParagraphs('a\nb\n\nc')).toEqual(['a\nb', 'c']);
  });
});

describe('thoughtStreamCatchupStep', () => {
  it('积压一次只追一小步，不会整段跳到最新', () => {
    expect(thoughtStreamCatchupStep(0)).toBe(0);
    expect(thoughtStreamCatchupStep(10)).toBeLessThan(10);
    expect(thoughtStreamCatchupStep(200)).toBe(24);
    expect(thoughtStreamCatchupStep(200, true)).toBe(200);
  });
});

describe('formatThoughtSeconds', () => {
  it('缺秒数或非正数在终态回空', () => {
    expect(formatThoughtSeconds(undefined)).toBe('');
    expect(formatThoughtSeconds(0)).toBe('');
  });

  it('终态按整数秒，进行中从 0s 起跳', () => {
    expect(formatThoughtSeconds(2.5)).toBe('3s');
    expect(formatThoughtSeconds(8)).toBe('8s');
    expect(formatThoughtSeconds(0, true)).toBe('0s');
    expect(formatThoughtSeconds(1.2, true)).toBe('1s');
  });
});

describe('thoughtStreamMetrics', () => {
  it('无句子时视口高度为 0，不撑开时间线', () => {
    const m = thoughtStreamMetrics(0, { done: false, open: true, fadeTop: false, fadeBottom: true });
    expect(m.viewH).toBe(0);
    expect(m.translate).toBe(0);
    expect(m.mask).toBe('none');
    expect(m.capped).toBe(false);
  });

  it('一句时视口刚好一句高', () => {
    const m = thoughtStreamMetrics(1, { done: false, open: true, fadeTop: false, fadeBottom: true });
    expect(m.viewH).toBe(THOUGHT_SENT_H);
    expect(m.capped).toBe(false);
  });

  it('超出 180px 后封顶并上卷，完成后展开改为原生滚动', () => {
    const thinking = thoughtStreamMetrics(5, { done: false, open: true, fadeTop: false, fadeBottom: true });
    expect(thinking.capped).toBe(true);
    expect(thinking.viewH).toBe(THOUGHT_MAX_H);
    expect(thinking.scrollable).toBe(false);
    expect(thinking.translate).toBe(THOUGHT_MAX_H - THOUGHT_FADE - thinking.contentH);
    expect(thinking.mask).toContain(`${THOUGHT_FADE}px`);

    const opened = thoughtStreamMetrics(5, { done: true, open: true, fadeTop: false, fadeBottom: true });
    expect(opened.scrollable).toBe(true);
    expect(opened.translate).toBe(0);
  });
});
