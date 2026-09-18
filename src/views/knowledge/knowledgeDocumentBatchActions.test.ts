import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const api = readFileSync(resolve(process.cwd(), 'src/views/knowledge/knowledge.api.ts'), 'utf8');
const managementDetail = readFileSync(
  resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeDetail.vue'),
  'utf8',
);
const myKnowledge = readFileSync(
  resolve(process.cwd(), 'src/views/peopleCenter/tabs/MyKnowledgeTab.vue'),
  'utf8',
);
const centerStyles = readFileSync(
  resolve(process.cwd(), 'src/views/peopleCenter/styles/centerNew.less'),
  'utf8',
);

describe('knowledge document batch actions', () => {
  it('exposes raw-file download and batch-delete APIs on both permission boundaries', () => {
    expect(api).toContain("documentDownload: '/ai/knowledge/document/download'");
    expect(api).toContain("documentDownloadZip: '/ai/knowledge/document/download-zip'");
    expect(api).toContain('export const deleteDocuments = (ids: string[])');
    expect(api).toContain('export const downloadKnowledgeDocument = (id: string, fileName: string)');
    expect(api).toContain('export const downloadKnowledgeDocumentArchive = (ids: string[])');
    expect(api).toContain('export const deleteManagedDocuments = (ids: string[])');
    expect(api).toContain('export const downloadManagedKnowledgeDocument = (id: string, fileName: string)');
    expect(api).toContain('export const downloadManagedKnowledgeDocumentArchive = (ids: string[])');
    // 用户侧走 agent-api（原 Java 接口已下线），管理侧仍在旧路径上。
    expect(api).toContain('`${KB}/documents/${id}/download`');
    expect(api).toContain("saveBlob(blob, fileName)");
    expect(api).toContain('`${KB}/documents/download-zip`');
    expect(api).toContain("saveBlob(blob, '知识库原始文档.zip')");
    expect(api).toContain('downloadBlobFile(managementUrl(Api.documentDownload), fileName, { id })');
    expect(api).toContain("downloadBlobFile(managementUrl(Api.documentDownloadZip), '知识库原始文档.zip', { ids: ids.join(',') })");
    expect(api).not.toContain('sourceUri');
  });

  it('keeps downloads available to viewers while limiting deletion to editors in both document lists', () => {
    expect(myKnowledge).toContain('下载原文');
    expect(myKnowledge).toContain('aria-label="批量操作"');
    expect(myKnowledge).toContain('selectedDocumentIds');
    expect(myKnowledge).toContain('v-if="canEditCurrent"');
    expect(myKnowledge).toContain('await deleteDocuments(selectedDocumentIds.value)');
    expect(myKnowledge).toContain('downloadKnowledgeDocumentArchive(selectedDocumentIds.value)');

    expect(managementDetail).toContain('下载原文');
    expect(managementDetail).toContain('批量下载原文');
    expect(managementDetail).toContain('selectedDocumentIds');
    expect(managementDetail).toContain('await deleteManagedDocuments(deletingIds)');
    expect(managementDetail).toContain('downloadManagedKnowledgeDocumentArchive(selectedDocumentIds.value)');
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
