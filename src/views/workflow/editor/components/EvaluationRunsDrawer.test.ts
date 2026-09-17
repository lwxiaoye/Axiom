import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('EvaluationRunsDrawer', () => {
  const source = readFileSync(resolve(process.cwd(), 'src/views/workflow/editor/components/EvaluationRunsDrawer.vue'), 'utf8');
  const editor = readFileSync(resolve(process.cwd(), 'src/views/workflow/editor/index.vue'), 'utf8');
  const api = readFileSync(resolve(process.cwd(), 'src/views/workflow/api/workflow.api.ts'), 'utf8');

  it('provides filterable evaluation run discovery and a helpful empty state', () => {
    expect(source).toContain('minDurationMs');
    expect(source).toContain('minTotalTokens');
    expect(source).toContain('暂无符合筛选条件的评测记录');
    expect(source).toContain("queryWorkflowEvaluationRuns(props.appId, requestParams())");
  });

  it('opens a trace detail with linked model-call token and latency data', () => {
    expect(source).toContain('queryWorkflowEvaluationRunDetail(props.appId, record.runId)');
    expect(source).toContain('Token（入/出/推理）');
    expect(source).toContain('节点轨迹（${detail.nodeRuns.length}）');
    expect(source).toContain('变量快照');
  });

  it('connects the editor action to the protected evaluation APIs', () => {
    expect(editor).toContain('评测记录');
    expect(editor).toContain('<EvaluationRunsDrawer');
    expect(api).toContain('queryWorkflowEvaluationRuns');
    expect(api).toContain('queryWorkflowEvaluationRunDetail');
  });
});
