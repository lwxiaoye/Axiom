import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('knowledge settings form', () => {
  it('does not expose ineffective chunking controls', () => {
    const detail = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeDetail.vue'), 'utf8');

    expect(detail).not.toContain('label="分段长度"');
    expect(detail).not.toContain('label="重叠长度"');
    expect(detail).not.toContain('chunkSize: knowledge.value.chunkSize');
    expect(detail).not.toContain('chunkOverlap: knowledge.value.chunkOverlap');
    expect(detail).toContain('class="threshold-control"');
    expect(detail).toContain('settingsForm.scoreThreshold || 0).toFixed(2)');
  });
});
