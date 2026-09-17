<template>
  <!-- 「我的文件」不再是底栏的独立按钮（2026-07-28 用户拍板：收进 + 菜单，对标 Manus 的
       「最近的文件」）。条目行 + 飞出定位由 PlusSubmenu 统一给，这里只管面板内容。 -->
  <PlusSubmenu
    label="我的文件"
    desc="选已有的文件带进这一轮"
    :count="count"
    @open="onOpen"
  >
    <template #icon><FolderOutlined /></template>

    <div class="fs-search">
      <SearchOutlined />
      <input v-model="searchKeyword" placeholder="搜索文件..." />
    </div>

    <!-- 交付物白名单的逃生口（2026-07-28），与「我的文件」页同名同义：默认只列文档类产物，
         打开后连模型下载的材料、写的 .py 脚本一起列出来——它们照样能带进下一轮，
         之前选不到纯粹是选择器没跟上服务端的 show_all 参数 -->
    <button
      type="button"
      :class="['fs-scope', { on: showAll }]"
      :aria-pressed="showAll"
      title="默认只列文档类产物；打开后连同下载的材料、生成脚本等过程文件一起显示"
      @click="toggleShowAll"
    >
      <EyeOutlined />
      <span>显示全部文件</span>
    </button>

    <div class="fs-list">
      <div v-if="loading && !options.length" class="fs-state">加载中...</div>
      <div v-else-if="!filteredOptions.length" class="fs-state">
        {{ options.length ? '没有匹配的文件' : '暂无文件，可在「我的文件」板块上传' }}
      </div>
      <button
        v-for="row in filteredOptions"
        :key="row.file.id"
        type="button"
        :class="['fs-item', { checked: isSelected(row.file.id), disabled: !isSelected(row.file.id) && atLimit }]"
        :disabled="!isSelected(row.file.id) && atLimit"
        :title="!isSelected(row.file.id) && atLimit ? `一次最多引用 ${MAX_CHAT_FILE_REFS} 个文件` : row.file.filename"
        @click="toggle(row.file)"
      >
        <span class="fs-check" aria-hidden="true">
          <CheckOutlined v-if="isSelected(row.file.id)" />
        </span>
        <!-- 类型图标与「我的文件」页同一套（composables/fileKind.ts） -->
        <span :class="['fs-ic', `k-${row.kind}`]" aria-hidden="true">
          <component :is="row.icon" />
        </span>
        <span class="fs-text">
          <span class="fs-name">{{ row.file.filename }}</span>
          <span class="fs-sub">
            {{ formatSize(row.file.size) }} · {{ row.file.source === 'generated' ? '对话产物' : '已上传' }}
          </span>
        </span>
      </button>
    </div>

    <div v-if="filteredOptions.length || count" class="fs-footer">
      <span>{{ count ? `已选 ${count} / ${MAX_CHAT_FILE_REFS} 个` : `最多可选 ${MAX_CHAT_FILE_REFS} 个` }}</span>
      <button v-if="count" type="button" @click="clearAll">清空</button>
    </div>
  </PlusSubmenu>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { FolderOutlined, SearchOutlined, CheckOutlined, EyeOutlined } from '@ant-design/icons-vue';
import { listUserFiles, type UserFileItem, type UserFileSelection } from '../myfiles.api';
import {
  limitFileSelection,
  MAX_CHAT_FILE_REFS,
  PICKER_FOLDER_ID,
  pickerOptions,
  pruneSelection,
} from '../composables/filePicker';
import { KIND_ICON, fileKindOf } from '../composables/fileKind';
import PlusSubmenu from './PlusSubmenu.vue';

const props = defineProps<{
  modelValue?: UserFileSelection[];
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: UserFileSelection[]): void;
}>();

const loading = ref(false);
const searchKeyword = ref('');
const options = ref<UserFileItem[]>([]);
// 默认关（与「我的文件」页同一取舍）：面板高度只有 ~380px，把下载的 16 个源文件、
// 每一版 build.py 都铺进来会把真正想选的产物挤出可视区。开关是逃生口，不是默认视图。
const showAll = ref(false);

const selectedList = computed(() => props.modelValue || []);
const count = computed(() => selectedList.value.length);
const atLimit = computed(() => count.value >= MAX_CHAT_FILE_REFS);

/** 行数据带上类型与图标：模板里每行只算一次 kind（否则图标与类名各判定一次） */
const filteredOptions = computed(() => {
  const keyword = searchKeyword.value.trim().toLowerCase();
  const list = keyword
    ? options.value.filter((f) => f.filename.toLowerCase().includes(keyword))
    : options.value;
  return list.map((file) => {
    const kind = fileKindOf(file.filename, file.mime);
    return { file, kind, icon: KIND_ICON[kind] };
  });
});

function isSelected(id: string) {
  return selectedList.value.some((f) => f.id === id);
}

function toggle(file: UserFileItem) {
  if (isSelected(file.id)) {
    emit('update:modelValue', selectedList.value.filter((f) => f.id !== file.id));
  } else {
    if (atLimit.value) return;
    emit('update:modelValue', limitFileSelection([
      ...selectedList.value,
      { id: file.id, filename: file.filename },
    ]));
  }
}

function clearAll() {
  emit('update:modelValue', []);
}

function formatSize(bytes: number): string {
  if (!bytes || bytes < 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// 请求版本号：每次 loadFiles 自增并闭包捕获，响应回来时比对是否仍是最新一次——
// 晚到的旧响应（快速关闭再打开面板、或网络乱序）直接丢弃，不能整体覆盖更新的 options/已选状态
let loadSeq = 0;
async function loadFiles() {
  const seq = (loadSeq += 1);
  loading.value = true;
  try {
    // PICKER_FOLDER_ID('__all__')：选择器是扁平全量视图，含已归入文件夹的文件，否则
    // 整理进文件夹的文件反而选不到；顶层/文件夹浏览语义只属于「我的文件」管理页（handoff D3）。
    // showAll 决定服务端 deliverables_only：不带它，用户在「我的文件」里已经能看到的
    // .py/.json/材料，在这里依然选不进下一轮——「带进下一轮」那条路是断的（2026-07-28 修）。
    const data = await listUserFiles(PICKER_FOLDER_ID, showAll.value);
    if (seq !== loadSeq) return; // 已有更新的一次请求发出，这次响应过期
    options.value = pickerOptions(data.files);
    // 已选但已被删除/过期的文件从选中态剔除，防发送后端 404 降级文案。
    // 只在全量视图下剪：默认视图看不见非交付物，照它剪会把用户刚选好的 build.py 静默踢掉
    const kept = pruneSelection(selectedList.value, options.value, showAll.value);
    if (kept) emit('update:modelValue', kept);
  } catch (error) {
    if (seq !== loadSeq) return;
    console.warn('Failed to load user files:', error);
  } finally {
    if (seq === loadSeq) loading.value = false;
  }
}

/** 口径变了必须回服务端重取：默认清单里没下发的文件，前端筛不出来 */
function toggleShowAll() {
  showAll.value = !showAll.value;
  loadFiles();
}

/** 每次展开都刷新，避免新上传/删除的文件不同步 */
function onOpen() {
  searchKeyword.value = '';
  loadFiles();
}
</script>

<style scoped>
.fs-search {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
  height: 38px;
  padding: 0 11px;
  border: 1px solid #e8eaee;
  border-radius: 9px;
  margin-bottom: 8px;
  background: #f6f7f9;
  color: #7b8494;
  transition:
    border-color 0.16s ease,
    background-color 0.16s ease;
}

.fs-search:focus-within {
  border-color: #cbd0d8;
  background: #f6f7f9;
}

.fs-search input {
  width: 100%;
  border: 0;
  outline: none;
  appearance: none;
  background: transparent;
  color: #111827;
  font-size: 13px;
}

/* 逃生口开关：安静的次级控件，不跟条目抢注意力；开启态只用底色加深表示（黑白风格） */
.fs-scope {
  display: inline-flex;
  align-self: flex-start;
  align-items: center;
  gap: 5px;
  flex: none;
  height: 24px;
  margin-bottom: 8px;
  padding: 0 8px;
  border: 1px solid #eceef2;
  border-radius: 7px;
  background: transparent;
  color: #8a8f99;
  cursor: pointer;
  font-size: 12px;
  transition:
    border-color 0.16s ease,
    background 0.16s ease,
    color 0.16s ease;
}

.fs-scope:hover {
  border-color: #dcdfe5;
  background: #f6f7f9;
  color: #545861;
}

.fs-scope.on {
  border-color: #c8cad0;
  background: #f0f1f3;
  color: #111827;
}

.fs-scope:focus-visible,
.fs-item:focus-visible,
.fs-footer button:focus-visible {
  outline: 1px solid #8f96a3;
  outline-offset: 0;
}

.fs-list {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  padding-right: 2px;
  scrollbar-width: thin;
  scrollbar-color: #cbd5e1 transparent;
}

.fs-list::-webkit-scrollbar {
  width: 6px;
}

.fs-list::-webkit-scrollbar-thumb {
  border-radius: 999px;
  background: #cbd5e1;
}

.fs-state {
  padding: 16px;
  color: #8a8f99;
  font-size: 13px;
  text-align: center;
}

.fs-item {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  border: 0;
  border-radius: 10px;
  background: transparent;
  padding: 7px 8px;
  cursor: pointer;
  text-align: left;
  transition:
    background 0.15s ease,
    color 0.15s ease;
}

.fs-item:hover {
  background: #f0f1f3;
}

.fs-item:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.fs-item:disabled:hover {
  background: transparent;
}

.fs-check {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 18px;
  height: 18px;
  border: 1.5px solid #cfd2d9;
  border-radius: 5px;
  color: #fff;
  font-size: 11px;
}

.fs-item.checked .fs-check {
  border-color: #111827;
  background: #111827;
}

/* 文件类型底片：与「我的文件」页 .mf-ic 同一组低饱和 tint，只是尺寸收到 26px */
.fs-ic {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 26px;
  height: 26px;
  border-radius: 7px;
  background: #f3f3f3;
  color: #4b5563;
  font-size: 14px;
}

.fs-ic.k-word { background: #eef2fc; color: #3b5ba5; }
.fs-ic.k-excel { background: #ebf6ef; color: #2f8a5b; }
.fs-ic.k-pdf { background: #fceeee; color: #c0554f; }
.fs-ic.k-ppt { background: #fdf1e7; color: #c07a25; }
.fs-ic.k-image { background: #f1eefb; color: #6b52b8; }
.fs-ic.k-markdown,
.fs-ic.k-text { background: #eef1f4; color: #5b6472; }
.fs-ic.k-archive { background: #f2f0ec; color: #8a7a55; }
.fs-ic.k-other { background: #eef1f4; color: #5b6472; }

.fs-text {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 1px;
}

.fs-name {
  overflow: hidden;
  color: #374151;
  font-size: 13px;
  font-weight: 650;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.fs-sub {
  color: #8a8f99;
  font-size: 12px;
}

.fs-footer {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid #eef0f3;
  color: #8a8f99;
  font-size: 12px;
}

.fs-footer button {
  border: 0;
  background: transparent;
  color: #545861;
  cursor: pointer;
  font-size: 12px;
}

.fs-footer button:hover {
  color: #111827;
}
</style>
