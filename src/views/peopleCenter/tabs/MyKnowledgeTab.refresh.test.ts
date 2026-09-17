import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const component = readFileSync(
  resolve(process.cwd(), 'src/views/peopleCenter/tabs/MyKnowledgeTab.vue'),
  'utf8',
);

describe('MyKnowledgeTab chunk refresh', () => {
  it('reloads chunks when opening the chunks view and while document processing completes', () => {
    expect(component).toContain('ref, watch }');
    expect(component).toContain("watch(activeView, (view) => {");
    expect(component).toContain("if (view === 'chunks') void reloadChunksPanel();");
    expect(component).toContain("if (activeView.value === 'chunks') await reloadChunksPanel();");
  });

  it('creates a knowledge base without presenting or submitting documents', () => {
    expect(component).not.toContain('knowledge-create-upload');
    expect(component).not.toContain('beforeCreateUpload');
    expect(component).not.toContain('uploadCreateDocuments');
    expect(component).not.toContain('uploadKnowledgeDocument');
  });

  it('shows a description and creator on every knowledge card', () => {
    const renderedTemplate = component.replace(/<!--[\s\S]*?-->/g, '');
    expect(component).toContain('class="knowledge-card-description"');
    expect(component).toContain('class="knowledge-card-creator"');
    expect(renderedTemplate).toContain('创建人：{{ displayCreator(item) }}');
  });

  it('does not render a duplicate empty-state card beside the owned create card', () => {
    expect(component).toContain('v-if="scope === \'shared\' && !items.length" class="my-agent-empty"');
  });

  it('allows selecting multiple documents without showing upload progress in the modal', () => {
    const uploadModal = readFileSync(
      resolve(process.cwd(), 'src/views/knowledge/components/DocumentUploadModal.vue'),
      'utf8',
    );
    expect(uploadModal).toContain(':multiple="true"');
    expect(uploadModal).toContain('upload-file-row');
    expect(uploadModal).not.toContain('文件上传进度');
    expect(uploadModal).not.toContain(':percent="item.progress"');
  });

  it('allows editors to insert user-side chunk images at the cursor and preserve inline image order', () => {
    const editor = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeChunkEditorDrawer.vue'), 'utf8');
    expect(component).toContain('v-model:open="chunkEditorOpen"');
    expect(component).toContain('<KnowledgeChunkEditorDrawer');
    expect(editor).toContain('@contextmenu.prevent="openChunkImageMenu"');
    expect(editor).toContain('ref="chunkTextareaRef"');
    expect(editor).toContain('uploadChunkImageFromMenu');
    expect(editor).toContain('uploadKnowledgeChunkImage(props.chunk.id, options.file as File)');
    expect(editor).toContain('insertChunkImage(image, chunkInsertSelection.value)');
    expect(editor).toContain('const contentWithImages = chunkEditorContent.value.trim();');
    expect(editor).toContain('const content = stripMarkdownImages(contentWithImages);');
    expect(editor).toContain('contentWithImages, images: chunkEditorImages.value');
  });

  it('keeps user-side knowledge cards and document statuses readable', () => {
    expect(component).toContain("return name || '未命名知识库';");
    expect(component).toContain('knowledgeDocumentStatusText(status)');
    expect(component).toContain('isKnowledgeDocumentProcessing(status)');
    expect(component).not.toContain('/^[\\d\\W_]+$/.test(name)');
  });

  it('allows editors to delete chunks from the user-side and admin-side lists', () => {
    const api = readFileSync(resolve(process.cwd(), 'src/views/knowledge/knowledge.api.ts'), 'utf8');
    const detail = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeDetail.vue'), 'utf8');
    const chunksPanel = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeChunksPanel.vue'), 'utf8');
    expect(api).toContain("chunkDelete: '/ai/knowledge/chunk/delete'");
    expect(api).toContain('export const deleteChunk = (id: string)');
    expect(component).toContain('<KnowledgeChunksPanel');
    expect(detail).toContain('<KnowledgeChunksPanel');
    expect(chunksPanel).toContain('title="确定删除该分段？"');
    expect(chunksPanel).toContain('@confirm="removeChunk(item)"');
    expect(chunksPanel).toContain('await deleteChunk(record.id);');
  });

  it('keeps document and chunk lists aligned with knowledge management', () => {
    const chunksPanel = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeChunksPanel.vue'), 'utf8');
    expect(component).toContain('const documentPagination = reactive({ current: 1, pageSize: 20, total: 0 });');
    expect(component).toContain('<KnowledgeChunksPanel');
    expect(chunksPanel).toContain('const pagination = reactive({ current: 1, pageSize: 20, total: 0 });');
    expect(chunksPanel).toContain('v-model:value="selectedDocumentId"');
    expect(component).toContain('@click="openDocumentChunks(item)"');
    expect(component).toContain('chunkDocumentId.value = document.id;');
    expect(chunksPanel).toContain('documentId: props.documentId');
    expect(chunksPanel).toContain('async function loadDocumentOptions()');
    expect(chunksPanel).toContain('class="knowledge-list-scroll"');
    expect(chunksPanel).toContain('class="knowledge-list-pagination"');
  });

  it('keeps the shared chunk list hover style isolated from legacy center chunk styles', () => {
    const chunksPanel = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeChunksPanel.vue'), 'utf8');
    expect(chunksPanel).toContain("['knowledge-chunk-item', { editable: canEdit }]");
    expect(chunksPanel).not.toContain("['chunk-item', { editable: canEdit }]");
    expect(chunksPanel).toContain('.knowledge-chunk-item.editable:hover');
    expect(chunksPanel).not.toContain('.chunk-item.editable:hover');
  });

  it('resets document and chunk list state when opening another knowledge base', () => {
    expect(component).toContain('chunkDocumentId.value = undefined;');
    expect(component).toContain('documentPagination.current = 1;');
  });

  it('uses the server permission and only loads ACL rows for owners', () => {
    expect(component).toContain('await loadKnowledge();');
    expect(component).toContain('canManageAccess.value ? loadAcl() : Promise.resolve()');
    expect(component).toContain('normalizeKnowledgePermission(source.currentPermission)');
    expect(component).not.toContain('function aclSubjectMatches');
    expect(component).not.toContain('function currentRoleIds');
    expect(component).not.toContain('function currentDeptIds');
  });
});
