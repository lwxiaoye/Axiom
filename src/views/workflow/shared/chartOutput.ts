import type { WorkflowRunResponse } from '../api/workflow.api';

export type WorkflowChartOutput = {
  data_markdown?: string;
  echarts_option?: Record<string, any>;
  image_base64?: string;
  image_mime_type?: string;
  chart_type?: string;
  render_warning?: string;
};

export function isChartOutput(value: unknown): value is WorkflowChartOutput {
  if (!value || typeof value !== 'object') return false;
  const output = value as WorkflowChartOutput;
  return !!(
    output.echarts_option ||
    (typeof output.image_base64 === 'string' && typeof output.image_mime_type === 'string')
  );
}

function stableStringify(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(',')}]`;
  }
  if (value && typeof value === 'object') {
    return `{${Object.keys(value as Record<string, unknown>)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableStringify((value as Record<string, unknown>)[key])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value);
}

function chartSignature(output: WorkflowChartOutput) {
  if (output.echarts_option) {
    return stableStringify({
      echarts_option: output.echarts_option,
      chart_type: output.chart_type || '',
    });
  }
  return stableStringify({
    image_base64: output.image_base64 || '',
    image_mime_type: output.image_mime_type || '',
    chart_type: output.chart_type || '',
  });
}

export function extractChartOutputs(result: WorkflowRunResponse | null | undefined): WorkflowChartOutput[] {
  const charts: WorkflowChartOutput[] = [];
  const seen = new Set<string>();

  function collect(value: unknown) {
    if (!isChartOutput(value)) return;
    const signature = chartSignature(value);
    if (seen.has(signature)) return;
    seen.add(signature);
    charts.push(value);
  }

  (result?.nodeRuns || []).forEach((run) => {
    collect(run.output);
    if (run.outputs && typeof run.outputs === 'object') {
      Object.values(run.outputs).forEach(collect);
    }
  });
  Object.values(result?.outputs || {}).forEach((nodeOutputs) => {
    Object.values(nodeOutputs || {}).forEach(collect);
  });

  return charts;
}
