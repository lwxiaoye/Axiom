import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('knowledge chunk list layout', () => {
  it('keeps the chunk table columns aligned with the document table', () => {
    const panel = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeChunksPanel.vue'), 'utf8');

    expect(panel).toContain('<div class="knowledge-chunk-table-head">');
    expect(panel).toContain('分段内容');
    expect(panel).toContain('参与检索');
    expect(panel).toMatch(/<div class="chunk-main-content">[\s\S]*?<b[^>]*>[\s\S]*?<\/b>[\s\S]*?<p class="chunk-preview">/);
    expect(panel).toContain('width: min(900px, 100%);');
    expect(panel).toContain('height: min(620px, calc(100vh - 300px));');
    expect(panel).toContain('min-height: 34px;');
    expect(panel).toContain('grid-template-columns: minmax(0, 1fr) 76px 84px 132px;');
    expect(panel).toContain('class="document-table-action danger"');
  });

  it('uses the same document-row grid and action treatment as the document tab', () => {
    const panel = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeChunksPanel.vue'), 'utf8');

    expect(panel).toContain('class="chunk-main-content"');
    expect(panel).toContain('class="document-table-action danger"');
    expect(panel).toContain('grid-template-columns: minmax(0, 1fr) 76px 84px 132px;');
    expect(panel).toContain('border: 1px solid #eceef3;');
    expect(panel).toContain('border-radius: 12px;');
    expect(panel).toContain('gap: 8px;');
  });

  it('matches the document tab typography for the section description', () => {
    const panel = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeChunksPanel.vue'), 'utf8');

    expect(panel).toContain('color: #858a95;');
    expect(panel).toContain('font-size: 16px;');
    expect(panel).toContain('line-height: 1.5715;');
  });

  it('isolates retrieval-switch pointer events from the editable chunk row', () => {
    const panel = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeChunksPanel.vue'), 'utf8');

    expect(panel).toContain('class="chunk-switch-control" @mousedown.stop @click.stop');
    expect(panel).toContain('<a-switch :checked="item.enabled === 1" @change="(value) => toggleChunk(item, Boolean(value))" />');
  });
});
