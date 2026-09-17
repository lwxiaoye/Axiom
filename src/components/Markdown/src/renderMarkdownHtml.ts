/**
 * MarkdownViewer 只读渲染：Showdown 默认放出 Markdown 中的原始 HTML。
 * 必须对转换后的最终 HTML 消毒，不能先清洗 Markdown 再转换（转换仍会生成标签）。
 *
 * 使用独立 DOMPurify 实例 + 明确白名单，不复用：
 * - slideEditKit：允许 style/data-* 和整页文档，面向同源 srcdoc 编辑帧
 * - js-xss：非浏览器 HTML 解析器，SVG/MathML 不在其保证范围
 */
import DOMPurify from 'dompurify';
import showdown from 'showdown';

const converter = new showdown.Converter();
converter.setOption('tables', true);
converter.setOption('emoji', true);

const HTML_NS = 'http://www.w3.org/1999/xhtml';

const MARKDOWN_ALLOWED_TAGS = [
  'p',
  'br',
  'hr',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'ul',
  'ol',
  'li',
  'blockquote',
  'pre',
  'code',
  'em',
  'strong',
  'del',
  's',
  'i',
  'b',
  'u',
  'a',
  'img',
  'table',
  'thead',
  'tbody',
  'tfoot',
  'tr',
  'th',
  'td',
  'caption',
  'colgroup',
  'col',
  'div',
  'span',
  'sub',
  'sup',
  'dl',
  'dt',
  'dd',
  'kbd',
  'samp',
  'var',
  'mark',
  'small',
  'abbr',
  'cite',
  'q',
];

const MARKDOWN_ALLOWED_ATTR = [
  'href',
  'title',
  'alt',
  'src',
  'class',
  'target',
  'rel',
  'colspan',
  'rowspan',
  'align',
  'start',
  'width',
  'height',
];

const MARKDOWN_FORBID_TAGS = [
  'script',
  'style',
  'iframe',
  'object',
  'embed',
  'applet',
  'form',
  'input',
  'textarea',
  'button',
  'select',
  'option',
  'svg',
  'math',
  'link',
  'meta',
  'base',
  'template',
  'noscript',
  'foreignobject',
  'animate',
  'set',
  'video',
  'audio',
  'source',
  'track',
  'canvas',
];

const MARKDOWN_SANITIZE_CONFIG = {
  ALLOWED_TAGS: MARKDOWN_ALLOWED_TAGS,
  ALLOWED_ATTR: MARKDOWN_ALLOWED_ATTR,
  ALLOWED_NAMESPACES: [HTML_NS],
  ALLOW_DATA_ATTR: false,
  ALLOW_UNKNOWN_PROTOCOLS: false,
  DATA_URI_TAGS: ['img'],
  FORBID_TAGS: MARKDOWN_FORBID_TAGS,
};

let markdownPurify: ReturnType<typeof DOMPurify> | null = null;

function getMarkdownPurify(): ReturnType<typeof DOMPurify> {
  if (!markdownPurify) {
    markdownPurify = DOMPurify(window);
    markdownPurify.addHook('afterSanitizeAttributes', (node) => {
      if (node.nodeName !== 'A' || !node.hasAttribute('href')) return;
      node.setAttribute('rel', 'noopener noreferrer');
      node.setAttribute('target', '_blank');
    });
  }
  return markdownPurify;
}

export function renderMarkdownHtml(markdown: string): string {
  const raw = converter.makeHtml(markdown || '');
  if (!raw) return '';
  // 无 DOM 时不能安全消毒：宁可不渲染，也不把 Showdown 原文交给 v-html。
  if (typeof window === 'undefined') return '';
  return getMarkdownPurify().sanitize(raw, MARKDOWN_SANITIZE_CONFIG);
}
