import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const api = readFileSync(resolve(process.cwd(), 'src/views/knowledge/knowledge.api.ts'), 'utf8');
const myKnowledge = readFileSync(
  resolve(process.cwd(), 'src/views/peopleCenter/tabs/MyKnowledgeTab.vue'),
  'utf8',
);
const centerStyles = readFileSync(
  resolve(process.cwd(), 'src/views/peopleCenter/styles/centerNew.less'),
  'utf8',
);

describe('knowledge document batch actions', () => {
  it('exposes raw-file download and batch-delete APIs through agent-api', () => {
    expect(api).toContain('export const deleteDocuments = (ids: string[])');
    expect(api).toContain('export const downloadKnowledgeDocument = (id: string, fileName: string)');
    expect(api).toContain('export const downloadKnowledgeDocumentArchive = (ids: string[])');
    // 用户侧走 agent-api（原 Java 接口已下线）
    expect(api).toContain('`${KB}/documents/${id}/download`');
    expect(api).toContain("saveBlob(blob, fileName)");
    expect(api).toContain('`${KB}/documents/download-zip`');
    expect(api).toContain("saveBlob(blob, '知识库原始文档.zip')");
    expect(api).not.toContain('sourceUri');
  });

  it('keeps downloads available to viewers while limiting deletion to editors', () => {
    expect(myKnowledge).toContain('下载原文');
    expect(myKnowledge).toContain('aria-label="批量操作"');
    expect(myKnowledge).toContain('selectedDocumentIds');
    expect(myKnowledge).toContain('v-if="canEditCurrent"');
    expect(myKnowledge).toContain('await deleteDocuments(selectedDocumentIds.value)');
    expect(myKnowledge).toContain('downloadKnowledgeDocumentArchive(selectedDocumentIds.value)');
  });

  it('keeps bulk and per-document actions in dedicated, consistently styled action zones', () => {
    expect(myKnowledge).toContain('class="knowledge-document-bulk-actions"');
    expect(myKnowledge).toContain('class="knowledge-document-table-header"');
    expect(myKnowledge).toContain('class="document-table-action"');
  });

  it('keeps the document table within the available detail-panel width', () => {
    expect(centerStyles).toContain('grid-template-columns: 24px minmax(180px, 1fr) 76px 84px minmax(132px, auto);');
    expect(centerStyles).toContain('min-width: 132px;');
  });

  it('keeps search and selected-document actions on one desktop toolbar line', () => {
    expect(centerStyles).toContain('.knowledge-document-toolbar {\n  display: flex;\n  flex: 0 0 auto;');
    expect(centerStyles).toContain('flex-wrap: nowrap;');
    expect(centerStyles).toContain('.knowledge-document-bulk-actions {\n  display: inline-flex;\n  flex: 0 0 auto;');
    expect(centerStyles).toContain('white-space: nowrap;');
    expect(centerStyles).toContain('.knowledge-document-toolbar .market-search.compact {\n  width: 240px;');
  });

  it('uses a compact retrieval switch in each document row', () => {
    expect(myKnowledge).toContain('class="document-retrieval-switch"');
    expect(centerStyles).toContain('.document-retrieval-switch {\n  min-width: 40px;\n  width: 40px;\n  height: 22px;');
  });
});
