import fs from 'fs';
import path from 'path';

const runFilesSource = fs.readFileSync(
  path.resolve(__dirname, 'RunGeneratedFiles.vue'),
  'utf8',
);
const inlinePreviewSource = fs.readFileSync(
  path.resolve(__dirname, '../../../peopleCenter/components/InlineFilePreview.vue'),
  'utf8',
);
const myFilesSource = fs.readFileSync(
  path.resolve(__dirname, '../../../peopleCenter/tabs/MyFilesTab.vue'),
  'utf8',
);
const excelViewerSource = fs.readFileSync(
  path.resolve(__dirname, '../../../peopleCenter/components/ExcelFileViewer.vue'),
  'utf8',
);

describe('子智能体输出文件预览', () => {
  it('预览尚未就绪时保留用户点击，就绪后自动打开', () => {
    expect(inlinePreviewSource).toContain("type OpenViewerResult = 'opened' | 'pending' | 'unavailable'");
    expect(inlinePreviewSource).toContain('viewerRequested.value = true;');
    expect(inlinePreviewSource).toContain("return 'pending';");
    expect(inlinePreviewSource).toContain('watch(ready, (isReady) => {');
    expect(inlinePreviewSource).toContain('if (hasViewerContent()) viewerOpen.value = true;');
  });

  it('不再静默吞掉转换失败或不可预览状态', () => {
    expect(runFilesSource).toContain('@ready="onPreviewReady(file)"');
    expect(runFilesSource).toContain('@failed="onPreviewFailed(file)"');
    expect(runFilesSource).toContain("if (result === 'pending')");
    expect(runFilesSource).toContain("else if (result !== 'opened')");
    expect(runFilesSource).toContain('预览生成失败，可先下载后查看');
  });

  it('Excel 与主 Agent 共用原始工作簿查看器，不再走 PDF 打印分页', () => {
    expect(runFilesSource).toContain("'xlsx', 'xlsm'");
    expect(inlinePreviewSource).toContain("if (ext === 'xlsx' || ext === 'xlsm') return 'xlsx';");
    expect(inlinePreviewSource).toContain("} else if (m === 'xlsx') {");
    expect(inlinePreviewSource).toContain("(mode === 'xlsx' && blobUrl)");
    expect(inlinePreviewSource).toContain('<ExcelFileViewer');
    expect(myFilesSource).toContain('<ExcelFileViewer');
    expect(excelViewerSource).toContain("@vue-office/excel/lib/v3/vue-office-excel.mjs");
  });
});
