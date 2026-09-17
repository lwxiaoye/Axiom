<template>
  <Teleport to="body">
    <div v-if="open" class="ws-layer">
      <button
        class="ws-backdrop"
        type="button"
        aria-label="关闭工作区"
        @click="$emit('close')"
      ></button>
      <aside
        class="ws-card"
        :class="{ 'is-drop': dropActive }"
        role="dialog"
        aria-modal="true"
        aria-label="工作区"
        @click="closePopups"
        @dragenter="onDragEnter"
        @dragover="onDragOver"
        @dragleave="onDragLeave"
        @drop="onDrop"
      >
        <div class="ws-heading">
          <strong>工作区</strong>
          <div class="ws-heading-actions">
            <button
              class="ws-ghost"
              type="button"
              :disabled="!threadId || busy"
              @click.stop="pickUpload"
            >
              <UploadOutlined />
              上传
            </button>
          </div>
        </div>

        <div v-if="showToolbar" class="ws-toolbar">
          <label class="ws-search">
            <SearchOutlined />
            <input
              v-model="query"
              type="search"
              placeholder="搜索文件..."
              @input="onSearch"
              @click.stop
            />
          </label>
          <div class="ws-filter" @click.stop>
            <button type="button" class="ws-filter-btn" @click="toggleFilter">
              <span>{{ kindLabel }}</span>
              <DownOutlined />
            </button>
            <div v-if="filterOpen" class="ws-pop">
              <button
                v-for="option in kindOptions"
                :key="option.value"
                type="button"
                :class="{ on: kind === option.value }"
                @click="selectKind(option.value)"
              >
                {{ option.label }}
              </button>
            </div>
          </div>
        </div>
        <input
          ref="fileInput"
          class="ws-file"
          type="file"
          multiple
          @change="onUpload"
        />

        <div v-if="!threadId || (!loading && files.length === 0)" class="ws-empty">
          <FolderOpenOutlined />
          <strong>{{ query || kind !== 'all' ? '未找到文件' : '暂无文件' }}</strong>
          <p v-if="threadId && !query && kind === 'all'">把文件拖到这里，或点右上角上传。输入框附件也会进本会话工作区。</p>
        </div>
        <div v-else-if="loading" class="ws-empty">
          <LoadingOutlined />
        </div>
        <div v-else class="ws-table-wrap">
          <table class="ws-table">
            <thead>
              <tr>
                <th>名称</th>
                <th>修改时间</th>
                <th>变化</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in files" :key="item.id">
                <td>
                  <div class="ws-name">
                    <span class="ws-icon" :class="'tone-' + fileTone(item)">
                      <component :is="fileIcon(item)" />
                    </span>
                    <span class="ws-name-text">
                      <b>{{ item.name }}</b>
                      <em>{{ fileMeta(item) }}</em>
                    </span>
                  </div>
                </td>
                <td class="ws-time">{{ formatTime(item.updated_at) }}</td>
                <td class="ws-change">
                  <span class="ws-diff">
                    <span :class="item.added ? 'plus' : 'zero'">+{{ item.added }}</span>
                    <span :class="item.removed ? 'minus' : 'zero'">-{{ item.removed }}</span>
                  </span>
                </td>
                <td class="ws-more">
                  <button type="button" aria-label="更多" @click.stop="toggleMenu($event, item)">
                    <EllipsisOutlined />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="dropActive" class="ws-drop-mask" aria-hidden="true">
          <UploadOutlined />
          <strong>放到这里上传</strong>
        </div>
      </aside>
      <div
        v-if="menu"
        class="ws-pop ws-row-pop"
        :style="{ top: `${menu.top}px`, right: `${menu.right}px` }"
        @click.stop
      >
        <button type="button" @click="downloadFile(menu.item)">下载</button>
        <button
          v-if="menu.item.kind === 'asset'"
          type="button"
          @click="removeFile(menu.item)"
        >
          删除
        </button>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import {
  DownOutlined,
  EllipsisOutlined,
  FileExcelFilled,
  FileFilled,
  FileImageFilled,
  FilePdfFilled,
  FilePptFilled,
  FileWordFilled,
  FolderOpenOutlined,
  LoadingOutlined,
  SearchOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue';
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import {
  deleteWorkspaceFile,
  downloadWorkspaceFile,
  listWorkspace,
  uploadWorkspaceFile,
  type WorkspaceFileItem,
} from '../agentApi';

const props = defineProps<{
  open: boolean;
  threadId: string;
}>();

const emit = defineEmits<{
  close: [];
  error: [unknown];
}>();

const query = ref('');
const kind = ref('all');
const loading = ref(false);
const busy = ref(false);
const files = ref<WorkspaceFileItem[]>([]);
const filterOpen = ref(false);
const menu = ref<{ item: WorkspaceFileItem; top: number; right: number } | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const dropActive = ref(false);
let searchTimer: number | null = null;
let dragDepth = 0;

const kindOptions = [
  { value: 'all', label: '全部类型' },
  { value: 'image', label: '图片' },
  { value: 'pdf', label: 'PDF' },
  { value: 'office', label: 'Office' },
  { value: 'uploaded', label: '我上传的' },
] as const;

const kindLabel = computed(
  () => kindOptions.find((option) => option.value === kind.value)?.label || '全部类型',
);

const showToolbar = computed(
  () => files.value.length > 0 || Boolean(query.value.trim()) || kind.value !== 'all',
);

watch(
  () => [props.open, props.threadId] as const,
  ([open]) => {
    if (open) void reload();
    else {
      closePopups();
      resetDrop();
    }
  },
);

onMounted(() => {
  window.addEventListener('keydown', onKeydown);
  window.addEventListener('center-workspace-changed', onWorkspaceChanged);
});
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown);
  window.removeEventListener('center-workspace-changed', onWorkspaceChanged);
  if (searchTimer != null) window.clearTimeout(searchTimer);
});

function onWorkspaceChanged(event: Event) {
  const detail = (event as CustomEvent<{ threadId?: string }>).detail;
  if (!props.open) return;
  if (detail?.threadId && detail.threadId !== props.threadId) return;
  void reload();
}

function onKeydown(event: KeyboardEvent) {
  if (!props.open) return;
  if (event.key === 'Escape') {
    if (menu.value || filterOpen.value) closePopups();
    else emit('close');
  }
}

function closePopups() {
  menu.value = null;
  filterOpen.value = false;
}

function toggleFilter() {
  menu.value = null;
  filterOpen.value = !filterOpen.value;
}

function selectKind(value: string) {
  kind.value = value;
  filterOpen.value = false;
  void reload();
}

function onSearch() {
  if (searchTimer != null) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    void reload();
  }, 180);
}

async function reload() {
  if (!props.open) return;
  if (!props.threadId) {
    files.value = [];
    return;
  }
  loading.value = true;
  try {
    const data = await listWorkspace(props.threadId, {
      q: query.value.trim(),
      kind: kind.value === 'all' ? '' : kind.value,
    });
    files.value = data.files;
  } catch (error) {
    emit('error', error);
  } finally {
    loading.value = false;
  }
}

function pickUpload() {
  fileInput.value?.click();
}

function hasDragFiles(event: DragEvent) {
  return Array.from(event.dataTransfer?.types || []).includes('Files');
}

function resetDrop() {
  dragDepth = 0;
  dropActive.value = false;
}

function onDragEnter(event: DragEvent) {
  if (!props.threadId || busy.value || !hasDragFiles(event)) return;
  event.preventDefault();
  dragDepth += 1;
  dropActive.value = true;
}

function onDragOver(event: DragEvent) {
  if (!props.threadId || busy.value || !hasDragFiles(event)) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
}

function onDragLeave(event: DragEvent) {
  if (!hasDragFiles(event)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) dropActive.value = false;
}

function onDrop(event: DragEvent) {
  event.preventDefault();
  resetDrop();
  closePopups();
  const chosen = Array.from(event.dataTransfer?.files || []).filter((file) => file?.name);
  void uploadPicked(chosen);
}

async function onUpload(event: Event) {
  const input = event.target as HTMLInputElement;
  const chosen = Array.from(input.files || []);
  input.value = '';
  await uploadPicked(chosen);
}

async function uploadPicked(chosen: File[]) {
  if (!props.threadId || !chosen.length) return;
  busy.value = true;
  try {
    for (const file of chosen) {
      await uploadWorkspaceFile(props.threadId, file);
    }
    await reload();
  } catch (error) {
    emit('error', error);
  } finally {
    busy.value = false;
  }
}

async function removeFile(item: WorkspaceFileItem) {
  closePopups();
  if (!props.threadId || item.kind !== 'asset') return;
  busy.value = true;
  try {
    await deleteWorkspaceFile(props.threadId, item.id);
    await reload();
  } catch (error) {
    emit('error', error);
  } finally {
    busy.value = false;
  }
}

function toggleMenu(event: MouseEvent, item: WorkspaceFileItem) {
  filterOpen.value = false;
  if (menu.value?.item.id === item.id) {
    menu.value = null;
    return;
  }
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
  menu.value = {
    item,
    top: Math.round(rect.bottom + 4),
    right: Math.round(Math.max(8, window.innerWidth - rect.right)),
  };
}

async function downloadFile(item: WorkspaceFileItem) {
  closePopups();
  if (!props.threadId) return;
  try {
    await downloadWorkspaceFile(props.threadId, item.id, item.name);
  } catch (error) {
    emit('error', error);
  }
}

function fileTone(item: WorkspaceFileItem) {
  const name = String(item.name || '').toLowerCase();
  if (/\.(png|jpe?g|gif|webp|svg|bmp)$/.test(name)) return 'img';
  if (name.endsWith('.pdf')) return 'pdf';
  if (/\.(pptx?|ppt)$/.test(name)) return 'ppt';
  if (/\.docx?$/.test(name)) return 'word';
  if (/\.xlsx?$/.test(name)) return 'xls';
  return 'file';
}

function fileIcon(item: WorkspaceFileItem) {
  const tone = fileTone(item);
  if (tone === 'img') return FileImageFilled;
  if (tone === 'pdf') return FilePdfFilled;
  if (tone === 'ppt') return FilePptFilled;
  if (tone === 'word') return FileWordFilled;
  if (tone === 'xls') return FileExcelFilled;
  return FileFilled;
}

function fileExt(name: string) {
  const index = String(name || '').lastIndexOf('.');
  if (index < 0) return 'FILE';
  return name.slice(index + 1).toUpperCase();
}

function formatSize(bytes: number) {
  const size = Number(bytes || 0);
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) {
    const kb = size / 1024;
    return kb < 10 ? `${kb.toFixed(1)} KB` : `${Math.round(kb)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function fileMeta(item: WorkspaceFileItem) {
  return `${fileExt(item.name)} • ${formatSize(item.size_bytes)}`;
}

function formatTime(value?: string) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const diff = Date.now() - date.getTime();
  if (diff < 45 * 1000) return '刚刚更新';
  if (diff < 60 * 60 * 1000) {
    const minutes = Math.max(1, Math.floor(diff / 60000));
    return `${minutes}分钟前`;
  }
  if (diff < 24 * 60 * 60 * 1000) {
    const hours = Math.max(1, Math.floor(diff / 3600000));
    return `${hours}小时前`;
  }
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const yesterday = new Date(startOfToday.getTime() - 86400000);
  if (date >= yesterday && date < startOfToday) return '昨天';
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}
</script>

<style scoped lang="less">
.ws-layer {
  position: fixed;
  z-index: 3000;
  top: 64px;
  right: 0;
  bottom: 0;
  left: var(--center-nav-width, 280px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  pointer-events: none;
  transition: left 0.28s cubic-bezier(0.22, 1, 0.36, 1);
}

.ws-backdrop {
  position: absolute;
  inset: 0;
  border: 0;
  background: rgba(17, 24, 39, 0.18);
  cursor: default;
  pointer-events: auto;
}

.ws-card {
  position: relative;
  z-index: 1;
  display: flex;
  width: 620px;
  max-width: 100%;
  max-height: min(640px, calc(100vh - 128px));
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 24px 64px rgba(15, 23, 42, 0.18);
  padding: 22px 24px 16px;
  pointer-events: auto;
  animation: ws-card-enter 0.2s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.ws-card.is-drop {
  outline: 2px dashed #111;
  outline-offset: -10px;
}

.ws-drop-mask {
  position: absolute;
  inset: 0;
  z-index: 8;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: rgba(255, 255, 255, 0.9);
  color: #111827;
  font-size: 28px;
  pointer-events: none;
}

.ws-drop-mask strong {
  font-size: 14px;
  font-weight: 600;
}

.ws-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  color: #111827;
}

.ws-heading strong {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.ws-heading-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.ws-ghost {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #4b5563;
  font-size: 13px;
  cursor: pointer;
}

.ws-ghost:hover:not(:disabled) {
  background: #f5f5f7;
  color: #111827;
}

.ws-ghost:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.ws-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.ws-search {
  display: flex;
  flex: 1;
  min-width: 0;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 12px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  color: #9ca3af;
  background: #fff;
}

.ws-search input {
  flex: 1;
  min-width: 0;
  height: 34px;
  border: 0;
  outline: none;
  background: transparent;
  color: #111827;
  font-size: 13px;
}

.ws-search input::-webkit-search-decoration,
.ws-search input::-webkit-search-cancel-button {
  -webkit-appearance: none;
}

.ws-search input::placeholder {
  color: #c4c7ce;
}

.ws-filter {
  position: relative;
  flex-shrink: 0;
}

.ws-filter-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 12px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  color: #4b5563;
  font-size: 13px;
  cursor: pointer;
}

.ws-filter-btn :deep(.anticon) {
  font-size: 10px;
  color: #9ca3af;
}

.ws-pop {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 6;
  display: flex;
  min-width: 120px;
  flex-direction: column;
  padding: 4px;
  border: 1px solid #eceef1;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.12);
}

.ws-pop button {
  padding: 7px 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #374151;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.ws-pop button:hover,
.ws-pop button.on {
  background: #f5f5f7;
}

.ws-row-pop {
  position: fixed;
  top: 0;
  /* 不能继承 .ws-pop 的 right:0，否则 left+right 会把菜单横向拉满到视口右缘 */
  right: auto;
  left: auto;
  width: max-content;
  z-index: 4000;
  pointer-events: auto;
}

.ws-file {
  display: none;
}

.ws-table-wrap {
  flex: 1;
  min-height: 0;
  overflow: auto;
  margin: 4px -8px 0;
  padding: 0 8px;
}

.ws-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 13px;
}

.ws-table th,
.ws-table td {
  padding: 12px 8px;
  border-bottom: 1px solid #f3f4f6;
  text-align: left;
  vertical-align: middle;
}

.ws-table th {
  color: #9ca3af;
  font-size: 12px;
  font-weight: 500;
}

.ws-table th:nth-child(1),
.ws-table td:nth-child(1) {
  width: auto;
}

.ws-table th:nth-child(2),
.ws-table td:nth-child(2) {
  width: 92px;
}

.ws-table th:nth-child(3),
.ws-table td:nth-child(3) {
  width: 88px;
}

.ws-table th:nth-child(4),
.ws-table td:nth-child(4) {
  width: 40px;
  padding-left: 0;
  padding-right: 4px;
}

.ws-table tbody tr:hover td {
  background: #fafafa;
}

.ws-name {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
}

.ws-icon {
  display: grid;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  place-items: center;
  font-size: 22px;
  line-height: 1;
}

.ws-icon.tone-ppt { color: #f97316; }
.ws-icon.tone-word { color: #3b82f6; }
.ws-icon.tone-pdf { color: #ef4444; }
.ws-icon.tone-img { color: #14b8a6; }
.ws-icon.tone-xls { color: #22c55e; }
.ws-icon.tone-file { color: #9ca3af; }

.ws-name-text {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.ws-name-text b {
  overflow: hidden;
  color: #111827;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ws-name-text em {
  color: #9ca3af;
  font-size: 12px;
  font-style: normal;
}

.ws-time {
  color: #9ca3af;
  font-size: 12px;
  white-space: nowrap;
}

.ws-change {
  white-space: nowrap;
}

.ws-diff {
  display: inline-flex;
  gap: 6px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.ws-diff .plus { color: #16a34a; }
.ws-diff .minus { color: #ef4444; }
.ws-diff .zero { color: #d1d5db; }

.ws-more {
  position: relative;
  text-align: right;
}

.ws-more > button {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #9ca3af;
  cursor: pointer;
}

.ws-more > button:hover {
  background: #f3f4f6;
  color: #4b5563;
}

.ws-empty {
  display: flex;
  flex: 1;
  min-height: 240px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: #d1d5db;
  font-size: 28px;
  text-align: center;
}

.ws-empty strong {
  color: #9ca3af;
  font-size: 13px;
  font-weight: 400;
}

.ws-empty p {
  max-width: 280px;
  margin: 0;
  color: #9ca3af;
  font-size: 12px;
  line-height: 1.5;
}

@keyframes ws-card-enter {
  from { opacity: 0; }
  to { opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .ws-layer {
    transition: none;
  }
  .ws-card {
    animation: none;
  }
}

@media (max-width: 980px) {
  .ws-layer {
    top: 56px;
    left: 0;
    padding: 16px;
  }
  .ws-card {
    width: 100%;
    max-height: calc(100vh - 88px);
    border-radius: 14px;
  }
}
</style>
