import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('knowledge chunk read-only access', () => {
  it('opens a chunk for viewers and leaves mutations behind canEdit', () => {
    const panel = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeChunksPanel.vue'), 'utf8');
    const tab = readFileSync(resolve(process.cwd(), 'src/views/peopleCenter/tabs/MyKnowledgeTab.vue'), 'utf8');

    expect(panel).toContain("function openChunk(record: KnowledgeChunk) {\n  emit('edit', record);");
    expect(tab).toContain("function editChunk(record: KnowledgeChunk) {\n  editingChunk.value = record;");
    expect(panel).toContain('if (!props.canEdit) return;\n  await setChunkEnabled(record.id, enabled)');
  });

  it('renders the shared chunk drawer as read-only for viewers', () => {
    const drawer = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeChunkEditorDrawer.vue'), 'utf8');

    expect(drawer).toContain("canEdit ? '编辑分段' : '分段详情'");
    expect(drawer).toContain(':readonly="!canEdit"');
    expect(drawer).toContain('v-if="canEdit"');
  });
});
