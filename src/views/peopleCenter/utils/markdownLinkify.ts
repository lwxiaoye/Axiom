import type MarkdownIt from 'markdown-it';

// linkify-it 把全角标点视为 URL fragment 的合法字符。例如 `https://x/#/），后文`
// 会被完整链接化，点击时后文被编码进地址。仅在协议裸链接上把中文标点作为明确边界。
const CJK_URL_DELIMITER_RE = /[，。；：！？、）】》」』〉〕｝]/u;

export function stopProtocolLinkAtCjkPunctuation(parser: MarkdownIt): void {
  const linkify = parser.linkify as typeof parser.linkify & {
    matchAtStart: (text: string) => any;
  };
  const originalMatchAtStart = linkify.matchAtStart.bind(linkify);

  linkify.matchAtStart = (text: string) => {
    const match = originalMatchAtStart(text);
    if (!match) return match;

    const raw = String(match.raw || text.slice(0, match.lastIndex));
    const delimiterIndex = raw.search(CJK_URL_DELIMITER_RE);
    if (delimiterIndex < 0) return match;

    const url = raw.slice(0, delimiterIndex);
    if (!url) return match;
    match.lastIndex = delimiterIndex;
    match.raw = url;
    match.text = url;
    match.url = url;
    return match;
  };

  const defaultLinkOpen = parser.renderer.rules.link_open;
  parser.renderer.rules.link_open = (tokens, index, options, env, self) => {
    tokens[index].attrSet('target', '_blank');
    tokens[index].attrSet('rel', 'noopener noreferrer');
    return defaultLinkOpen
      ? defaultLinkOpen(tokens, index, options, env, self)
      : self.renderToken(tokens, index, options);
  };
}
