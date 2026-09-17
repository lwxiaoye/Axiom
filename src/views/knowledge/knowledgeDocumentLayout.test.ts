import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('knowledge document list layout', () => {
  it('uses a viewport-bounded scroll area so pagination remains visible', () => {
    const detail = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeDetail.vue'), 'utf8');

    expect(detail).toContain(":scroll=\"{ y: 'calc(100vh - 440px)' }\"");
  });
});
