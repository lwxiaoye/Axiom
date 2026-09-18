import fs from 'fs';
import path from 'path';

describe('standalone agent run page chart outputs', () => {
  const source = fs.readFileSync(path.join(process.cwd(), 'src/views/agent/run/index.vue'), 'utf8');

  it('renders echarts outputs from workflow run results in assistant messages', () => {
    expect(source).toContain("import EChartsOutputPreview from '../../workflow/editor/components/EChartsOutputPreview.vue'");
    expect(source).toContain('chartOutputs?: WorkflowChartOutput[]');
    expect(source).toContain('const chartOutputs = extractChartOutputs(result)');
    expect(source).toContain('assistantMsg.chartOutputs = chartOutputs');
    expect(source).toContain('<EChartsOutputPreview');
  });

  it('does not render the no-output placeholder for chart-only results', () => {
    expect(source).toContain('const chartOutputs = extractChartOutputs(result)');
    // 兜底表达式后来被抽成 resultText() 复用，不再是内联在 `const text =` 后面的一整行；
    // 本契约只关心「（无输出）占位必须以 chartOutputs 为空为条件」，所以钉条件表达式本身，
    // 并反向钉不存在无条件的 `|| '（无输出）'` 兜底。
    expect(source).toContain("(chartOutputs.length ? '' : '（无输出）')");
    expect(source).not.toContain("|| '（无输出）'");
  });
});
