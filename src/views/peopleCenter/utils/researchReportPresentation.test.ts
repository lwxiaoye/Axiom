/** @jest-environment jsdom */
import { presentResearchReport } from './researchReportPresentation';

test('保留连续报告章节和表格，正文链接化为普通文本，来源列表放在外部入口', () => {
  const body = '<h2>核心发现</h2><p>参照<a href="https://example.com">官方说明</a><a class="cite-chip" href="#ref-7">7</a>。</p>'
    + '<table><tr><th>方案</th><th>边界</th></tr><tr><td>A</td><td>条件</td></tr></table>'
    + '<h2>参考来源</h2><ol><li><a href="https://example.com">网址</a></li></ol>'
    + '<h2>结论</h2><p>保留结论。</p>';
  const rendered = presentResearchReport(body);
  const doc = new DOMParser().parseFromString(rendered, 'text/html');
  expect(doc.querySelector('a, details')).toBeNull();
  expect(doc.querySelector('sup')?.textContent).toBe('7');
  expect(doc.querySelector('p')?.textContent).toBe('参照官方说明7。');
  expect(doc.querySelector('.research-table-scroll table')).not.toBeNull();
  expect(doc.body.textContent).not.toContain('参考来源');
  expect(doc.body.lastElementChild?.textContent).toBe('保留结论。');
  expect(presentResearchReport(rendered)).toBe(rendered);
});

test('旧版超时报告不再堆放原文，但保留未核验提示和缺口', () => {
  const body = '<p>报告生成或核验未能在本轮时间预算内完成。</p>'
    + '<h2>已取得的材料</h2><h3>来源一</h3><blockquote>登录 注册 导航及长篇转载</blockquote>'
    + '<h2>尚待解决的问题</h2><p>仍需核验边界。</p>';
  const doc = new DOMParser().parseFromString(presentResearchReport(body), 'text/html');
  expect(doc.body.textContent).toContain('未能在本轮时间预算内完成');
  expect(doc.body.textContent).toContain('仍需核验边界');
  expect(doc.body.textContent).not.toContain('登录 注册');
});

test('已净化的来源文字不会被重新解释成 HTML', () => {
  const body = '<p>&lt;img src=x onerror=alert(1)&gt;</p><p><a href="https://example.com">https://example.com</a></p>';
  const doc = new DOMParser().parseFromString(presentResearchReport(body), 'text/html');
  expect(doc.querySelector('script, img, a')).toBeNull();
  expect(doc.querySelectorAll('p')).toHaveLength(1);
  expect(doc.body.textContent).toContain('<img');
});
