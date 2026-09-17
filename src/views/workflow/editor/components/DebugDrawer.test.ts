import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('DebugDrawer interactive state and chart output preview', () => {
  const source = readFileSync(resolve(process.cwd(), 'src/views/workflow/editor/components/DebugDrawer.vue'), 'utf8');

  it('renders workflow interactive user selection in debug waiting state', () => {
    expect(source).toContain('result.interactive');
    expect(source).toContain("result.interactive.type === 'userSelect'");
    expect(source).toContain('handleResume(opt.value)');
  });

  it('emits resume payloads so debug runs can continue from an interrupt', () => {
    expect(source).toContain("(e: 'resume'");
    expect(source).toContain("emit('resume'");
  });

  it('renders chart previews only in the main run result area to avoid duplicate charts', () => {
    expect(source).toContain("import EChartsOutputPreview from './EChartsOutputPreview.vue'");
    expect(source).not.toContain('isChartOutput(trace.output)');
    expect(source).toContain('<EChartsOutputPreview');
    expect(source).toContain('class="trace-detail"');
  });

  it('renders chart outputs in the main run result area', () => {
    expect(source).toContain('const resultChartOutputs = computed(() => extractChartOutputs(props.result))');
    expect(source).toContain('v-if="resultChartOutputs.length"');
    expect(source).toContain('v-for="(chartOutput, chartIndex) in resultChartOutputs"');
  });

  it('offers an explicit single-step lifecycle alongside full-flow debugging', () => {
    expect(source).toContain("(e: 'start-step'");
    expect(source).toContain("(e: 'next-step'");
    expect(source).toContain("(e: 'stop-step')");
    expect(source).toContain('开始单步');
    expect(source).toContain('执行下一节点');
    expect(source).toContain('结束单步');
    expect(source).toContain('当前变量快照');
  });
});
