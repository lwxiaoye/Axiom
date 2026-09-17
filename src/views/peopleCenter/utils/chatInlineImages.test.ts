/** @jest-environment jsdom */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { isRenderableChatImageUrl, renderChatImageFigure, renderMissingChatImage, retryChatImage, setChatImageLoadState } from './chatInlineImages';

describe('正文图片在窄屏、失败和历史缺引用时的展示', () => {
  afterEach(() => { document.body.replaceChildren(); });

  it('保留真实来源并提供键盘可用的放大和重试按钮', () => {
    document.body.innerHTML = renderChatImageFigure({
      url: '/upload/campus/map.png', title: '校区地图', source: 'https://school.example/map',
    });
    expect(document.querySelector('img')?.getAttribute('src')).toBe('/upload/campus/map.png');
    expect(document.querySelector('.msg-image-open')?.tagName).toBe('BUTTON');
    expect(document.querySelector('.msg-image-open')?.getAttribute('aria-label')).toBe('放大查看：校区地图');
    expect(document.querySelector('.msg-image-retry')?.textContent).toBe('重新加载');
    expect(document.querySelector('figcaption a')?.getAttribute('href')).toBe('https://school.example/map');
  });

  it('加载失败不隐藏整张卡，重试保持签名地址不变，成功后清除失败态', () => {
    const src = 'https://school.example/map.png?signature=a%2Bb&expires=123';
    document.body.innerHTML = renderChatImageFigure({ url: src, title: '校区地图' });
    document.body.addEventListener('error', (event) => setChatImageLoadState(event, true), { once: true, capture: true });
    const image = document.querySelector('img')!;
    const figure = document.querySelector('figure')!;
    image.dispatchEvent(new Event('error'));
    expect(figure.classList.contains('is-image-error')).toBe(true);
    expect(figure.style.display).not.toBe('none');
    expect(retryChatImage(document.querySelector<HTMLElement>('.msg-image-retry')!)).toBe(true);
    expect(image.getAttribute('src')).toBe(src);
    expect(image.loading).toBe('eager');
    expect(figure.classList.contains('is-image-error')).toBe(false);
    figure.classList.add('is-image-error');
    document.body.addEventListener('load', (event) => setChatImageLoadState(event, false), { once: true, capture: true });
    image.dispatchEvent(new Event('load'));
    expect(figure.classList.contains('is-image-error')).toBe(false);
  });

  it('历史缺引用如实说明，不猜链接；不允许 data/javascript/本地文件协议', () => {
    expect(renderMissingChatImage(2)).toContain('图片 2 的历史引用暂不可用');
    expect(renderMissingChatImage(2)).not.toContain('<img');
    expect(renderMissingChatImage(-1)).toBe('');
    for (const url of ['javascript:alert(1)', 'data:image/svg+xml,a', 'file:///tmp/a.png', '/private/a.png']) {
      expect(isRenderableChatImageUrl(url)).toBe(false);
      expect(renderChatImageFigure({ url })).toBe('');
    }
  });

  it('引用内容和图片标题不能注入 HTML 或不安全来源', () => {
    const html = renderChatImageFigure({
      url: 'https://school.example/map.png', title: '\"><script>alert(1)</script>', source: 'javascript:alert(1)',
    });
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('href="javascript:');
    expect(html).toContain('&lt;script&gt;');
  });

  it('校园独立图块保持完整比例，普通主对话保留首图浮动', () => {
    expect(renderChatImageFigure({ url: '/upload/a.png' })).not.toContain('msg-figure-float');
    expect(renderChatImageFigure({ url: '/upload/a.png' }, true)).toContain('msg-figure-float');
    const source = readFileSync(resolve(__dirname, '../components/MessageList.vue'), 'utf8');
    expect(source).toContain("props.answerLayout !== 'campus' && !hasFloatImage");
    expect(source).toMatch(/\.campus-answer-layout[^{}]*figure\.msg-figure img\)[^{]*\{[^}]*height: auto;[^}]*max-height: none;[^}]*object-fit: contain;/);
    expect(source).toContain('max-width: min(100%, 560px)');
    const mapping = source.slice(source.indexOf('function messageImages('), source.indexOf('function faviconList('));
    expect(mapping).not.toContain('isRenderableChatImageUrl');
    expect(mapping).toContain("filter((c) => c.type === 'image')");
  });
});
