import MarkdownIt from 'markdown-it';
import { FilterXSS, getDefaultWhiteList } from 'xss';
import { stopProtocolLinkAtCjkPunctuation } from './markdownLinkify';

describe('protocol link rendering', () => {
  it('does not consume CJK punctuation and following prose into a bare URL', () => {
    const parser = new MarkdownIt({ linkify: true, breaks: true });
    parser.linkify.set({ fuzzyLink: false });
    stopProtocolLinkAtCjkPunctuation(parser);

    const html = parser.render('（https://sls.cdb.com.cn/#/），完成注册并填写本人信息。');

    expect(html).toContain('<a href="https://sls.cdb.com.cn/#/" target="_blank" rel="noopener noreferrer">https://sls.cdb.com.cn/#/</a>），完成注册并填写本人信息。');
    expect(html).not.toContain('%EF%BC%89');

    const defaults = getDefaultWhiteList();
    const safeHtml = new FilterXSS({
      whiteList: { ...defaults, a: [...defaults.a, 'rel'] },
    }).process(html);
    expect(safeHtml).toContain('target="_blank" rel="noopener noreferrer"');
  });
});
