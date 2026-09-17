import { readFileSync } from 'fs';
import { resolve } from 'path';

describe('EChartsOutputPreview', () => {
  const source = readFileSync(resolve(__dirname, 'EChartsOutputPreview.vue'), 'utf8');

  it('renders workflow chart outputs with echarts and static image fallback', () => {
    expect(source).toContain("import * as echarts from 'echarts'");
    expect(source).toContain('echarts.init');
    expect(source).toContain('setOption');
    expect(source).toContain('imageDataUrl');
  });

  it('does not render chart source data above the chart', () => {
    expect(source).not.toContain("import MarkdownIt from 'markdown-it'");
    expect(source).not.toContain("import xss from 'xss'");
    expect(source).not.toContain('props.output?.data_markdown');
    expect(source).not.toContain('v-html="renderedDataMarkdown"');
    expect(source).not.toContain('class="chart-data-markdown markdown-body"');
  });
});
