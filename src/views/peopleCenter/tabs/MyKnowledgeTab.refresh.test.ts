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

  it('keeps existing chunk images insertable at the cursor and strips them from the saved content', () => {
    const editor = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeChunkEditorDrawer.vue'), 'utf8');
    expect(component).toContain('v-model:open="chunkEditorOpen"');
    expect(component).toContain('<KnowledgeChunkEditorDrawer');
    // 插图上传只存在于已下线的 Java 管理侧（management 分支），agent-api 的分段没有图片存储：
    // 抽屉不再带右键插图菜单与上传按钮，也不再有 management prop；只有老数据自带 images 时
    // 才展示图片区，让编辑者还能把它们插回正文或删掉。
    expect(editor).not.toContain('management');
    expect(editor).not.toContain('uploadKnowledgeChunkImage');
    expect(editor).not.toContain('openChunkImageMenu');
    expect(editor).not.toContain('<a-upload');
    expect(editor).toContain('<a-form-item v-if="chunkEditorImages.length" label="分段图片">');
    expect(editor).toContain('ref="chunkTextareaRef"');
    expect(editor).toContain('@click="insertChunkImage(image)"');
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

  it('allows editors to delete chunks from the user-side list', () => {
    const api = readFileSync(resolve(process.cwd(), 'src/views/knowledge/knowledge.api.ts'), 'utf8');
    const chunksPanel = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeChunksPanel.vue'), 'utf8');
    expect(api).toContain('export const deleteChunk = (id: string)');
    expect(api).toContain('`${KB}/chunks/${id}`');
    expect(component).toContain('<KnowledgeChunksPanel');
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
    // 本契约保护的是「分段条目样式挂在 knowledge-chunk-item 命名空间下，不与个人中心旧的 .chunk-item 撞」。
    // hover 后来从 .editable 改挂到 .interactive（只读用户也能点开分段详情，所以人人有 hover），
    // 修饰类名不是契约内容，只钉根类名与 hover 规则的命名空间。
    expect(chunksPanel).toMatch(/:class="\['knowledge-chunk-item', \{/);
    expect(chunksPanel).not.toMatch(/:class="\['chunk-item', \{/);
    expect(chunksPanel).toMatch(/\.knowledge-chunk-item\.\w+:hover/);
    expect(chunksPanel).not.toMatch(/(?<![\w-])\.chunk-item(?:\.\w+)?:hover/);
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

  it('never mounts the department selector on the user-side ACL editor', () => {
    const template = component.slice(0, component.indexOf('<script'));
    const renderedTemplate = template.replace(/<!--[\s\S]*?-->/g, '');
    // auth-api 没有部门概念：JSelectDept 一挂载就请求已下线的 sysDepart 接口（授权页打开即 404）。
    // 既不引入该组件，下拉也不再提供「部门」；历史数据里的部门行只读展示、保存时原样带回。
    expect(component).not.toMatch(/import \{[^}]*JSelectDept[^}]*\} from '\/@\/components\/Form'/);
    expect(renderedTemplate).not.toContain('<JSelectDept');
    expect(renderedTemplate).not.toContain('value="DEPARTMENT"');
    expect(renderedTemplate).toContain('v-for="(item, index) in aclReadonlyItems"');
    // 服务端存的 subjectType 大小写不一（所有者那条是小写 user）：读回时先归一，
    // 否则小写行落进错误分支——这正是之前 JSelectDept 被渲染出来的原因
    expect(component).toContain('subjectType: normalizeAclSubjectType(row.subjectType)');
    expect(component).toMatch(/expandAclItems\(\[\.\.\.aclReadonlyItems\.value, \.\.\.aclItems\.value\]\)/);
  });
});
