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
    // 右键菜单：编辑器后来把 @contextmenu.prevent 直接绑 openChunkImageMenu 改成先过一层
    // handleChunkContextMenu（只读态放行浏览器原生菜单，可编辑态才 preventDefault 再打开）。
    // 这里只钉「文本框容器绑了 contextmenu、且最终走到 openChunkImageMenu」，不钉中间那层的名字。
    expect(editor).toMatch(/class="chunk-editor-textarea-wrap"[^>]*@contextmenu(?:\.prevent)?="\w+"/);
    expect(editor).toMatch(/openChunkImageMenu\(event\)|@contextmenu\.prevent="openChunkImageMenu"/);
    expect(editor).toContain('ref="chunkTextareaRef"');
    expect(editor).toContain('uploadChunkImageFromMenu');
    // 上传：同一个抽屉现在同时服务用户侧与管理侧（props.management 三元选 API），
    // 本契约只关心用户侧仍走 uploadKnowledgeChunkImage 且带上分段 id。
    expect(editor).toMatch(/uploadKnowledgeChunkImage\)?\(props\.chunk\.id, /);
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
    // 面板同时服务用户侧与管理侧（props.management 三元选 deleteChunk / deleteManagedChunk），
    // 只钉「用户侧删除仍调 deleteChunk 且传分段 id」。
    expect(chunksPanel).toMatch(/await \(?[^;\n]*\bdeleteChunk\)?\(record\.id\);/);
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
