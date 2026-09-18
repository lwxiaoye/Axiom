import fs from 'fs';
import path from 'path';

describe('workflow agent run preview chart outputs', () => {
  const source = fs.readFileSync(path.join(__dirname, 'index.vue'), 'utf8');

  it('extracts chart outputs from final workflow results and renders them under assistant messages', () => {
    expect(source).toContain("import EChartsOutputPreview from '../editor/components/EChartsOutputPreview.vue'");
    expect(source).toContain('chartOutputs?: WorkflowChartOutput[]');
    expect(source).toContain('extractChartOutputs(result)');
    expect(source).toContain('assistantMessage.chartOutputs');
    expect(source).toContain('<EChartsOutputPreview');
  });

  it('loads only personal Agent Skills for the my skills picker tab', () => {
    expect(source).toContain("getAgentSkillList({ source: 'personal' })");
  });

  it('keeps the tool and skill picker modal actions comfortably spaced', () => {
    // 旧的 modal-actions 块（margin 8px / padding 16px 0 20px / min-width 76px）已被 picker-footer 取代，
    // 取消/确认收进 footer 右侧的按钮组。像素值不是契约内容，只钉「两个动作按钮同在 footer 里、
    // 按钮组声明了间距」；footer 与弹窗底边的距离由下面那条用例单独把关。
    const footerStart = source.indexOf('<div class="picker-footer">');
    expect(footerStart).toBeGreaterThan(-1);
    const footerTemplate = source.slice(footerStart, source.indexOf('</a-modal>', footerStart));
    expect(footerTemplate).toContain('@click="cancelPicker"');
    expect(footerTemplate).toContain('@click="confirmPicker"');

    const styleStart = source.indexOf('.picker-footer {');
    expect(styleStart).toBeGreaterThan(-1);
    const footerStyle = source.slice(styleStart, source.indexOf('\n}\n', styleStart));
    expect(footerStyle).toMatch(/>\s*div\s*\{[^}]*gap:\s*\d+px/);
  });

  it('explicitly calls saveDraft from the toolbar so click events do not silence success messages', () => {
    expect(source).toContain('@click="handleSaveDraft"');
    expect(source).toContain('async function handleSaveDraft()');
    expect(source).toContain('await saveDraft(false)');
    expect(source).toContain('const notify = silent !== true');
    expect(source).toContain("message.success({ content: '草稿已保存', key: 'agent-draft-save' })");
  });

  it('keeps the resource picker footer away from the modal bottom edge', () => {
    expect(source).toContain('padding: 14px 28px 26px;');
  });

  it('loads only owned and shared knowledge bases for the agent knowledge picker', () => {
    expect(source).toContain("import { loadWorkflowSelectableKnowledgeOptions } from '../utils/knowledgeSelection'");
    expect(source).toContain('knowledgeOptions.value = await loadWorkflowSelectableKnowledgeOptions(100);');
    expect(source).not.toContain("getKnowledgeList({ pageNo: 1, pageSize: 100, scope: 'all' } as any)");
  });
});
