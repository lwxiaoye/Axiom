import { extractArtifactTitle, hashArtifact, preprocessArtifacts, unwrapInnerFence } from './artifactParser';

const HTML = '<!DOCTYPE html>\n<html><head><title>报表</title></head><body>hi</body></html>';

describe('preprocessArtifacts（:::artifact 围栏归一）', () => {
  it('完整 HTML 围栏 → artifact-html 代码栅栏，源码原样保留', () => {
    const out = preprocessArtifacts(`前言\n:::artifact{type="text/html"}\n${HTML}\n:::\n后记`);
    expect(out).toContain('```artifact-html');
    expect(out).toContain(HTML);
    expect(out).toContain('前言');
    expect(out).toContain('后记');
  });

  it('mermaid 围栏 → mermaid 代码栅栏', () => {
    const out = preprocessArtifacts(':::artifact{type="application/vnd.mermaid"}\ngraph TD;A-->B\n:::');
    expect(out).toContain('```mermaid');
    expect(out).toContain('graph TD;A-->B');
  });

  it('流式未闭合的 HTML 围栏 → 流式源码块（直接看到代码在写），围栏闭合后才升级产物卡', () => {
    const out = preprocessArtifacts(':::artifact{type="text/html"}\n<!DOCTYPE html>\n<html><body>写到一半');
    expect(out).toContain('```artifact-streaming');
    expect(out).toContain('写到一半');
  });

  it('漏了结尾 ::: 但 HTML 已完整（有 </html>）→ 仍升级为产物卡', () => {
    const out = preprocessArtifacts(`:::artifact{type="text/html"}\n${HTML}`);
    expect(out).toContain('```artifact-html');
    expect(out).not.toContain('artifact-streaming');
  });

  it('模型自套的内层 ```html 围栏被剥掉，不截断外层围栏', () => {
    const out = preprocessArtifacts(`:::artifact{type="text/html"}\n\`\`\`html\n${HTML}\n\`\`\`\n:::\n设计思路说明`);
    expect(out).toContain('```artifact-html');
    expect(out).not.toContain('```html\n<!DOCTYPE');
    expect(out).toContain('设计思路说明');
  });

  it('多个产物围栏逐一归一', () => {
    const out = preprocessArtifacts(
      `:::artifact{type="text/html"}\n${HTML}\n:::\n中间\n:::artifact{type="text/html"}\n${HTML.replace('报表', '图表')}\n:::`,
    );
    expect(out.match(/```artifact-html/g)).toHaveLength(2);
  });

  it('普通代码块与正文原样保留，未知 type 只留内容', () => {
    const plain = '开头\n```python\nprint(1)\n```\n结尾';
    expect(preprocessArtifacts(plain)).toBe(plain);
    const out = preprocessArtifacts(':::artifact{type="text/plain"}\n纯文本\n:::');
    expect(out).toContain('纯文本');
    expect(out).not.toContain(':::artifact');
  });
});

describe('unwrapInnerFence', () => {
  it('裸源码不动；带围栏剥壳；流式未闭合尾围栏也能处理', () => {
    expect(unwrapInnerFence('<html></html>')).toBe('<html></html>');
    expect(unwrapInnerFence('```html\n<p>a</p>\n```')).toBe('<p>a</p>');
    expect(unwrapInnerFence('```html\n<p>还没写完')).toBe('<p>还没写完');
  });
});

describe('extractArtifactTitle', () => {
  it('<title> 优先，其次首个 <h1>，否则默认；超长截断', () => {
    expect(extractArtifactTitle(HTML)).toBe('报表');
    expect(extractArtifactTitle('<html><body><h1>年度 <b>总结</b></h1></body></html>')).toBe('年度 总结');
    expect(extractArtifactTitle('<html><body>无标题</body></html>')).toBe('HTML 页面');
    expect(extractArtifactTitle(`<title>${'长'.repeat(50)}</title>`)).toHaveLength(41); // 40 + 省略号
  });
});

describe('hashArtifact（产物 id：内容哈希，跨重扫稳定）', () => {
  it('同内容同 id、异内容异 id', () => {
    expect(hashArtifact(HTML)).toBe(hashArtifact(HTML));
    expect(hashArtifact(HTML)).not.toBe(hashArtifact(HTML + ' '));
    expect(hashArtifact(HTML)).toMatch(/^art[0-9a-z]+$/);
  });
});
