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
    expect(source).toContain("const text = result.output || result.errorMessage || (chartOutputs.length ? '' : '（无输出）')");
    expect(source).not.toContain("const text = result.output || result.errorMessage || '（无输出）'");
  });
});
