<template>
  <a-drawer :open="open" title="Skill 管理" width="min(1280px, 96vw)" class="skill-manager-drawer" @close="handleClose">
    <a-spin :spinning="loading">
      <div v-if="detail" class="skill-meta">
        <div>
          <strong>{{ detail.name }}</strong>
          <span>{{ detail.skillId }}</span>
        </div>
        <em>v{{ detail.version || '-' }}</em>
        <em>{{ detail.enabled === 1 ? '已启用' : '已停用' }}</em>
        <em>{{ saveStateText }}</em>
      </div>

      <section class="skill-workspace">
        <aside class="skill-tree-panel">
          <div class="tree-toolbar">
            <a-tooltip title="新建文件"><button type="button" @click="openCreate('file')"><FileAddOutlined /></button></a-tooltip>
            <a-tooltip title="新建文件夹"><button type="button" @click="openCreate('folder')"><FolderAddOutlined /></button></a-tooltip>
            <a-tooltip title="上传文件"><button type="button" @click="triggerUpload"><UploadOutlined /></button></a-tooltip>
            <a-tooltip title="全部折叠"><button type="button" @click="expandedKeys = []"><VerticalAlignMiddleOutlined /></button></a-tooltip>
            <input ref="uploadInputRef" type="file" class="hidden-upload" @change="handleUpload" />
          </div>
          <a-tree
            v-if="treeData.length"
            v-model:expandedKeys="expandedKeys"
            :tree-data="treeData"
            block-node
            show-icon
            @select="handleTreeSelect"
          >
            <template #icon="{ dataRef }">
              <component :is="fileIcon(dataRef as SkillTreeNode)" />
            </template>
            <template #title="{ title }">
              <span class="tree-title">{{ title }}</span>
            </template>
          </a-tree>
          <a-empty v-else description="暂无文件" />
        </aside>

        <main class="skill-editor-panel">
          <div class="open-tabs" v-if="openFiles.length">
            <button
              v-for="file in openFiles"
              :key="file.path"
              type="button"
              :class="{ active: activePath === file.path }"
              @click="activateFile(file.path)"
            >
              <component :is="fileIconByPath(file.path)" />
              <i v-if="file.dirty" class="unsaved-dot"></i>
              <span>{{ basename(file.path) }}</span>
              <CloseOutlined @click.stop="closeFile(file.path)" />
            </button>
          </div>

          <div v-if="activeFile" class="editor-shell">
            <div class="editor-head">
              <div>
                <strong>{{ activeFile.path }}</strong>
                <span>{{ activeFile.unsupported ? 'unsupported' : editorModeLabel }}</span>
              </div>
              <a-segmented v-if="isMarkdown(activeFile.path)" v-model:value="markdownMode" :options="markdownModes" />
            </div>

            <div v-if="activeFile.unsupported" class="unsupported-preview">
              <FileTextOutlined />
              <strong>该文件不支持预览</strong>
              <p>{{ activeFile.unsupportedReason || '当前文件类型无法在编辑器中安全打开。' }}</p>
            </div>
            <div v-else-if="isMarkdown(activeFile.path) && markdownMode === 'preview'" class="markdown-preview-scroll">
              <MarkdownViewer class="markdown-preview" :value="activeFile.content || '暂无内容'" />
            </div>
            <CodeEditor
              v-else
              v-model:value="activeFile.content"
              class="code-editor"
              :mode="codeMode(activeFile.path)"
              :auto-format="false"
              @change="onEditorChange"
              @save="saveCurrentFile"
            />
          </div>
          <div v-else class="editor-empty">
            <FileTextOutlined />
            <strong>选择左侧文件开始编辑</strong>
            <p>支持 Python、Markdown、JSON、YAML 等文本文件；输入后会自动保存。</p>
          </div>
        </main>
      </section>
    </a-spin>

    <a-modal v-model:open="createOpen" :title="createKind === 'folder' ? '新建文件夹' : '新建文件'" @ok="confirmCreate">
      <a-input v-model:value="createPath" :placeholder="createKind === 'folder' ? '例如：scripts' : '例如：scripts/main.py'" />
    </a-modal>
  </a-drawer>
</template>

<script lang="ts" setup>
  import { Modal } from 'ant-design-vue';
  import { computed, ref, watch } from 'vue';
  import {
    CloseOutlined,
    CodeOutlined,
    FileAddOutlined,
    FileMarkdownOutlined,
    FileTextOutlined,
    FolderAddOutlined,
    FolderOpenOutlined,
    FolderOutlined,
    UploadOutlined,
    VerticalAlignMiddleOutlined,
  } from '@ant-design/icons-vue';
  import { CodeEditor } from '/@/components/CodeEditor';
  import { MarkdownViewer } from '/@/components/Markdown';
  import { useMessage } from '/@/hooks/web/useMessage';
  import { createSkillFile, getFiles, getSkillFile, queryById, saveSkillFile, uploadSkillFile } from '../skill.api';

  type SkillTreeNode = {
    title: string;
    key: string;
    path: string;
    directory: boolean;
    children?: SkillTreeNode[];
  };

  type OpenFile = {
    path: string;
    content: string;
    savedContent: string;
    loaded: boolean;
    dirty?: boolean;
    unsupported?: boolean;
    unsupportedReason?: string;
  };

  const props = defineProps({
    open: { type: Boolean, default: false },
    id: { type: String, default: '' },
  });

  const emit = defineEmits(['update:open']);
  const { createMessage } = useMessage();
  const loading = ref(false);
  const saving = ref(false);
  const detail = ref<any>(null);
  const files = ref<any[]>([]);
  const expandedKeys = ref<string[]>([]);
  const openFiles = ref<OpenFile[]>([]);
  const activePath = ref('');
  const markdownMode = ref<'edit' | 'preview'>('edit');
  const createOpen = ref(false);
  const createKind = ref<'file' | 'folder'>('file');
  const createPath = ref('');
  const uploadInputRef = ref<HTMLInputElement | null>(null);

  const markdownModes = [
    { label: '编辑', value: 'edit' },
    { label: '预览', value: 'preview' },
  ];

  const treeData = computed(() => toTree(files.value));
  const activeFile = computed(() => openFiles.value.find((file) => file.path === activePath.value));
  const saveStateText = computed(() => (saving.value ? '保存中...' : activeFile.value?.dirty ? '未保存' : activeFile.value ? '已保存' : ''));
  const editorModeLabel = computed(() => (activeFile.value ? codeMode(activeFile.value.path) : 'text'));

  async function saveCurrentFile() {
    const file = activeFile.value;
    await saveFile(file);
  }

  async function saveFile(file?: OpenFile) {
    if (!file || !props.id || file.unsupported || !file.dirty) return;
    saving.value = true;
    try {
      await saveSkillFile({ id: props.id, path: file.path, content: file.content });
      file.savedContent = file.content;
      file.dirty = false;
      if (file.path.toLowerCase() === 'skill.md') {
        await loadFilesOnly();
      }
    } finally {
      saving.value = false;
    }
  }

  watch(
    () => [props.open, props.id],
    async () => {
      if (props.open && props.id) {
        await loadDetail();
      }
    },
    { immediate: true }
  );

  async function loadDetail() {
    loading.value = true;
    try {
      detail.value = await queryById({ id: props.id });
      await loadFilesOnly();
      openFiles.value = [];
      activePath.value = '';
      const first = findFirstFile(treeData.value, 'SKILL.md') || findFirstFile(treeData.value);
      if (first) await openFile(first.path);
    } finally {
      loading.value = false;
    }
  }

  async function loadFilesOnly() {
    files.value = await getFiles({ id: props.id });
    expandedKeys.value = [];
  }

  function toTree(list: any[]): SkillTreeNode[] {
    return (list || []).map((item) => ({
      title: item.name,
      key: item.path || item.name,
      path: item.path || item.name,
      directory: !!item.directory,
      children: item.children ? toTree(item.children) : undefined,
    }));
  }

  async function handleTreeSelect(keys: string[], info: any) {
    const node = info?.node?.dataRef as SkillTreeNode | undefined;
    if (!node || node.directory || !keys.length) return;
    await openFile(node.path);
  }

  async function openFile(path: string) {
    let file = openFiles.value.find((item) => item.path === path);
    if (!file) {
      file = { path, content: '', savedContent: '', loaded: false };
      openFiles.value.push(file);
    }
    markdownMode.value = 'edit';
    if (!isEditableFile(path)) {
      file.content = '';
      file.savedContent = '';
      file.loaded = true;
      file.dirty = false;
      file.unsupported = true;
      file.unsupportedReason = '当前文件类型无法在编辑器中安全打开。';
      activePath.value = path;
      return;
    }
    if (!file.loaded) {
      try {
        file.content = await getSkillFile({ id: props.id, path });
        file.savedContent = file.content;
        file.dirty = false;
        file.unsupported = false;
        file.unsupportedReason = '';
      } catch (error) {
        file.content = '';
        file.savedContent = '';
        file.dirty = false;
        file.unsupported = true;
        file.unsupportedReason = '文件内容无法按文本读取，请下载后在本地查看。';
      } finally {
        file.loaded = true;
      }
    }
    activePath.value = path;
  }

  function activateFile(path: string) {
    activePath.value = path;
  }

  async function closeFile(path: string) {
    const index = openFiles.value.findIndex((item) => item.path === path);
    if (index < 0) return;
    const file = openFiles.value[index];
    const canClose = await confirmSaveBeforeClose(file);
    if (!canClose) return;
    openFiles.value.splice(index, 1);
    if (activePath.value === path) {
      activePath.value = openFiles.value[Math.max(index - 1, 0)]?.path || '';
    }
  }

  function onEditorChange(value?: string) {
    const file = activeFile.value;
    if (file && !file.unsupported) {
      const nextContent = typeof value === 'string' ? value : file.content;
      file.content = nextContent;
      file.dirty = nextContent !== file.savedContent;
    }
  }

  function confirmSaveBeforeClose(file?: OpenFile) {
    if (!file?.dirty) return Promise.resolve(true);
    return new Promise<boolean>((resolve) => {
      Modal.confirm({
        title: '有未保存的修改',
        content: `${file.path} 尚未保存，是否保存后关闭？`,
        okText: '保存并关闭',
        cancelText: '取消关闭',
        async onOk() {
          await saveFile(file);
          resolve(true);
        },
        onCancel() {
          resolve(false);
        },
      });
    });
  }

  async function confirmAllBeforeClose() {
    const dirtyFiles = openFiles.value.filter((file) => file.dirty);
    return confirmDirtyFilesBeforeClose(dirtyFiles);
  }

  function confirmDirtyFilesBeforeClose(dirtyFiles: OpenFile[]) {
    if (!dirtyFiles.length) return Promise.resolve(true);
    return new Promise<boolean>((resolve) => {
      Modal.confirm({
        title: '有未保存的修改',
        content: `${dirtyFiles.length} 个文件尚未保存，是否全部保存后关闭？`,
        okText: '全部保存并关闭',
        cancelText: '取消关闭',
        async onOk() {
          await Promise.all(dirtyFiles.map((file) => saveFile(file)));
          resolve(true);
        },
        onCancel() {
          resolve(false);
        },
      });
    });
  }

  function openCreate(kind: 'file' | 'folder') {
    createKind.value = kind;
    createPath.value = activeDirectory();
    createOpen.value = true;
  }

  async function confirmCreate() {
    const path = createPath.value.trim().replace(/\\/g, '/');
    if (!path) {
      createMessage.warning('请输入路径');
      return;
    }
    await createSkillFile({ id: props.id, path, directory: createKind.value === 'folder' });
    createOpen.value = false;
    await loadFilesOnly();
    if (createKind.value === 'file') await openFile(path);
  }

  function triggerUpload() {
    uploadInputRef.value?.click();
  }

  async function handleUpload(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    await uploadSkillFile({ file, data: { id: props.id, path: activeDirectory() } });
    input.value = '';
    await loadFilesOnly();
    await openFile(joinPath(activeDirectory(), file.name));
  }

  function activeDirectory() {
    const path = activePath.value;
    if (!path) return '';
    const index = path.lastIndexOf('/');
    return index >= 0 ? path.slice(0, index) : '';
  }

  function joinPath(parent: string, name: string) {
    return parent ? parent + '/' + name : name;
  }

  function basename(path: string) {
    return path.split('/').pop() || path;
  }

  function isMarkdown(path: string) {
    return /\.(md|markdown)$/i.test(path);
  }

  function codeMode(path: string) {
    const ext = path.split('.').pop()?.toLowerCase();
    if (ext === 'py') return 'python';
    if (ext === 'md' || ext === 'markdown') return 'markdown';
    if (ext === 'json') return 'application/json';
    if (ext === 'js' || ext === 'ts') return 'javascript';
    if (ext === 'html') return 'htmlmixed';
    if (ext === 'css' || ext === 'less') return 'css';
    if (ext === 'yaml' || ext === 'yml') return 'yaml';
    if (ext === 'xml') return 'xml';
    return 'text/plain';
  }

  function isEditableFile(path: string) {
    const name = basename(path).toLowerCase();
    const ext = path.includes('.') ? path.split('.').pop()?.toLowerCase() : '';
    const textNames = ['dockerfile', 'makefile', 'readme', 'license', '.gitignore', '.env'];
    const textExtensions = [
      'css',
      'env',
      'gitignore',
      'html',
      'ini',
      'java',
      'js',
      'json',
      'less',
      'log',
      'markdown',
      'md',
      'properties',
      'py',
      'sh',
      'sql',
      'ts',
      'txt',
      'xml',
      'yaml',
      'yml',
    ];
    return textNames.includes(name) || (!!ext && textExtensions.includes(ext));
  }

  function fileIcon(node: SkillTreeNode) {
    if (node.directory) return expandedKeys.value.includes(node.key) ? FolderOpenOutlined : FolderOutlined;
    return fileIconByPath(node.path);
  }

  function fileIconByPath(path: string) {
    const ext = path.split('.').pop()?.toLowerCase();
    if (ext === 'md' || ext === 'markdown') return FileMarkdownOutlined;
    if (ext === 'py' || ext === 'js' || ext === 'ts') return CodeOutlined;
    return FileTextOutlined;
  }

  function findFirstFile(nodes: SkillTreeNode[], preferred?: string): SkillTreeNode | undefined {
    for (const node of nodes) {
      if (!node.directory && (!preferred || node.path.toLowerCase() === preferred.toLowerCase())) return node;
      const found = node.children ? findFirstFile(node.children, preferred) : undefined;
      if (found) return found;
    }
    return undefined;
  }

  async function handleClose() {
    const canClose = await confirmAllBeforeClose();
    if (!canClose) return;
    emit('update:open', false);
  }
</script>

<style scoped lang="less">
  :global(.skill-manager-drawer .ant-drawer-body) {
    overflow: hidden;
  }

  .skill-meta {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 14px;
    padding: 12px 14px;
    border: 1px solid #e8eaf0;
    border-radius: 8px;
    background: #fff;

    div {
      display: grid;
      min-width: 0;
      margin-right: auto;
    }

    strong {
      color: #111827;
      font-size: 15px;
    }

    span,
    em {
      color: #667085;
      font-size: 12px;
      font-style: normal;
    }
  }

  .skill-workspace {
    display: grid;
    grid-template-columns: 300px minmax(0, 1fr);
    height: calc(100vh - 180px);
    min-height: 520px;
    overflow: hidden;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    background: #fff;
  }

  .skill-tree-panel {
    min-width: 0;
    min-height: 0;
    overflow: auto;
    border-right: 1px solid #e5e7eb;
    background: #fbfcfe;
  }

  .tree-toolbar {
    position: sticky;
    top: 0;
    z-index: 1;
    display: flex;
    gap: 6px;
    padding: 10px;
    border-bottom: 1px solid #edf0f3;
    background: #fff;

    button {
      display: grid;
      width: 32px;
      height: 32px;
      place-items: center;
      border: 1px solid #e3e5ea;
      border-radius: 7px;
      background: #fff;
      color: #344054;
      cursor: pointer;

      &:hover {
        border-color: #111827;
        color: #111827;
      }
    }
  }

  .hidden-upload {
    display: none;
  }

  .tree-title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .skill-editor-panel {
    display: flex;
    min-width: 0;
    min-height: 0;
    flex-direction: column;
    background: #fff;
  }

  .open-tabs {
    display: flex;
    min-height: 42px;
    gap: 6px;
    overflow-x: auto;
    padding: 8px 10px;
    border-bottom: 1px solid #edf0f3;
    background: #fafafa;

    button {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      max-width: 220px;
      height: 28px;
      border: 1px solid #e5e7eb;
      border-radius: 7px;
      background: #fff;
      color: #475467;
      cursor: pointer;

      span {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .unsaved-dot {
        width: 7px;
        height: 7px;
        flex: 0 0 auto;
        border-radius: 50%;
        background: #ef4444;
      }

      &.active {
        border-color: #111827;
        color: #111827;
      }
    }
  }

  .editor-shell {
    display: flex;
    min-height: 0;
    flex: 1;
    flex-direction: column;
    overflow: hidden;
  }

  .editor-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 12px;
    border-bottom: 1px solid #edf0f3;

    div {
      display: grid;
      min-width: 0;
    }

    strong {
      overflow: hidden;
      color: #111827;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    span {
      color: #98a2b3;
      font-size: 12px;
    }
  }

  .code-editor {
    min-height: 0;
    flex: 1;
    overflow: auto;
  }

  .markdown-preview-scroll {
    min-height: 0;
    flex: 1;
    overflow: auto;
    padding: 18px 22px;
  }

  .markdown-preview {
    min-height: 100%;
  }

  .unsupported-preview {
    display: grid;
    min-height: 0;
    flex: 1;
    place-items: center;
    align-content: center;
    padding: 24px;
    color: #667085;
    text-align: center;

    .anticon {
      margin-bottom: 12px;
      color: #98a2b3;
      font-size: 34px;
    }

    strong {
      color: #111827;
      font-size: 15px;
    }

    p {
      max-width: 360px;
      margin: 8px 0 0;
      color: #667085;
    }
  }

  .editor-empty {
    display: grid;
    flex: 1;
    place-items: center;
    align-content: center;
    color: #98a2b3;
    text-align: center;

    .anticon {
      margin-bottom: 10px;
      color: #667085;
      font-size: 30px;
    }

    strong {
      color: #344054;
    }

    p {
      margin: 6px 0 0;
    }
  }

  @media (max-width: 900px) {
    .skill-workspace {
      grid-template-columns: 1fr;
      height: calc(100vh - 160px);
    }

    .skill-tree-panel {
      max-height: 260px;
      border-right: 0;
      border-bottom: 1px solid #e5e7eb;
    }
  }
</style>
