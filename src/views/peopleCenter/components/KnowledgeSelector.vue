<template>
  <!-- 「知识库」与「我的文件」同一形态（2026-07-28 用户拍板：一起收进 + 菜单）：
       条目行 + 飞出定位由 PlusSubmenu 统一给，这里只管面板内容。 -->
  <PlusSubmenu
    label="知识库"
    desc="让这一轮回答基于选中的知识库"
    :count="count"
    @open="onOpen"
  >
    <template #icon><ReadOutlined /></template>

    <div class="kb-search">
      <SearchOutlined />
      <input v-model="searchKeyword" placeholder="搜索知识库..." />
    </div>

    <div class="kb-list">
      <div v-if="loading && !options.length" class="kb-state">加载中...</div>
      <div v-else-if="!filteredOptions.length" class="kb-state">
        {{ options.length ? '没有匹配的知识库' : '暂无可用知识库' }}
      </div>
      <button
        v-for="kb in filteredOptions"
        :key="kb.id"
        type="button"
        :class="['kb-item', { checked: isSelected(kb.id), disabled: !isKnowledgeReady(kb) }]"
        :disabled="!isKnowledgeReady(kb)"
        :title="isKnowledgeReady(kb) ? kb.name : '该知识库尚无可检索内容，完成处理后才能引用'"
        @click="toggle(kb)"
      >
        <span class="kb-check" aria-hidden="true">
          <CheckOutlined v-if="isSelected(kb.id)" />
        </span>
        <!-- 图标底片与「我的文件」面板同尺寸同形状，两个面板并排看是一套东西 -->
        <span class="kb-ic" aria-hidden="true"><ReadOutlined /></span>
        <span class="kb-text">
          <span class="kb-name-row">
            <span class="kb-name">{{ kb.name }}</span>
            <span v-if="!kb.chunkCount" class="kb-badge" title="该知识库暂无可检索内容，选中后无法命中">未就绪</span>
          </span>
          <span class="kb-sub">
            <span class="kb-seg">{{ kb.chunkCount ? `${kb.chunkCount} 个分段` : '暂无可检索内容' }}</span>
            <span v-if="kb.description" class="kb-desc">· {{ kb.description }}</span>
          </span>
        </span>
      </button>
    </div>

    <div v-if="filteredOptions.length" class="kb-footer">
      <span>已选 {{ count }} 个</span>
      <div class="kb-footer-actions">
        <button type="button" :disabled="!selectableFilteredOptions.length" @click="toggleSelectAll">
          {{ allFilteredSelected ? '取消全选' : '全选可用' }}
        </button>
        <button v-if="count" type="button" @click="clearAll">清空</button>
      </div>
    </div>
  </PlusSubmenu>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { ReadOutlined, SearchOutlined, CheckOutlined } from '@ant-design/icons-vue';
import { getKnowledgeList } from '../../knowledge/knowledge.api';
import type { KnowledgeBase, KnowledgePermission } from '../../knowledge/knowledge.types';
import type { KnowledgeSelection } from '../agentApi';
import {
  isKnowledgeReady,
  pruneKnowledgeSelection,
  readyKnowledgeOptions,
} from '../composables/knowledgePicker';
import PlusSubmenu from './PlusSubmenu.vue';

interface KbOption {
  id: string;
  name: string;
  description?: string;
  permission?: KnowledgePermission;
  chunkCount?: number;
}

const props = defineProps<{
  modelValue?: KnowledgeSelection[];
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: KnowledgeSelection[]): void;
}>();

const loading = ref(false);
const searchKeyword = ref('');
const options = ref<KbOption[]>([]);

const selectedList = computed(() => props.modelValue || []);
const count = computed(() => selectedList.value.length);

const filteredOptions = computed(() => {
  const keyword = searchKeyword.value.trim().toLowerCase();
  if (!keyword) return options.value;
  return options.value.filter(
    (kb) => kb.name.toLowerCase().includes(keyword) || (kb.description || '').toLowerCase().includes(keyword),
  );
});

const selectableFilteredOptions = computed(() => readyKnowledgeOptions(filteredOptions.value));

const allFilteredSelected = computed(
  () => selectableFilteredOptions.value.length > 0
    && selectableFilteredOptions.value.every((kb) => isSelected(kb.id)),
);

function isSelected(id: string) {
  return selectedList.value.some((k) => k.id === id);
}

function toggle(kb: KbOption) {
  if (!isKnowledgeReady(kb)) return;
  if (isSelected(kb.id)) {
    emit('update:modelValue', selectedList.value.filter((k) => k.id !== kb.id));
  } else {
    emit('update:modelValue', [
      ...selectedList.value,
      { id: kb.id, name: kb.name, permission: kb.permission },
    ]);
  }
}

function clearAll() {
  emit('update:modelValue', []);
}

function toggleSelectAll() {
  const filtered = selectableFilteredOptions.value;
  if (!filtered.length) return;
  if (allFilteredSelected.value) {
    // 取消全选：仅移除当前筛选出的这些（保留搜索框外已选的）
    const filteredIds = new Set(filtered.map((kb) => kb.id));
    emit('update:modelValue', selectedList.value.filter((k) => !filteredIds.has(k.id)));
  } else {
    // 全选：把当前筛选出的全部并入已选（去重）
    const existing = new Set(selectedList.value.map((k) => k.id));
    const additions = filtered
      .filter((kb) => !existing.has(kb.id))
      .map((kb) => ({ id: kb.id, name: kb.name, permission: kb.permission }));
    emit('update:modelValue', [...selectedList.value, ...additions]);
  }
}

function resolvePermission(kb: KnowledgeBase): KnowledgePermission | undefined {
  return kb.currentPermission || kb.accessPermission || kb.aclPermission || kb.permission || undefined;
}

// 请求版本号（与 FileSelector 同一模式）：晚到的旧响应不许覆盖新一次的结果
let loadSeq = 0;
async function loadKnowledge() {
  const seq = (loadSeq += 1);
  loading.value = true;
  try {
    const [owned, shared] = await Promise.all([
      getKnowledgeList({ pageNo: 1, pageSize: 200, scope: 'owned' }),
      getKnowledgeList({ pageNo: 1, pageSize: 200, scope: 'shared' }),
    ]);
    if (seq !== loadSeq) return;
    const merged = [
      ...(owned?.records || []),
      ...(shared?.records || []),
    ];
    const seen = new Set<string>();
    options.value = merged
      .filter((kb) => kb && kb.id && kb.status !== 'DISABLED')
      .filter((kb) => (seen.has(kb.id) ? false : (seen.add(kb.id), true)))
      .map((kb) => ({
        id: kb.id,
        name: kb.name,
        description: kb.description,
        permission: resolvePermission(kb),
        chunkCount: Number(kb.chunkCount || 0),
      }));
    const kept = pruneKnowledgeSelection(selectedList.value, options.value);
    if (kept) emit('update:modelValue', kept);
  } catch (error) {
    if (seq !== loadSeq) return;
    console.warn('Failed to load knowledge bases:', error);
  } finally {
    if (seq === loadSeq) loading.value = false;
  }
}

/** 每次展开都刷新，避免新建/删除的知识库不同步 */
function onOpen() {
  searchKeyword.value = '';
  loadKnowledge();
}
</script>

<style scoped>
.kb-search {
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

.kb-search:focus-within {
  border-color: #cbd0d8;
  background: #f6f7f9;
}

.kb-search input {
  width: 100%;
  border: 0;
  outline: none;
  appearance: none;
  background: transparent;
  color: #111827;
  font-size: 13px;
}

.kb-list {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  padding-right: 2px;
  scrollbar-width: thin;
  scrollbar-color: #cbd5e1 transparent;
}

.kb-list::-webkit-scrollbar {
  width: 6px;
}

.kb-list::-webkit-scrollbar-thumb {
  border-radius: 999px;
  background: #cbd5e1;
}

.kb-state {
  padding: 16px;
  color: #8a8f99;
  font-size: 13px;
  text-align: center;
}

.kb-item {
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

.kb-item:hover {
  background: #f0f1f3;
}

.kb-item:disabled {
  cursor: not-allowed;
  opacity: 0.58;
}

.kb-item:disabled:hover {
  background: transparent;
}

.kb-item:focus-visible,
.kb-footer button:focus-visible {
  outline: 1px solid #8f96a3;
  outline-offset: 0;
}

.kb-check {
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

.kb-item.checked .kb-check {
  border-color: #111827;
  background: #111827;
}

/* 与文件面板的 .fs-ic 同尺寸同圆角，知识库只有一种类型，用中性灰底 */
.kb-ic {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 26px;
  height: 26px;
  border-radius: 7px;
  background: #eef1f4;
  color: #5b6472;
  font-size: 14px;
}

.kb-text {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 1px;
}

.kb-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.kb-name {
  overflow: hidden;
  color: #374151;
  font-size: 13px;
  font-weight: 650;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.kb-badge {
  flex: none;
  border-radius: 5px;
  background: #f2f4f7;
  padding: 1px 6px;
  color: #98a0ad;
  font-size: 11px;
  font-weight: 600;
}

.kb-sub {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  color: #8a8f99;
  font-size: 12px;
}

.kb-seg {
  flex: none;
}

.kb-desc {
  overflow: hidden;
  min-width: 0;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.kb-footer {
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

.kb-footer-actions {
  display: inline-flex;
  align-items: center;
  gap: 14px;
}

.kb-footer button {
  border: 0;
  background: transparent;
  color: #545861;
  cursor: pointer;
  font-size: 12px;
}

.kb-footer button:hover {
  color: #111827;
}

.kb-footer button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}
</style>
