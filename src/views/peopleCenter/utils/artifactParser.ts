/**
 * 产物围栏解析（纯 TS，无 DOM/markdown-it 依赖）：把模型输出里的
 * `:::artifact{type=...}` 围栏归一成内部代码栅栏语言，供 MessageList 的
 * highlight 占位 + 后处理消费；区分「已完整（产物卡）/流式未完成（进度卡）/
 * mermaid 图/普通代码块」。独立成模块以便对不完整围栏、多产物等场景做单测。
 */

/**
 * 模型常在 :::artifact 围栏里又习惯性套一层 ```html ... ``` 代码围栏。若原样再包成
 * ```artifact-html，内层 ``` 会把外层围栏提前截断——导致 ①「```html」漏进 iframe 源码，
 * ②多出的孤儿 ``` 把后续正文（如「设计思路说明」）整段吞成一个 text 代码块。
 * 这里先剥掉模型自套的外层代码围栏，只留纯 HTML/图源码。
 */
export function unwrapInnerFence(body: string): string {
  const trimmed = body.replace(/^\s+/, '');
  if (!/^```/.test(trimmed)) return body; // 规规矩矩放了裸源码，无需处理
  return trimmed
    .replace(/^```[a-zA-Z0-9-]*[ \t]*\r?\n/, '') // 去开头的 ```lang 行
    .replace(/\r?\n```[ \t]*\s*$/, ''); // 去结尾的 ``` 行（流式未闭合时本就没有，不影响）
}

/**
 * 把 `:::artifact{type=...}\n<body>\n:::` 围栏转成代码栅栏，复用 highlight 占位 + 后处理：
 * text/html → 沙箱 iframe，application/vnd.mermaid → mermaid 图（ADR-039）。未知 type 原样。
 */
export function preprocessArtifacts(content: string): string {
  // 1) 完整围栏 :::artifact{...}\n...\n::: → 产物卡片 / 图
  let out = content.replace(/:::artifact\{([^}]*)\}\r?\n([\s\S]*?)\r?\n:::/g, (_m, attrs, body) => {
    const clean = unwrapInnerFence(body);
    if (/vnd\.mermaid/.test(attrs)) return `\n\`\`\`mermaid\n${clean}\n\`\`\`\n`;
    if (/text\/html/.test(attrs)) return `\n\`\`\`artifact-html\n${clean}\n\`\`\`\n`;
    return `\n${clean}\n`;
  });
  // 2) 尾部未闭合围栏（流式中，或小模型漏了结尾 :::）：绝不显示成裸文本
  out = out.replace(/:::artifact\{([^}]*)\}\r?\n([\s\S]*)$/, (_m, attrs, body) => {
    const clean = unwrapInnerFence(body);
    const isHtml = /text\/html/.test(attrs);
    // HTML 已完整（有 </html>）但模型漏了结尾 ::: → 仍当作产物卡片
    if (isHtml && /<\/html\s*>/i.test(clean)) return `\n\`\`\`artifact-html\n${clean}\n\`\`\`\n`;
    // HTML 流式未完成：直接流式展示正在编写的源码（2026-07-13 用户拍板，对齐裸 ```html
    // 路径的"看到代码在写"体验）；刷屏/抖动由 artifact-streaming-block 的限高黏底样式化解，
    // 围栏闭合后升级为产物卡片。
    if (isHtml) return `\n\`\`\`artifact-streaming\n${clean}\n\`\`\`\n`;
    // mermaid 图很小，仍放代码框，完成后由 renderMermaid 渲染
    return `\n\`\`\`mermaid\n${clean}\n\`\`\`\n`;
  });
  return out;
}

/** 消息流里识别出的产物（HTML 页面 / 文本源码预览）——全屏文档查看器（DocPagesViewer 网页/源码模式）消费 */
export interface Artifact {
  id: string;
  type: 'html' | 'code' | 'mermaid';
  title: string;
  html?: string;
  code?: string;
  lang?: string;
}

/** 当前产物对应的「我的文件」记录（产物流收尾自动保存后回填；无=未保存/历史会话） */
export interface ArtifactSavedFile {
  id: string;
  filename: string;
  versionNo?: number;
}

/** 从 HTML 里取一个标题：<title> → 首个 <h1> → 默认。 */
export function extractArtifactTitle(html: string): string {
  const raw =
    html.match(/<title[^>]*>([^<]+)<\/title>/i)?.[1] ||
    html.match(/<h1[^>]*>([\s\S]*?)<\/h1>/i)?.[1] ||
    '';
  const clean = raw.replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
  if (!clean) return 'HTML 页面';
  return clean.length > 40 ? `${clean.slice(0, 40)}…` : clean;
}

/** 产物 id：内容哈希，跨重扫稳定（同内容重渲染不算新产物，防重复自动打开） */
export function hashArtifact(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) | 0;
  return `art${(h >>> 0).toString(36)}`;
}
