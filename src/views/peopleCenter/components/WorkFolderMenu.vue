<template>
  <a-popover v-model:open="menuOpen" trigger="click" placement="topLeft" :arrow="false" overlay-class-name="work-folder-popover" @open-change="onMenuOpen">
    <template #content>
      <div class="work-folder-menu">
        <a-input v-model:value="search" class="folder-search" placeholder="搜索文件夹" allow-clear aria-label="查找工作文件夹">
          <template #prefix><SearchOutlined /></template>
        </a-input>
        <div v-if="folderError" class="folder-load-error" role="alert">
          <div><span>暂时无法加载文件夹</span><small>{{ folderError }}</small></div>
          <button type="button" :disabled="loadingFolders" @click="loadFolders"><ReloadOutlined :spin="loadingFolders" />{{ loadingFolders ? '重试中' : '重试' }}</button>
        </div>
        <div v-if="loadingFolders && !folders.length" class="folder-empty"><LoadingOutlined /> 正在加载</div>
        <div v-else class="folder-choices">
          <button
            v-for="item in filteredFolders" :key="item.id" type="button"
            class="folder-choice" :class="{ selected: item.id === folder?.id }"
            :disabled="busy" :title="item.name" :aria-pressed="item.id === folder?.id" @click="choose(item)"
          >
            <span class="folder-symbol"><FolderOutlined /></span><span class="folder-name">{{ item.name }}</span>
          </button>
          <div v-if="!folderError && !filteredFolders.length" class="folder-empty">{{ search ? '没有匹配的文件夹' : '还没有工作文件夹' }}</div>
        </div>
        <div class="folder-actions">
          <button type="button" class="folder-action" :disabled="busy" @click="startCreate"><PlusOutlined /><span>新建文件夹</span></button>
          <button v-if="folder" type="button" class="folder-action view-files" :disabled="folder.unavailable" @click="openFiles">
            <FolderOpenOutlined /><span>查看文件与上传素材</span>
          </button>
          <button v-if="folder" type="button" class="folder-action" :disabled="busy" @click="choose(null)"><MinusOutlined /><span>取消选择文件夹</span></button>
        </div>
        <p v-if="folder?.unavailable" class="folder-error" role="alert">原文件夹已删除，请选择新的工作文件夹。</p>
        <p v-if="busy" class="menu-note">本轮结束后可以切换文件夹或上传素材。</p>
        <p v-else-if="hasThread" class="menu-note">切换文件夹将新建对话，当前对话保留在历史中。</p>
      </div>
    </template>
    <button
      type="button" class="work-folder-trigger" :class="{ selected: folder, unavailable: folder?.unavailable }"
      :title="folder ? `工作区：${folder.name}` : '选择工作区'" :aria-expanded="menuOpen" aria-label="工作区">
      <FolderOpenOutlined v-if="folder" /><FolderOutlined v-else /><span>{{ folder?.name || '选择工作区' }}</span>
    </button>
  </a-popover>

  <a-modal v-model:open="creating" wrap-class-name="work-folder-create-dialog" :footer="null" :width="520" :closable="!savingFolder" :mask-closable="!savingFolder" :keyboard="!savingFolder" :destroy-on-close="true">
    <template #title><strong class="create-title">创建文件夹</strong></template>
    <form class="create-folder" @submit.prevent="createAndSelect">
      <a-input ref="nameInput" v-model:value="newName" class="create-name" :maxlength="50" placeholder="文件夹名称" aria-label="新文件夹名称" :disabled="savingFolder">
        <template #prefix><FolderOutlined /></template>
      </a-input>
      <div class="create-material-label">素材<span>（可选）</span></div>
      <button type="button" class="create-material-picker" :disabled="savingFolder" @click="createUploadInput?.click()">
        <FolderAddOutlined /><span>添加 Agent 可读取和编辑的素材</span>
      </button>
      <input ref="createUploadInput" type="file" multiple class="upload-input" aria-label="添加新文件夹素材" @change="pickCreateUploads" />
      <ul v-if="createUploads.length" class="create-upload-list">
        <li v-for="(file, index) in createUploads" :key="`${file.name}-${file.size}-${file.lastModified}`">
          <FileOutlined /><span :title="file.name">{{ file.name }}</span><button type="button" :aria-label="`移除 ${file.name}`" :disabled="savingFolder" @click="createUploads.splice(index, 1)"><CloseOutlined /></button>
        </li>
      </ul>
      <p v-if="createError" class="folder-error" role="alert">{{ createError }}</p>
      <div class="create-folder-actions">
        <a-button type="text" aria-label="取消新建文件夹" :disabled="savingFolder" @click="creating = false">取消</a-button>
        <a-button class="folder-primary" html-type="submit" :loading="savingFolder" :disabled="busy || !newName.trim()">创建文件夹</a-button>
      </div>
    </form>
  </a-modal>

  <a-modal v-model:open="filesOpen" wrap-class-name="work-folder-dialog" :footer="null" :width="560" :destroy-on-close="true">
    <template #title>
      <div class="files-title">
        <span class="files-title-icon"><FolderOpenOutlined /></span>
        <div><strong :title="folder?.name">{{ folder?.name || '工作文件夹' }}</strong><small>工作文件夹</small></div>
      </div>
    </template>
    <div class="work-folder-files">
      <p class="menu-help">上传素材可直接用于对话与编辑，生成的文件也保存在这里。</p>
      <div class="files-toolbar">
        <span>{{ files.length }} 个文件</span>
        <a-button type="text" class="files-refresh" :disabled="loadingFiles || uploading" @click="loadFiles"><ReloadOutlined /> 刷新</a-button>
        <a-button class="folder-primary" :loading="uploading" :disabled="busy || uploading || folder?.unavailable" @click="uploadInput?.click()"><UploadOutlined /> 上传素材</a-button>
        <input ref="uploadInput" type="file" multiple class="upload-input" aria-label="上传文件夹素材" @change="uploadFiles" />
      </div>
      <p v-if="filesError" class="folder-error" role="alert">{{ filesError }}</p>
      <div v-if="loadingFiles" class="folder-empty"><LoadingOutlined /> 正在加载文件</div>
      <ul v-else-if="files.length" class="files-list">
        <li v-for="file in files" :key="file.id">
          <span class="file-icon"><FileOutlined /></span>
          <div class="file-info"><span :title="file.filename">{{ file.filename }}</span><small>{{ file.source === 'uploaded' ? '上传素材' : '生成文件' }} · {{ formatSize(file.size) }}</small></div>
          <a-button type="text" class="file-download" :loading="downloading.has(file.id)" :aria-label="`下载 ${file.filename}`" @click="download(file)"><DownloadOutlined /></a-button>
        </li>
      </ul>
      <div v-else-if="!filesError" class="folder-empty files-empty">
        <span class="empty-folder-icon"><FolderOpenOutlined /></span>
        <strong>文件夹还是空的</strong><span>上传素材，或让 Agent 在这里生成文件。</span>
      </div>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue';
import { CloseOutlined, DownloadOutlined, FileOutlined, FolderAddOutlined, FolderOpenOutlined, FolderOutlined, LoadingOutlined, MinusOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, UploadOutlined } from '@ant-design/icons-vue';
import { createFolder, downloadUserFile, listUserFiles, uploadUserFile, type UserFileItem, type UserFolderItem, type WorkFolderSelection } from '../myfiles.api';

const props = defineProps<{ folder: WorkFolderSelection | null; busy?: boolean; hasThread?: boolean; conversationKey?: string }>();
const emit = defineEmits<{
  (event: 'select', folder: WorkFolderSelection | null): void;
  (event: 'uploading', active: boolean): void;
}>();
const menuOpen = ref(false);
const filesOpen = ref(false);
const search = ref('');
const folders = ref<UserFolderItem[]>([]);
const loadingFolders = ref(false);
const folderError = ref('');
const creating = ref(false);
const newName = ref('');
const createError = ref('');
const createUploads = ref<File[]>([]);
const createUploadInput = ref<HTMLInputElement | null>(null);
const savingFolder = ref(false);
const nameInput = ref<{ focus: () => void } | null>(null);
const uploadInput = ref<HTMLInputElement | null>(null);
const files = ref<UserFileItem[]>([]);
const filesError = ref('');
const loadingFiles = ref(false);
const uploading = ref(false);
const downloading = ref(new Set<string>());
let folderRequest = 0;
let fileRequest = 0;
let selectionVersion = 0;
const filteredFolders = computed(() => folders.value.filter((item) => item.name.toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase())));
const errorText = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试';

async function loadFolders() {
  const request = ++folderRequest;
  loadingFolders.value = true;
  folderError.value = '';
  try {
    const result = await listUserFiles('__root__');
    if (request === folderRequest) folders.value = result.folders || [];
  } catch (error) {
    if (request === folderRequest) folderError.value = errorText(error);
  } finally {
    if (request === folderRequest) loadingFolders.value = false;
  }
}

function onMenuOpen(open: boolean) {
  if (open) void loadFolders();
}

function choose(folder: WorkFolderSelection | null) {
  if (props.busy || savingFolder.value) return;
  selectionVersion += 1;
  emit('select', folder ? { id: folder.id, name: folder.name } : null);
  menuOpen.value = false;
  creating.value = false;
  newName.value = '';
}

async function startCreate() {
  menuOpen.value = false;
  creating.value = true;
  newName.value = '';
  createError.value = '';
  createUploads.value = [];
  await nextTick();
  nameInput.value?.focus();
}

function pickCreateUploads(event: Event) {
  const input = event.target as HTMLInputElement;
  const uploads = [...createUploads.value, ...Array.from(input.files || [])];
  createUploads.value = [...new Map(uploads.map((file) => [`${file.name}-${file.size}-${file.lastModified}`, file])).values()];
  input.value = '';
}

async function createAndSelect() {
  if (props.busy || savingFolder.value || !newName.value.trim()) return;
  const version = selectionVersion;
  savingFolder.value = true;
  createError.value = '';
  try {
    const folder = await createFolder(newName.value.trim());
    folders.value = [...folders.value, folder];
    const failures: string[] = [];
    for (const file of createUploads.value) {
      try { await uploadUserFile(file, folder.id); }
      catch (error) { failures.push(`${file.name}：${errorText(error)}`); }
    }
    if (version === selectionVersion && !props.busy && creating.value) {
      emit('select', { id: folder.id, name: folder.name });
      menuOpen.value = false;
      creating.value = false;
      newName.value = '';
      createUploads.value = [];
      if (failures.length) {
        await nextTick();
        filesOpen.value = true;
        await loadFiles();
        filesError.value = `文件夹已创建，以下素材上传失败：${failures.join('；')}`;
      }
    }
  } catch (error) {
    createError.value = errorText(error);
  } finally {
    savingFolder.value = false;
  }
}

function openFiles() {
  menuOpen.value = false;
  filesOpen.value = true;
  void loadFiles();
}

async function loadFiles() {
  const id = props.folder?.id;
  const request = ++fileRequest;
  if (!id) return;
  loadingFiles.value = true;
  filesError.value = '';
  try {
    const result = await listUserFiles(id);
    if (request === fileRequest && id === props.folder?.id) files.value = result.files;
  } catch (error) {
    if (request === fileRequest && id === props.folder?.id) filesError.value = errorText(error);
  } finally {
    if (request === fileRequest) loadingFiles.value = false;
  }
}

async function uploadFiles(event: Event) {
  const input = event.target as HTMLInputElement;
  const uploads = Array.from(input.files || []);
  input.value = '';
  const id = props.folder?.id;
  if (!id || props.busy || uploading.value || !uploads.length) return;
  uploading.value = true;
  emit('uploading', true);
  filesError.value = '';
  const failures: string[] = [];
  try {
    for (const file of uploads) {
      try { await uploadUserFile(file, id); } catch (error) { failures.push(`${file.name}：${errorText(error)}`); }
    }
    if (id === props.folder?.id && filesOpen.value) {
      await loadFiles();
      if (failures.length) filesError.value = failures.join('；');
    }
  } finally {
    uploading.value = false;
    emit('uploading', false);
  }
}

async function download(file: UserFileItem) {
  if (downloading.value.has(file.id)) return;
  downloading.value.add(file.id);
  try { await downloadUserFile(file); } catch (error) { filesError.value = errorText(error); }
  finally { downloading.value.delete(file.id); }
}

function formatSize(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

watch(() => props.folder?.id, () => {
  selectionVersion += 1;
  fileRequest += 1;
  files.value = [];
  filesError.value = '';
  filesOpen.value = false;
  loadingFiles.value = false;
});
watch(() => props.busy, (busy) => {
  if (busy) selectionVersion += 1;
  else if (filesOpen.value) void loadFiles();
});
watch(() => props.conversationKey, () => {
  selectionVersion += 1;
  menuOpen.value = false;
  filesOpen.value = false;
  creating.value = false;
});
onUnmounted(() => emit('uploading', false));
</script>

<style scoped lang="less">
@ink: #292929;
@muted: #858585;
@line: #ececec;
@surface: #f5f5f5;

:global(.work-folder-popover .ant-popover-inner) {
  padding: 6px;
  border: 1px solid #e9e9e9;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 8px 24px rgba(0, 0, 0, .07), 0 2px 4px rgba(0, 0, 0, .03);
}
:global(.work-folder-popover .ant-popover-inner-content) { padding: 0; }
.work-folder-trigger {
  display: inline-flex; align-items: center; gap: 6px; width: auto; height: 32px; max-width: 210px;
  padding: 0 10px; margin-left: 0; border: 1px solid transparent; border-radius: 8px; background: #f5f5f5; color: #5f6063;
  cursor: pointer; font-size: 13px; line-height: 20px; flex-shrink: 1; min-width: 0; transition: color .15s, background .15s, border-color .15s;
  > span:not(.anticon) { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .anticon { flex: 0 0 auto; font-size: 14px; }
  &:hover, &[aria-expanded='true'] { color: #303237; background: #f2f2f3; border-color: #dedee1; }
  &:focus-visible { outline: 2px solid #aaa; outline-offset: 2px; }
  &.selected { color: #444; }
  &.unavailable { color: #a33; }
}
.work-folder-menu { width: min(280px, calc(100vw - 48px)); color: @ink; }
.menu-help { font-size: 12px; color: @muted; line-height: 1.7; margin: 0; }
.work-folder-menu .folder-search {
  width: 100%; margin: 0 0 5px; height: 32px; padding: 0 9px;
  // 后台主题对全站输入框使用 !important；只在这个弹层内覆盖其皮肤。
  border: 0; border-color: transparent !important; border-radius: 0; background: transparent !important; box-shadow: none;
  &:hover, &:focus-within { border-color: transparent !important; box-shadow: none; }
  :deep(.ant-input) { background: transparent !important; color: @ink; font-size: 13px; }
  :deep(.ant-input-prefix) { color: #9597a0; margin-right: 8px; }
  :deep(.ant-input::placeholder) { color: #a0a2ab; }
}
.menu-note { color: @muted; font-size: 11px; line-height: 1.6; margin: 8px 10px 5px; }
.folder-choices { display: flex; flex-direction: column; gap: 0; max-height: min(280px, 42vh); overflow-y: auto; }
.folder-choice, .folder-action {
  display: flex; align-items: center; gap: 9px; width: 100%; min-height: 34px; padding: 6px 9px;
  border: 0; border-radius: 6px; background: transparent; color: @ink; text-align: left; cursor: pointer; font-size: 13px;
  transition: background .15s, color .15s;
  &:hover:not(:disabled) { background: @surface; }
  &:focus-visible { outline: 2px solid #a6a8b2; outline-offset: -2px; }
  &:disabled { cursor: default; opacity: .45; }
}
.folder-symbol { display: grid; place-items: center; flex: 0 0 14px; width: 14px; height: 20px; color: #777; font-size: 14px; }
.folder-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.folder-choice.selected {
  color: #292929; background: #f1f1f1;
  .folder-symbol { color: #555; }
}
.folder-actions { margin: 5px 0 0; padding: 5px 0 0; border-top: 1px solid @line; }
.folder-action {
  min-height: 34px; padding: 6px 9px; gap: 9px; color: #606060;
  > .anticon { font-size: 14px; color: #858585; }
  > span:not(.anticon) { flex: 1; }
}
.create-folder {
  padding: 16px 24px 20px;
  .create-name { height: 40px; padding: 0 12px; border-radius: 12px !important; }
  :deep(.ant-input) { min-width: 0; font-size: 14px; }
  :deep(.ant-input-prefix) { margin-right: 12px; color: #777; }
  .folder-primary { padding: 0 12px; }
}
.create-folder-actions { display: flex; align-items: center; justify-content: flex-end; gap: 12px; margin-top: 20px; :deep(.ant-btn-text) { color: #888; font-size: 13px; font-weight: 400; } }
.create-folder .create-name {
  border-color: #dedede !important;
  &:focus-within, &:hover { border-color: #999 !important; box-shadow: none !important; }
}
.create-title { font-size: 20px; font-weight: 600; line-height: 28px; color: #222; }
.create-material-label { margin: 12px 0 8px; font-size: 13px; color: #333; span { color: #999; font-size: 12px; } }
.create-material-picker { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 7px; width: 100%; min-height: 98px; padding: 16px; border: 1px solid #ededed; border-radius: 11px; background: #fff; color: #333; font-size: 13px; cursor: pointer; > .anticon { color: #999; font-size: 19px; } &:hover:not(:disabled) { background: #fafafa; border-color: #d9d9d9; } }
.create-upload-list { list-style: none; margin: 8px 0 0; padding: 0; max-height: 100px; overflow-y: auto; li { display: flex; align-items: center; gap: 8px; padding: 4px; color: #777; font-size: 12px; } li > span:not(.anticon) { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } button { border: 0; background: transparent; color: #999; cursor: pointer; } }
:global(.work-folder-create-dialog .ant-modal .ant-modal-content) { padding: 0; border-radius: 20px !important; overflow: hidden; background: #fcfcfc !important; }
:global(.work-folder-create-dialog .ant-modal-header) { margin: 0; padding: 20px 24px 0; border: 0; background: transparent; }
:global(.work-folder-create-dialog .ant-modal-body) { padding: 0; }
:global(.work-folder-create-dialog .ant-modal-close) { top: 16px; right: 16px; width: 28px; height: 28px; color: #777; }
:global(.work-folder-create-dialog .ant-modal-close-x) { width: 28px; height: 28px; line-height: 28px; font-size: 12px; }
.folder-error { color: #b42318; font-size: 12px; line-height: 1.6; margin: 8px 10px; overflow-wrap: anywhere; }
.folder-error button { border: 0; background: none; color: inherit; text-decoration: underline; cursor: pointer; }
.folder-load-error {
  display: flex; align-items: center; gap: 12px; margin: 2px 3px 6px; padding: 10px;
  border-radius: 7px; background: #f7f7f7;
  > div { flex: 1; min-width: 0; }
  span { display: block; color: #555; font-size: 12px; line-height: 20px; }
  small { display: block; color: #888; font-size: 11px; line-height: 18px; overflow-wrap: anywhere; }
  button { display: inline-flex; align-items: center; gap: 4px; flex-shrink: 0; padding: 5px 7px; border: 1px solid #e5e5e5; border-radius: 6px; background: #fff; color: #555; font-size: 12px; cursor: pointer; }
  button:hover:not(:disabled) { border-color: #bbb; }
  button:focus-visible { outline: 2px solid #aaa; outline-offset: 2px; }
  button:disabled { opacity: .5; cursor: wait; }
}
.folder-empty { padding: 14px 10px; text-align: center; color: @muted; font-size: 12px; }
.folder-primary {
  height: 32px; padding: 0 13px; border: 1px solid @ink; border-radius: 8px; background: @ink; color: #fff; font-size: 12px; box-shadow: none;
  &:hover:not(:disabled), &:focus-visible:not(:disabled) { background: #444750; border-color: #444750; color: #fff; }
  &:disabled { color: #a9abb3; background: #f1f2f4; border-color: @line; }
}
:global(.work-folder-dialog .ant-modal .ant-modal-content) { padding: 0; border: 1px solid @line; border-radius: 20px !important; overflow: hidden; box-shadow: 0 16px 64px rgba(30, 32, 43, .16); }
:global(.work-folder-dialog .ant-modal-header) { padding: 22px 24px 0; margin: 0; background: transparent; border-bottom: 0; }
:global(.work-folder-dialog .ant-modal-body) { padding: 0; }
:global(.work-folder-dialog .ant-modal-close) { top: 18px; right: 18px; width: 32px; height: 32px; color: #92949d; }
:global(.work-folder-dialog .ant-modal-close-x) { width: 32px; height: 32px; line-height: 32px; }
.files-title {
  display: flex; align-items: center; gap: 12px; padding-right: 30px; color: @ink;
  > div { min-width: 0; }
  strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 16px; font-weight: 600; line-height: 24px; }
  small { display: block; color: @muted; font-size: 11px; font-weight: 400; line-height: 18px; }
}
.files-title-icon { display: grid; place-items: center; flex: 0 0 42px; height: 42px; border: 1px solid @line; border-radius: 12px; background: #f8f9fa; color: #727682; font-size: 21px; }
.work-folder-files { padding: 16px 24px 24px; }
.files-toolbar {
  display: flex; align-items: center; gap: 6px; padding: 18px 0 12px;
  > span { flex: 1; color: @muted; font-size: 12px; white-space: nowrap; }
}
.files-refresh { padding: 0 10px; border-radius: 8px; color: #777b87; font-size: 12px; }
.upload-input { display: none; }
.files-list { list-style: none; margin: 0; padding: 0; max-height: 50vh; overflow-y: auto; border-top: 1px solid @line;
  li { display: flex; align-items: center; gap: 12px; padding: 14px 2px; border-bottom: 1px solid #f2f3f5; &:last-child { border-bottom: 0; } }
}
.file-icon { display: grid; place-items: center; flex: 0 0 36px; height: 40px; border: 1px solid @line; border-radius: 9px; background: #fafafb; color: #858995; font-size: 19px; }
.file-info { flex: 1; min-width: 0; > span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: @ink; font-size: 13px; font-weight: 500; } small { display: block; margin-top: 4px; color: @muted; font-size: 11px; } }
.file-download { flex-shrink: 0; color: #858995; border-radius: 8px; }
.files-empty {
  display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 36px 8px 30px; border: 1px dashed #e5e6eb; border-radius: 12px; background: #fcfcfd;
  strong { color: #666b77; font-size: 13px; font-weight: 500; }
}
.empty-folder-icon { display: grid; place-items: center; width: 48px; height: 48px; margin-bottom: 4px; border: 1px solid @line; border-radius: 14px; background: #fff; color: #9599a5; font-size: 25px; }
@media (max-width: 600px) {
  :global(.work-folder-popover) { left: max(17px, calc((100vw - 294px) / 2)) !important; }
  .work-folder-trigger { min-height: 44px; max-width: 130px; padding: 0 7px; gap: 5px; font-size: 12px; }
  .folder-choice, .folder-action { min-height: 40px; }
  .work-folder-files { padding: 12px 16px 16px; }
  .create-folder { padding: 16px; }
  :global(.work-folder-create-dialog .ant-modal-header) { padding: 20px 16px 0; }
  :global(.work-folder-dialog .ant-modal-header) { padding: 20px 16px 0; }
  :global(.work-folder-dialog .ant-modal-close) { right: 12px; }
  .files-title { gap: 10px; strong { font-size: 15px; } }
  .files-title-icon { flex-basis: 36px; height: 36px; font-size: 18px; border-radius: 10px; }
}
@media (max-width: 400px) { .work-folder-trigger { max-width: 102px; } }
@media (max-width: 360px) {
  .work-folder-trigger { max-width: 88px; }
}
@media (prefers-reduced-motion: reduce) {
  .work-folder-trigger, .folder-choice, .folder-action { transition: none; }
}
</style>
