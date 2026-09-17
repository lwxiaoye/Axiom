/**
 * @jest-environment jsdom
 *
 * MarkdownViewer 消毒的行为回归：真调用 renderMarkdownHtml，不靠源码 grep。
 * 载荷只存在于本测试，不写入 Skill 文件或用户内容。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import showdown from 'showdown';
import { renderMarkdownHtml } from './renderMarkdownHtml';

function parseBody(html: string): HTMLElement {
  return new DOMParser().parseFromString(`<div id="root">${html}</div>`, 'text/html').body;
}

function eventHandlerAttrs(html: string): string[] {
  const found: string[] = [];
  parseBody(html).querySelectorAll('*').forEach((el) => {
    for (const attr of Array.from(el.attributes)) {
      if (/^on/i.test(attr.name)) found.push(`${el.tagName.toLowerCase()}[${attr.name}=${attr.value}]`);
    }
  });
  return found;
}

function uriValues(html: string, selector: string, attr: string): string[] {
  return Array.from(parseBody(html).querySelectorAll(selector)).map((el) => el.getAttribute(attr) || '');
}

describe('SEC-01 Showdown 默认放出原始 HTML', () => {
  it('未消毒时 img onerror 会进入 HTML', () => {
    const converter = new showdown.Converter();
    converter.setOption('tables', true);
    converter.setOption('emoji', true);
    const html = converter.makeHtml('<img src=x onerror=alert(1)>');
    expect(html.toLowerCase()).toContain('onerror');
    expect(eventHandlerAttrs(html).length).toBeGreaterThan(0);
  });
});

describe('renderMarkdownHtml：危险标记不能执行或逃逸', () => {
  it('剥掉 img onerror 和其它事件属性', () => {
    const html = renderMarkdownHtml('<img src=x onerror=alert(1)><div onclick="alert(2)">c</div>');
    expect(eventHandlerAttrs(html)).toEqual([]);
    expect(parseBody(html).querySelector('img')?.getAttribute('src')).toBe('x');
    expect(parseBody(html).textContent).toContain('c');
  });

  it('剥掉 javascript: / vbscript: / data:text HTML URL', () => {
    const html = renderMarkdownHtml(
      '[js](javascript:alert(1)) [vb](vbscript:msgbox(1)) [data](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==) [ok](https://example.com/docs)',
    );
    const hrefs = uriValues(html, 'a', 'href').map((h) => h.toLowerCase());
    expect(hrefs.some((h) => h.startsWith('javascript:'))).toBe(false);
    expect(hrefs.some((h) => h.startsWith('vbscript:'))).toBe(false);
    expect(hrefs.some((h) => h.startsWith('data:'))).toBe(false);
    expect(hrefs).toContain('https://example.com/docs');
  });

  it('拦截 <svg/onload 无空白写法及 SVG/MathML 容器', () => {
    const html = renderMarkdownHtml(
      '<svg/onload=alert(4)></svg><math><mi href="javascript:alert(1)">x</mi></math>',
    );
    const doc = parseBody(html);
    expect(doc.querySelectorAll('svg, math, mi').length).toBe(0);
    expect(eventHandlerAttrs(html)).toEqual([]);
  });

  it('剥掉 script/iframe/object/embed/base/form/style', () => {
    const html = renderMarkdownHtml(
      '<script>window.__pwn=1</script><iframe src="https://evil.example"></iframe><object data="x"></object><embed src="x"><base href="https://evil.example/"><form action="/x"><input name="a"></form><style>body{background:red}</style>',
    );
    const doc = parseBody(html);
    expect(doc.querySelectorAll('script, iframe, object, embed, base, form, input, style').length).toBe(0);
    expect(html.toLowerCase()).not.toContain('__pwn');
  });

  it('fenced code 中的攻击文本保持为文本，不升格为标签', () => {
    const html = renderMarkdownHtml('```\n<img src=x onerror=alert(1)>\n```');
    expect(eventHandlerAttrs(html)).toEqual([]);
    expect(parseBody(html).querySelectorAll('img').length).toBe(0);
    expect(parseBody(html).querySelector('code')?.textContent).toContain('<img src=x onerror=alert(1)>');
  });
});

describe('renderMarkdownHtml：普通 Markdown 仍可用', () => {
  it('保留链接、表格、代码和 http(s) 图片', () => {
    const src = [
      '[docs](https://example.com/path)',
      '',
      '| a | b |',
      '| --- | --- |',
      '| 1 | 2 |',
      '',
      'inline `code` and **bold**',
      '',
      '![pic](https://example.com/a.png)',
    ].join('\n');
    const html = renderMarkdownHtml(src);
    const doc = parseBody(html);

    const link = doc.querySelector('a');
    expect(link?.getAttribute('href')).toBe('https://example.com/path');
    expect(link?.getAttribute('rel')).toBe('noopener noreferrer');
    expect(link?.getAttribute('target')).toBe('_blank');
    expect(link?.textContent).toBe('docs');

    expect(doc.querySelectorAll('table').length).toBe(1);
    expect(doc.querySelector('td')?.textContent).toContain('1');
    expect(doc.querySelector('code')?.textContent).toBe('code');
    expect(doc.querySelector('strong, b')?.textContent).toBe('bold');
    expect(doc.querySelector('img')?.getAttribute('src')).toBe('https://example.com/a.png');
    expect(doc.querySelector('img')?.getAttribute('alt')).toBe('pic');
  });

  it('空输入返回空字符串', () => {
    expect(renderMarkdownHtml('')).toBe('');
  });
});

describe('MarkdownViewer 接线', () => {
  it('v-html 绑定消毒后的 HTML，而不是 Showdown 原文', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/components/Markdown/src/MarkdownViewer.vue'), 'utf8');
    expect(source).toContain("import { renderMarkdownHtml } from './renderMarkdownHtml'");
    expect(source).toContain('renderMarkdownHtml(props.value || \'\')');
    expect(source).not.toContain('converter.makeHtml');
    expect(source).toContain('v-html="getHtmlData"');
  });
});
