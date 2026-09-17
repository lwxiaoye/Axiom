/**
 * Markdown 文件渲染（2026-07-20 展览区支持 md）：markdown-it + xss 清洗,
 * 与主对话正文同源的安全策略（html:false 原生转义 + FilterXSS 白名单兜底）。
 * 不含 mermaid/产物占位等对话专属扩展——文件查看只需要干净的排版。
 */
import MarkdownIt from 'markdown-it';
import { FilterXSS, getDefaultWhiteList } from 'xss';
import { stopProtocolLinkAtCjkPunctuation } from './markdownLinkify';

const parser = new MarkdownIt({ html: false, linkify: true, breaks: true });
// 与主对话正文同策略：无协议裸文本不成链（「xxx.md」会被当 .md 域名），带协议 URL 保持可点。
parser.linkify.set({ fuzzyLink: false });
stopProtocolLinkAtCjkPunctuation(parser);
const defaultMarkdownWhiteList = getDefaultWhiteList();
const sanitizer = new FilterXSS({
  whiteList: {
    ...defaultMarkdownWhiteList,
    a: [...defaultMarkdownWhiteList.a, 'rel'],
  },
});

export function renderMarkdownDoc(src: string): string {
  return sanitizer.process(parser.render(src || ''));
}
