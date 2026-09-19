import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { KNOWLEDGE_UPLOAD_ACCEPT } from './knowledgeUploadFormats';

describe('knowledge upload formats', () => {
  it('allows every knowledge document format promised by the upload dialog', () => {
    expect(KNOWLEDGE_UPLOAD_ACCEPT.split(',')).toEqual([
      '.markdown', '.htm', '.xlsx', '.csv', '.html', '.properties',
      '.txt', '.docx', '.md', '.mdx', '.pdf', '.xls', '.vtt',
    ]);
  });

  it('keeps document preview and upload on the current origin agent-api path', () => {
    const api = readFileSync(resolve(process.cwd(), 'src/views/knowledge/knowledge.api.ts'), 'utf8');
    // uploadFile 绕过了 URL 前缀钩子：agent-api 不在 /api 之下，baseURL 必须是空串而不是
    // useGlobSetting().apiUrl（那是老 Java 管理侧上传用的，已随 Managed* 一起删掉）。
    expect(api).not.toContain('useGlobSetting');
    expect(api).toMatch(/export const uploadKnowledgeDocument[\s\S]*?url: `\$\{KB\}\/bases\/\$\{knowledgeId\}\/documents\/upload`, baseURL: ''/);
    expect(api).toMatch(/export const previewKnowledgeDocument[\s\S]*?url: `\$\{KB\}\/bases\/\$\{knowledgeId\}\/documents\/preview`, baseURL: ''/);
  });

  it('keeps the upload dialog a capped content-area size instead of stretching with the sidebar', () => {
    const modal = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/DocumentUploadModal.vue'), 'utf8');
    expect(modal).toContain('wrap-class-name="knowledge-upload-modal"');
    expect(modal).toContain('width="min(1040px, calc(100% - 64px))"');
    expect(modal).toContain('left: var(--center-nav-width, 0px);');
    expect(modal).toContain('max-width: 1040px;');
    expect(modal).not.toContain('width="1180px"');
    expect(modal).not.toContain('width="calc(100% - 48px)"');
    expect(modal).toContain('prefers-reduced-motion: reduce');
  });

  it('uses a multi-file preview layout without upload progress in the dialog', () => {
    const modal = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/DocumentUploadModal.vue'), 'utf8');
    expect(modal).not.toContain('文件上传进度');
    expect(modal).not.toContain('<a-progress');
    expect(modal).toContain('class="preview-file-list"');
    expect(modal).toContain('class="preview-detail-pane"');
    expect(modal).toContain('@click="selectPreviewFile(item.uid)"');
    expect(modal).toContain('@click="openChunkDetail(chunk, index)"');
  });

  it('keeps preview documents and chunks in a 2 to 3 width ratio', () => {
    const modal = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/DocumentUploadModal.vue'), 'utf8');
    expect(modal).toContain('grid-template-columns: minmax(0, 2fr) minmax(0, 3fr);');
  });

  it('keeps the parameter step compact without changing its controls', () => {
    const modal = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/DocumentUploadModal.vue'), 'utf8');
    expect(modal).toMatch(/\.settings-panel\s*\{[\s\S]*?min-height:\s*300px;/);
    expect(modal).toMatch(/min-height:\s*56px;\s*padding:\s*10px 14px;/);
    expect(modal).toMatch(/\.settings-panel :deep\(\.ant-form-item\)\s*\{[\s\S]*?margin-bottom:\s*14px;/);
  });

  it('closes the dialog after at least one document has been submitted', () => {
    const modal = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/DocumentUploadModal.vue'), 'utf8');
    expect(modal).toContain("if (completed) { emit('success'); close(); }");
  });

  it('paginates knowledge chunks by 20 while keeping pagination outside the scrollable table body', () => {
    const chunksPanel = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeChunksPanel.vue'), 'utf8');
    expect(chunksPanel).toContain('const pagination = reactive({ current: 1, pageSize: 20');
    expect(chunksPanel).toContain('class="knowledge-list-scroll"');
    expect(chunksPanel).toContain('class="knowledge-list-pagination"');
    expect(chunksPanel).not.toContain('class="chunk-table-scroll"');
  });

  it('opens a document-specific chunk list when its name is clicked', () => {
    const chunksPanel = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeChunksPanel.vue'), 'utf8');
    expect(chunksPanel).toContain('documentId: props.documentId');
    expect(chunksPanel).toContain('v-model:value="selectedDocumentId"');
    expect(chunksPanel).toContain('@change="handleChunkDocumentFilter"');
  });

  it('labels the retrieval percentage as relevance instead of accuracy', () => {
    const retrieval = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/RetrievalTester.vue'), 'utf8');
    expect(retrieval).toContain('相关度');
  });

  it('inserts existing chunk images as markdown at the textarea cursor, without an upload entry', () => {
    const editor = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeChunkEditorDrawer.vue'), 'utf8');
    const api = readFileSync(resolve(process.cwd(), 'src/views/knowledge/knowledge.api.ts'), 'utf8');
    expect(editor).toContain('ref="chunkTextareaRef"');
    expect(editor).toContain('@click="insertChunkImage(image)"');
    expect(editor).toContain('insertTextAtCursor(`\\n\\n${markdownImage(image)}\\n\\n`)');
    expect(editor).toContain('const contentWithImages = chunkEditorContent.value.trim();');
    expect(editor).toContain('contentWithImages, images: chunkEditorImages.value');
    // 插图上传（右键菜单、上传按钮、5 MB 限制）只属于已下线的 Java 管理侧，agent-api 的分段没有图片存储
    expect(editor).not.toContain('handleChunkContextMenu');
    expect(editor).not.toContain('<a-upload');
    expect(editor).not.toContain('单张不超过 5 MB');
    expect(api).toContain("'contentWithImages'");
    expect(api).not.toContain('uploadKnowledgeChunkImage');
  });

  it('keeps long retrieval result lists in a dedicated scrollable region', () => {
    const retrieval = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/RetrievalTester.vue'), 'utf8');
    expect(retrieval).toContain('class="retrieval-result-list"');
    expect(retrieval).toContain('max-height: min(60vh, 680px);');
    expect(retrieval).toContain('overflow-y: auto;');
  });
});
