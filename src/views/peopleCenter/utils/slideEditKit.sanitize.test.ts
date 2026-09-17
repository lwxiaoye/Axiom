/**
 * @jest-environment jsdom
 *
 * 幻灯片消毒的**行为**回归（2026-07-26）——真调用 sanitizeSlideHtml，不做源码 grep。
 *
 * 背景：`iframeSandbox.security.test.ts` 只能用正则确认 DocPagesViewer 的 liveHtml 计算属性
 * 「写了 sanitizeSlideHtml 这个名字」（.vue 不进 node 单测链路）。那种测法在下面这个 P0 存在时
 * 是全绿的：DOMPurify 3.4.x 的 mXSS 探针会把 CSS 注释里含 `<div>` / `<br/>` 的整块 `<style>`
 * fail-closed 删掉（实测某页 14321 → 664 字符），而本仓库 ppt-html 技能的 design-system.css
 * 头部通篇是 `/* DOM: <div class="…"> *\/` 这类注释 —— 于是**每一份** AI 生成的 PPT 进编辑帧
 * 就没样式，用户随手改一个字触发 harvest → editedPages → savePages，原 slides.json 被无样式
 * 版本永久覆盖。所以这里必须真跑消毒器，同时把「保版式」和「拦脚本」两侧一起钉死：
 * 只钉前者会诱使有人用 SAFE_FOR_XML:false 一把关掉 mXSS 防线换取样式保留。
 *
 * 环境：本文件用 docblock 单独切到 jsdom（jest.config.chat.cjs 默认 node），
 * 因为 DOMPurify 与 DOMParser 都要真实 DOM 才能运行。
 */
import { sanitizeSlideHtml, slideFrameSrc } from './slideEditKit';

/** 贴着 ppt-html 技能真实 design-system.css 的形状：注释里带 DOM 结构说明。 */
const DESIGN_CSS = `/* design-system.css —— 版式契约
 * 整页 DOM: <div class="slide">…</div>
 * 标题区 DOM: <h1 class="title">…</h1>，副标 <span class="sub">
 * 列表项内允许 <br/> 手动换行，禁止 </div> 之外的裸闭合
 */
:root{--brand:#10233f;--ink:#23272e}
.slide{width:1280px;height:720px;background:#fff;position:relative}
.title{font-size:52px;line-height:1.15;color:var(--brand)}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:24px}`;

const PPT_PAGE = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>节能减排</title>
<style>${DESIGN_CSS}</style></head>
<body><div class="slide"><h1 class="title">节能减排</h1>
<div class="grid"><div>一</div><div>二</div><div>三</div></div></div></body></html>`;

/** 模拟编辑帧的收割：帧内把 srcdoc 解析成文档，harvest() 再 `'<!DOCTYPE html>'+documentElement.outerHTML` 吐回。 */
function harvestLike(html: string): string {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  return `<!DOCTYPE html>${doc.documentElement.outerHTML}`;
}

describe('sanitizeSlideHtml：保住版式 CSS', () => {
  it('CSS 注释里含 <div>/<br/>/</div> 时，<style> 整块必须留下（P0 回归：曾被 DOMPurify 探针整块吞掉）', () => {
    const clean = sanitizeSlideHtml(PPT_PAGE);

    expect(clean).toMatch(/<style[\s>]/i);
    // 关键规则逐条在场——只断言「有 style 标签」不够，占位符没写回时也会有个空 style。
    expect(clean).toContain('.slide{width:1280px;height:720px;background:#fff;position:relative}');
    expect(clean).toContain('.title{font-size:52px;line-height:1.15;color:var(--brand)}');
    expect(clean).toContain('.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:24px}');
    expect(clean).toContain('--brand:#10233f');
    // 注释原文也原样保留（写回的是 textContent 原文，不是重新序列化的 CSSOM）
    expect(clean).toContain('整页 DOM: <div class="slide">');
    // 正文与 DOCTYPE 都在
    expect(clean).toContain('<h1 class="title">节能减排</h1>');
    expect(clean).toMatch(/^<!DOCTYPE html>/i);
    // 塌缩兜底：修复前 14321 → 664，这里守住「消毒后不该只剩零头」
    expect(clean.length).toBeGreaterThan(PPT_PAGE.length * 0.8);
  });

  it('多个 <style> 各自写回自己的内容，不串台', () => {
    const clean = sanitizeSlideHtml(
      `<!DOCTYPE html><html><head><style>/* <div> */ .a{color:red}</style>` +
        `<style>/* <span> */ .b{color:blue}</style></head><body><p>x</p></body></html>`
    );
    expect(clean).toContain('/* <div> */ .a{color:red}');
    expect(clean).toContain('/* <span> */ .b{color:blue}');
    expect(clean.indexOf('.a{color:red}')).toBeLessThan(clean.indexOf('.b{color:blue}'));
  });

  it('不含标记样子子串的普通 CSS 走朴素路径，同样保留', () => {
    const clean = sanitizeSlideHtml('<html><head><style>.slide{width:1280px}</style></head><body><p>x</p></body></html>');
    expect(clean).toContain('.slide{width:1280px}');
  });

  it('slideFrameSrc（缩略图/放映帧）同样保住版式，并追加 1280×720 复位样式', () => {
    const src = slideFrameSrc(PPT_PAGE);
    expect(src).toContain('.title{font-size:52px;line-height:1.15;color:var(--brand)}');
    expect(src).toContain('width:1280px;height:720px;overflow:hidden');
  });

  it('空输入原样返回，无 style 的页面正常消毒', () => {
    expect(sanitizeSlideHtml('')).toBe('');
    expect(sanitizeSlideHtml('<p>hi</p>')).toContain('<p>hi</p>');
  });
});

describe('sanitizeSlideHtml：脚本面必须依旧被拦（同源+allow-scripts 帧，泄 token 即账号接管）', () => {
  const EVIL = `<!DOCTYPE html><html><head><style>/* <div> */ .a{color:red}</style></head><body>
<script>window.__pwn=1</script>
<img src="x" onerror="alert(1)">
<div onclick="alert(2)">c</div>
<a href="javascript:alert(3)">go</a>
<svg/onload=alert(4)></svg>
<iframe src="https://evil.example"></iframe>
<object data="x"></object><embed src="x">
<base href="https://evil.example/">
<form action="/x"><input name="a"></form>
</body></html>`;
  const clean = sanitizeSlideHtml(EVIL);

  it('<script> 标签被剥掉', () => {
    expect(clean).not.toMatch(/<script/i);
    expect(clean).not.toContain('__pwn');
  });

  it('on* 事件属性被剥掉（含 <svg/onload= 这类无空白分隔写法）', () => {
    expect(clean).not.toMatch(/onerror/i);
    expect(clean).not.toMatch(/onclick/i);
    expect(clean).not.toMatch(/onload/i);
  });

  it('javascript: URI 被剥掉', () => {
    expect(clean).not.toMatch(/javascript:/i);
  });

  it('iframe/object/embed/base/form 等危险容器被剥掉', () => {
    expect(clean).not.toMatch(/<iframe/i);
    expect(clean).not.toMatch(/<object/i);
    expect(clean).not.toMatch(/<embed/i);
    expect(clean).not.toMatch(/<base/i);
    expect(clean).not.toMatch(/<form/i);
  });

  it('拦截的同时版式 CSS 仍在（证明保版式不是靠整体放行换来的）', () => {
    expect(clean).toContain('.a{color:red}');
  });

  it('SAFE_FOR_XML 必须保持开启：<svg><style> 命名空间混淆的 mXSS 突破载荷不得出现在返回串里', () => {
    // 这是 SAFE_FOR_XML:false（报告里的方案 b）会放行的载荷——实测关掉后 title 属性原样输出，
    // 浏览器再解析时 `</style>` 提前闭合，`<img onerror>` 就活了。此用例即该方案的否决闸。
    const out = sanitizeSlideHtml(
      `<style>/* <div> */.ok{color:green}</style>` + `<svg><style><a title="</style><img src=x onerror=alert(1)>"></style></svg>`
    );
    expect(out).not.toMatch(/onerror/i);
    expect(out).not.toContain('</style><img');
    expect(out).toContain('.ok{color:green}'); // 正常 style 不受牵连
  });

  it('CSS 文本内的 </style 会被中和，杜绝写回后序列化越界', () => {
    // 走 srcdoc 解析时 tokenizer 本就截断，这里直接把带 </style 的文本喂给 style 节点做纵深防御验证。
    const doc = new DOMParser().parseFromString('<html><head><style></style></head><body></body></html>', 'text/html');
    const styleEl = doc.querySelector('style') as HTMLStyleElement;
    styleEl.textContent = '/* <div> */ .a::after{content:"</style><img src=x onerror=alert(1)>"}';
    const out = sanitizeSlideHtml(`<!DOCTYPE html>${doc.documentElement.outerHTML}`);
    expect(out).not.toMatch(/onerror=alert/i);
    expect(out).not.toContain('</style><img');
  });
});

describe('harvest 往返：改一个字不该把样式改没', () => {
  it('sanitize → 帧内解析/收割 → 再 sanitize，CSS 一路不丢', () => {
    const first = sanitizeSlideHtml(PPT_PAGE);
    const harvested = harvestLike(first); // 用户在帧内改动后 harvest() 的等价输出
    expect(harvested).toContain('.slide{width:1280px;height:720px;background:#fff;position:relative}');

    // savePages 落盘后再次打开查看器，会对收割结果再消毒一次
    const second = sanitizeSlideHtml(harvested);
    expect(second).toContain('.slide{width:1280px;height:720px;background:#fff;position:relative}');
    expect(second).toContain('.title{font-size:52px;line-height:1.15;color:var(--brand)}');
    expect(second).toContain('.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:24px}');
  });

  it('反复往返三次仍不侵蚀（幂等）', () => {
    let html = sanitizeSlideHtml(PPT_PAGE);
    for (let i = 0; i < 3; i += 1) html = sanitizeSlideHtml(harvestLike(html));
    expect(html).toContain('.title{font-size:52px;line-height:1.15;color:var(--brand)}');
    expect(html).toContain('整页 DOM: <div class="slide">');
    expect(html).toContain('<h1 class="title">节能减排</h1>');
  });
});
