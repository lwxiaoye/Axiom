import fs from 'node:fs';
import path from 'node:path';

const menu = fs.readFileSync(
  path.resolve(__dirname, 'ResearchReportExportMenu.vue'),
  'utf8',
);
const viewer = fs.readFileSync(path.resolve(__dirname, 'DocPagesViewer.vue'), 'utf8');
const messageList = fs.readFileSync(path.resolve(__dirname, 'MessageList.vue'), 'utf8');

describe('研究报告导出菜单', () => {
  it('四项文案与截图一致，且不带图标', () => {
    expect(menu).toContain('复制内容');
    expect(menu).toContain('导出到 Markdown');
    expect(menu).toContain('导出到 Word');
    expect(menu).toContain('导出到 PDF');
    expect(menu).not.toContain('导出到 HTML');
    expect(menu).not.toContain('FilePdfOutlined');
    expect(menu).toContain('border-radius: 16px');
    expect(menu).toContain('min-width: 200px');
  });

  it('全屏查看器的研究报告下载复用同一套菜单', () => {
    const start = viewer.indexOf('<ResearchReportExportMenu v-if="isReport"');
    expect(start).toBeGreaterThan(-1);
    const slice = viewer.slice(start, start + 500);
    expect(slice).toContain(':html="htmlSrc || \'\'"');
    expect(viewer).not.toContain('onCopyReport');
    expect(viewer).not.toContain('导出到 HTML');
  });

  it('对话里只保留蓝框报告导出菜单，不追加研究文件卡', () => {
    const start = messageList.indexOf('class="research-report-card"');
    const end = messageList.indexOf('class="generated-file-card"', start);
    const body = messageList.slice(start, end);
    expect(start).toBeGreaterThan(-1);
    expect(body).toContain('<ResearchReportExportMenu');
    expect(body).toContain(':html="researchCardPreviewHtml(message)"');
    expect(body).not.toContain('@click="downloadGeneratedFile(file)"');
    expect(messageList).toContain('filter((file) => !isResearchReportFile(file))');
    expect(messageList).not.toContain('v-if="isResearchTurn(message) && isResearchReportFile(file)');
    const structure = messageList.slice(
      messageList.indexOf('showResearchStructureCard(message)'),
      messageList.indexOf('visibleGeneratedFiles(message)'),
    );
    expect(structure).toContain('class="report-head-ic"');
    expect(structure).toContain('M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4');
    expect(structure).toContain('M15 3h6v6');
    expect(structure).not.toContain('DownloadOutlined');
    expect(structure).not.toContain('<ExpandOutlined');
  });
});
