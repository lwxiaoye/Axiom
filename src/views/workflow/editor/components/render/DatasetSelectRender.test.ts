import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('DatasetSelectRender', () => {
  const source = readFileSync(resolve(process.cwd(), 'src/views/workflow/editor/components/render/DatasetSelectRender.vue'), 'utf8');

  it('hides dataset parameter button for AI chat knowledge selection', () => {
    expect(source).toContain('v-if="showParamsButton"');
    expect(source).toContain('props.input.key !== NodeInputKeyEnum.aiChatDatasets');
  });

  it('hides rerank controls from dataset search parameters', () => {
    expect(source).not.toContain('结果重排');
    expect(source).not.toContain('datasetSearchUsingReRank');
    expect(source).not.toContain('datasetSearchRerankModel');
    expect(source).not.toContain('datasetSearchRerankWeight');
  });
});
