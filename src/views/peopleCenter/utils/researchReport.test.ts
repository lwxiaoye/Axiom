/**
 * @jest-environment jsdom
 */
import {
  countResearchSearches,
  isResearchReportFile,
  isResearchReportHtml,
  isResearchTurn,
  looksLikeResearchMarkdown,
  linkResearchCites,
  parseResearchReport,
  reportFilenameStem,
  researchCompletionStats,
  researchStructureMarkdown,
  researchStructureTitle,
  sanitizeResearchTitle,
  stripLeadingTitleHeadings,
  stripResearchScaffold,
} from './researchReport';

describe('researchReport helpers', () => {
  it('identifies compiled report HTML by data-kind', () => {
    expect(isResearchReportHtml('<body class="research-report" data-kind="research-report">')).toBe(true);
    expect(isResearchReportHtml('<html><body>普通网页</body></html>')).toBe(false);
    expect(isResearchReportHtml('')).toBe(false);
  });

  it('only treats source/origin=research as a research file, not every HTML', () => {
    expect(isResearchReportFile({ filename: '竞品.html', source: 'research' })).toBe(true);
    expect(isResearchReportFile({ filename: '竞品.html', origin: { tool: 'research' } })).toBe(true);
    expect(isResearchReportFile({ filename: 'landing.html', source: 'generated' })).toBe(false);
    expect(isResearchReportFile({ filename: '竞品.research.md', source: 'research' })).toBe(true);
  });

  it('parses a continuous ChatGPT-style article, not cover/toc pages', () => {
    const html = `
      <!DOCTYPE html><html><head><title>网页端 Agent 计划模式</title></head>
      <body class="research-report" data-kind="research-report">
        <article class="research-article" data-kind="research-report">
          <h1>网页端 Agent「计划模式」深度研究报告</h1>
          <h2 id="exec">执行摘要</h2>
          <p>核心结论是 Plan Mode。<a class="cite" href="#ref-1">1</a></p>
          <h2 id="def">定义</h2>
          <p>第二段。</p>
        </article>
        <script type="text/plain" id="research-markdown"># title</script>
      </body></html>
    `;
    const parsed = parseResearchReport(html);
    expect(parsed).not.toBeNull();
    expect(parsed?.title).toContain('计划模式');
    expect(parsed?.articleHtml).toContain('执行摘要');
    expect(parsed?.pages.map((page) => page.title)).toEqual(['执行摘要', '定义']);
    expect(parsed?.markdown).toBe('# title');
  });

  it('recovers a false-delivery scrub that flattened the report markdown', () => {
    const raw = [
      '文件还没有成功写入「我的文件」，目前没有可下载的交付文档。生成过程可能中断或只完成了中间步骤，本轮未能完成交付。',
      ' 已整理的内容要点：所有步骤的搜索证据已完备。现在交付最终研究报告。 --- ',
      '# 2026年折叠屏手机选购研究报告 ## 执行摘要 铰链耐用是第一门槛。',
    ].join('');
    const cleaned = stripResearchScaffold(raw);
    expect(cleaned.startsWith('# 2026年折叠屏')).toBe(true);
    expect(cleaned).toContain('\n## 执行摘要');
    expect(cleaned).not.toContain('文件还没有成功写入');
    expect(sanitizeResearchTitle('文件还没有成功写入「我的文件」.html')).toBe('研究报告');
    expect(
      sanitizeResearchTitle('文件还没有成功写入「我的文件」 --- # 2026年折叠屏手机选购研究报告'),
    ).toBe('2026年折叠屏手机选购研究报告');
    expect(sanitizeResearchTitle('2026年折叠屏手机选购研究报告')).toBe('2026年折叠屏手机选购研究报告');
  });

  it('strips synthesis scaffolding before the first heading', () => {
    const raw = [
      '已掌握足够多角度的独立来源，现在合成完整研究报告。',
      '---',
      '# 斯蒂芬·库里生涯数据深度调研报告',
      '',
      '## 执行摘要',
      '库里改变了联盟。',
    ].join('\n');
    const cleaned = stripResearchScaffold(raw);
    expect(cleaned.startsWith('# 斯蒂芬·库里')).toBe(true);
    expect(cleaned).not.toContain('现在合成完整研究报告');
    expect(stripResearchScaffold('# 已有标题\n正文')).toBe('# 已有标题\n正文');
  });

  it('unflattens live glued headings without spaced --- / ##', () => {
    const curry = [
      '已掌握足够多角度的独立来源，现在合成完整研究报告。',
      '---# 斯蒂芬·库里（Stephen Curry）生涯数据深度调研报告',
      '## 执行摘要斯蒂芬·库里改变了联盟。',
    ].join('');
    const cleaned = stripResearchScaffold(curry);
    expect(cleaned.startsWith('# 斯蒂芬·库里')).toBe(true);
    expect(cleaned).toContain('\n## 执行摘要\n');
    expect(cleaned).not.toContain('现在合成完整研究报告');
    expect(isResearchTurn({ content: curry })).toBe(false);
    expect(isResearchTurn({ agentMode: 'research', content: curry })).toBe(true);
    expect(researchStructureTitle(researchStructureMarkdown({ content: curry }))).toContain('斯蒂芬·库里');

    const stopped = [
      '# Kimi K3 能力边界',
      '',
      '## 核心发现',
      'K3 仍处早期评测。',
      '',
      '## 参考来源',
      '- 公开报道',
    ].join('\n');
    expect(looksLikeResearchMarkdown(stopped)).toBe(true);
    expect(researchStructureMarkdown({ content: stopped })).toContain('Kimi K3');

    const kimi = '# Kimi K3 调研报告**月之暗面旗舰**---## 执行摘要Kimi K3 是旗舰模型。';
    const kimiCleaned = stripResearchScaffold(kimi);
    expect(kimiCleaned.startsWith('# Kimi K3 调研报告')).toBe(true);
    expect(kimiCleaned).toContain('\n## 执行摘要\n');
  });

  it('turns [n] markers into cite chips and recognizes research turns', () => {
    expect(linkResearchCites('场均 24.6 分[1]，三分命中率[12]。')).toBe(
      '场均 24.6 分<a class="cite-chip" href="#ref-1">1</a>，三分命中率<a class="cite-chip" href="#ref-12">12</a>。',
    );
    expect(isResearchTurn({ agentMode: 'research' })).toBe(true);
    expect(isResearchTurn({ generatedFiles: [{ filename: 'a.html', source: 'research' }] })).toBe(true);
    expect(isResearchTurn({ agentMode: 'standard' })).toBe(false);
    expect(isResearchTurn({
      agentMode: 'standard',
      researchProgress: { stage: 'researching' },
      content: '# 普通搜索报告\n\n## 核心发现\n' + '普通搜索。'.repeat(80),
    })).toBe(false);
    expect(isResearchTurn({
      agentMode: 'plan',
      researchProgress: { stage: 'researching' },
    })).toBe(false);
    expect(isResearchTurn({
      agentMode: 'standard',
      content: '# 牛顿定律\n\n## 第一定律\n' + '惯性。'.repeat(80),
    })).toBe(false);
  });

  it('routes every consecutive Research report through the same structured viewer inputs', () => {
    const reports = [1, 2, 3].map((index) => ({
      agentMode: 'research',
      content: [
        `# 第 ${index} 份研究报告`,
        '',
        '## 执行摘要',
        '这是当轮结论。',
        '',
        '## 核心发现',
        '所有轮次都应进入同一白卡和全屏查看器。',
      ].join('\n'),
    }));

    expect(reports.map((message) => isResearchTurn(message))).toEqual([true, true, true]);
    expect(reports.map((message) => Boolean(researchStructureMarkdown(message)))).toEqual([
      true,
      true,
      true,
    ]);
  });

  it('unflattens glued markdown tables', () => {
    const glued = '| 成熟度 | 核心能力 | 风险 | | --- | --- | --- | | 规划 | 可修订计划 | 计划空转 |';
    const cleaned = stripResearchScaffold(`# 标题\n\n## 核心发现\n${glued}`);
    expect(cleaned).toContain('| 成熟度 | 核心能力 | 风险 |');
    expect(cleaned).toMatch(/\|\s*-{3,}\s*\|/);
    expect(cleaned.split('\n').filter((line) => line.includes('|')).length).toBeGreaterThan(1);
  });

  it('strips the document title from plan/research body when chrome already shows it', () => {
    const title = '整理 Claude Fable 5.1 介绍文档为 Word';
    const duplicated = [
      `# ${title}`,
      '',
      `# ${title}`,
      '',
      '## Summary',
      '核对资料后生成可下载文档。',
    ].join('\n');
    expect(stripLeadingTitleHeadings(duplicated, title)).toBe(
      '## Summary\n核对资料后生成可下载文档。',
    );
    expect(stripLeadingTitleHeadings(`# **${title}**\n\n## Key Changes\n- 整理规格`, title)).toBe(
      '## Key Changes\n- 整理规格',
    );
    expect(stripLeadingTitleHeadings(`## ${title}\n\n**Context**：先核对现有材料。`, title)).toBe(
      '**Context**：先核对现有材料。',
    );
    expect(stripLeadingTitleHeadings('# Claude Fable 5.1 研究报告\n\n## 执行摘要\n结论。', 'Claude Fable 5.1 研究报告')).toBe(
      '## 执行摘要\n结论。',
    );
    expect(stripLeadingTitleHeadings('## Summary\n\n正文', title)).toBe('## Summary\n\n正文');
    expect(stripLeadingTitleHeadings('', title)).toBe('');
    const longTitle = `${'深度研究长标题'.repeat(18)}需要超过一百二十字`;
    expect(longTitle.length).toBeGreaterThan(120);
    expect(stripLeadingTitleHeadings(`# ${longTitle}\n\n## 执行摘要\n结论。`, longTitle.slice(0, 120))).toBe(
      '## 执行摘要\n结论。',
    );
  });

  it('strips file suffixes and illegal path chars from download stems', () => {
    expect(reportFilenameStem('网页端 Agent 计划模式', '计划.html')).toBe('网页端 Agent 计划模式');
    expect(reportFilenameStem('', '竞品分析.html')).toBe('竞品分析');
    expect(reportFilenameStem('a/b:c?.html')).toBe('a b c');
  });

  it('keeps the legacy call counter separate from report page statistics', () => {
    expect(countResearchSearches({
      researchProgress: { searchCalls: 23 },
      agentSteps: [
        { name: 'search_web' },
        { name: 'search_web' },
        { name: 'search_web' },
        { name: 'deep_read' },
      ],
    })).toBe(3);
    expect(countResearchSearches({
      researchProgress: { searchCalls: 8 },
      agentSteps: [],
    })).toBe(8);
  });

  it('shows persisted team-wide pages and whole-run duration for the reported Kimi run', () => {
    const message = {
      researchProgress: { sourcesFound: 18, searchCalls: 5, citationCount: 4 },
      agentSteps: [{ name: 'search_web' }],
    };
    expect(researchCompletionStats(message, '6分53秒'))
      .toBe('耗时 6分53秒 · 共搜索到 18 个网页');
  });

  it('does not turn missing statistics or invalid counts into zero or use call counts', () => {
    const missing = { researchProgress: { searchCalls: 8, citationCount: 4 } };
    expect(researchCompletionStats(missing as Parameters<typeof researchCompletionStats>[0]))
      .toBe('耗时未记录 · 网页总数未记录');
    for (const sourcesFound of [-1, NaN, Infinity, 1.5]) {
      expect(researchCompletionStats({ researchProgress: { sourcesFound } }, '2.0s'))
        .toBe('耗时 2.0s · 网页总数未记录');
    }
    expect(researchCompletionStats({ researchProgress: { sourcesFound: 0 } }, '0.0s'))
      .toBe('耗时 0.0s · 共搜索到 0 个网页');
  });
});
