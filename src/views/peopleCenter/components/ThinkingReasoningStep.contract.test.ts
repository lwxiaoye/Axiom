import fs from 'node:fs';
import path from 'node:path';

const source = fs.readFileSync(path.resolve(__dirname, 'ThinkingReasoningStep.vue'), 'utf8');
const thoughtBodyStyle = source.match(/\.tr-p \{([\s\S]*?)\n\}/)?.[1] || '';

describe('ThinkingReasoningStep 动画契约', () => {
  it('思考正文用截图同款灰色段落字，完整展示、无渐隐裁切', () => {
    expect(source).toContain('<span class="tr-shimmer">Thinking</span>');
    expect(source).toContain('Thoughts<template v-if="elapsed"> for {{ elapsed }}</template>');
    expect(source).not.toContain('正在思考');
    expect(source).not.toContain('思考了');
    expect(source).toContain('class="tr-p"');
    expect(source).toContain('color: #999');
    expect(source).toContain('font-size: 15px');
    expect(thoughtBodyStyle).toContain('font-size: 15px');
    expect(thoughtBodyStyle).not.toContain('font-size: 16px');
    expect(source).toContain('font-weight: 400');
    expect(source).toContain('line-height: 1.55');
    expect(source).toContain('white-space: pre-wrap');
    expect(source).toContain('requestStreamAnimationFrame');
    expect(source).toContain('thoughtStreamCatchupStep');
    expect(source).toContain('is-live');
    expect(source).not.toContain('THOUGHT_MAX_H');
    expect(source).not.toContain('maskImage');
  });

  it('标题后跟执行步骤同款箭头：收起向右、展开向下，默认隐藏悬停才显示', () => {
    expect(source).toContain('PremiumChevron');
    expect(source).toContain(":direction=\"expanded ? 'down' : 'right'\"");
    expect(source.indexOf('tr-label')).toBeLessThan(source.indexOf('PremiumChevron'));
    expect(source).toContain('.tr-chevron');
    expect(source).toContain('opacity: 0;');
    expect(source).toContain('.tr-header:hover .tr-chevron');
    expect(source).toContain('.tr-header:focus-visible .tr-chevron');
  });

  it('收起的思考直接保全全文，不运行不可见的追赶动画', () => {
    expect(source).toContain('instant || !expanded.value');
    expect(source).toContain('watch(expanded, (open) =>');
    expect(source).toContain("if (!open) syncText(String(props.text || ''), true)");
    expect(source).toContain('watch(streaming, (live) =>');
  });
});
