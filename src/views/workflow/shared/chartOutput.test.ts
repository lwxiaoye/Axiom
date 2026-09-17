import { extractChartOutputs, isChartOutput } from './chartOutput';

describe('workflow chart output helpers', () => {
  it('detects and extracts echarts outputs from workflow node results', () => {
    const chart = { echarts_option: { xAxis: {}, yAxis: {}, series: [] }, chart_type: 'bar' };
    const result = {
      runId: 'r1',
      status: 'success',
      output: '',
      durationMs: 1,
      nodeRuns: [{ nodeId: 'n1', nodeType: 'tool', nodeLabel: '图表', status: 'success', durationMs: 1, output: chart }],
    };

    expect(isChartOutput(chart)).toBe(true);
    expect(extractChartOutputs(result)).toEqual([chart]);
  });

  it('deduplicates the same chart mirrored through workflow compatibility output fields', () => {
    const chart = {
      echarts_option: { xAxis: { type: 'category' }, yAxis: {}, series: [{ type: 'bar', data: [1] }] },
      image_base64: 'PHN2Zy8+',
      image_mime_type: 'image/svg+xml',
      chart_type: 'bar',
    };
    const clonedChart = () => JSON.parse(JSON.stringify(chart));
    const result = {
      runId: 'r1',
      status: 'success',
      output: '',
      durationMs: 1,
      nodeRuns: [
        {
          nodeId: 'n1',
          nodeType: 'tool',
          nodeLabel: '图表',
          status: 'success',
          durationMs: 1,
          output: clonedChart(),
          outputs: {
            system_rawResponse: clonedChart(),
            result: clonedChart(),
          },
        },
      ],
      outputs: {
        n1: {
          system_rawResponse: clonedChart(),
          result: clonedChart(),
        },
      },
    };

    expect(extractChartOutputs(result)).toHaveLength(1);
  });

  it('deduplicates chart outputs by rendered chart content, not markdown data', () => {
    const first = {
      data_markdown: '| a | b |\n| --- | --- |\n| A | 1 |',
      echarts_option: { xAxis: { type: 'category' }, yAxis: {}, series: [{ type: 'bar', data: [1] }] },
      chart_type: 'bar',
    };
    const second = {
      ...first,
      data_markdown: '| a | b |\n| --- | --- |\n| A | 1 |\n| A | 1 |',
    };
    const result = {
      runId: 'r1',
      status: 'success',
      output: '',
      durationMs: 1,
      nodeRuns: [
        { nodeId: 'n1', nodeType: 'tool', nodeLabel: '图表', status: 'success', durationMs: 1, output: first },
        { nodeId: 'n1', nodeType: 'tool', nodeLabel: '图表', status: 'success', durationMs: 1, output: second },
      ],
    };

    expect(extractChartOutputs(result)).toHaveLength(1);
  });

  it('deduplicates the same echarts chart when one mirrored image payload is truncated', () => {
    const chart = {
      echarts_option: { xAxis: { type: 'category', data: ['1月'] }, yAxis: {}, series: [{ type: 'bar', data: [12] }] },
      image_base64: 'full-svg-base64-payload',
      image_mime_type: 'image/svg+xml',
      chart_type: 'bar',
    };
    const truncatedChart = {
      ...chart,
      image_base64: 'full-svg-base64-pay...(截断，共 9999 字符)',
    };
    const result = {
      runId: 'r1',
      status: 'success',
      output: '',
      durationMs: 1,
      nodeRuns: [
        {
          nodeId: 'n1',
          nodeType: 'tool',
          nodeLabel: '图表',
          status: 'success',
          durationMs: 1,
          outputs: {
            system_rawResponse: truncatedChart,
          },
        },
      ],
      outputs: {
        n1: {
          system_rawResponse: chart,
        },
      },
    };

    expect(extractChartOutputs(result)).toHaveLength(1);
  });
});
