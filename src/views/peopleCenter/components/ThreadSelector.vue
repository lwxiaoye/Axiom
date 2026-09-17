<template>
  <!-- 「最近的对话」（2026-07-28，对标 Manus + 菜单里的「最近的任务」）：选中历史会话，
       后端把它的对话记录渲染成转录附件带进这一轮。条目行 + 飞出定位由 PlusSubmenu 统一给
       （与「我的文件」「知识库」同一个外壳），这里只管面板内容。 -->
  <PlusSubmenu
    label="最近的对话"
    desc="把之前聊过的内容带进这一轮"
    :count="count"
    @open="onOpen"
  >
    <template #icon><HistoryOutlined /></template>

    <div class="ts-search">
      <SearchOutlined />
      <!-- 搜索走服务端（标题 + 消息正文 LIKE），不是前端过滤：会话标题往往是自动生成的
           「关于…的讨论」，用户记得住的多半是当时聊过的内容而不是标题 -->
      <input v-model="searchKeyword" placeholder="搜索对话..." />
    </div>

    <div class="ts-list">
      <div v-if="loading && !options.length" class="ts-state">加载中...</div>
      <div v-else-if="!options.length" class="ts-state">
        {{ searchKeyword.trim() ? '没有匹配的对话' : '还没有别的历史对话' }}
      </div>
      <button
        v-for="item in options"
        :key="item.id"
        type="button"
        :class="['ts-item', { checked: isSelected(item.id), disabled: !isSelected(item.id) && atLimit }]"
        :disabled="!isSelected(item.id) && atLimit"
        :title="!isSelected(item.id) && atLimit ? `一次最多引用 ${MAX_REFS} 个对话` : item.title"
        @click="toggle(item)"
      >
        <span class="ts-check" aria-hidden="true">
          <CheckOutlined v-if="isSelected(item.id)" />
        </span>
        <span class="ts-ic" aria-hidden="true"><MessageOutlined /></span>
        <span class="ts-text">
          <span class="ts-name">{{ item.title }}</span>
          <span class="ts-meta">{{ formatWhen(item.updated_at || item.created_at) }}</span>
        </span>
      </button>
    </div>

    <div class="ts-footer">
      <span>{{ count ? `已选 ${count} / ${MAX_REFS} 个` : `最多可选 ${MAX_REFS} 个` }}</span>
      <button v-if="count" type="button" @click="clearAll">清空</button>
    </div>
  </PlusSubmenu>
</template>

<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from 'vue';
import { HistoryOutlined, SearchOutlined, CheckOutlined, MessageOutlined } from '@ant-design/icons-vue';
import { getThreads, type ThreadItem, type ThreadReference, type ThreadScope } from '../agentApi';
import PlusSubmenu from './PlusSubmenu.vue';

/** 与后端 thread_reference.MAX_THREADS 对齐：多选到第 4 个后端也只会读前 3 个并如实报告，
 *  前端先拦住比事后解释更好。改这里必须同步改后端常量。 */
const MAX_REFS = 3;
const LIST_LIMIT = 30;

const props = defineProps<{
  modelValue?: ThreadReference[];
  /** 当前会话 id：从候选里剔除自己——它的内容本来就在上下文里，引用自己纯属浪费 */
  currentThreadId?: string;
  scope?: ThreadScope;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: ThreadReference[]): void;
}>();

const loading = ref(false);
const searchKeyword = ref('');
const options = ref<ThreadItem[]>([]);

const selectedList = computed(() => props.modelValue || []);
const count = computed(() => selectedList.value.length);
const atLimit = computed(() => count.value >= MAX_REFS);

function isSelected(id: string) {
  return selectedList.value.some((t) => t.id === id);
}

function toggle(item: ThreadItem) {
  if (isSelected(item.id)) {
    emit('update:modelValue', selectedList.value.filter((t) => t.id !== item.id));
    return;
  }
  if (atLimit.value) return;
  emit('update:modelValue', [...selectedList.value, { id: item.id, title: item.title }]);
}

function clearAll() {
  emit('update:modelValue', []);
}

/** 相对时间：与历史抽屉的分组口径一致（今天/昨天/更早按日期） */
function formatWhen(raw?: string): string {
  if (!raw) return '';
  const time = new Date(raw);
  if (Number.isNaN(time.getTime())) return '';
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const ts = time.getTime();
  if (ts >= startOfToday) return `今天 ${String(time.getHours()).padStart(2, '0')}:${String(time.getMinutes()).padStart(2, '0')}`;
  if (ts >= startOfToday - 86400000) return '昨天';
  const days = Math.floor((startOfToday - ts) / 86400000) + 1;
  if (days < 7) return `${days} 天前`;
  return `${time.getMonth() + 1}月${time.getDate()}日`;
}

// 请求版本号：晚到的旧响应直接丢弃，不覆盖更新的一次结果（同 FileSelector 的做法）
let loadSeq = 0;
async function loadThreads() {
  const seq = (loadSeq += 1);
  loading.value = true;
  try {
    const list = await getThreads(
      searchKeyword.value.trim() || undefined,
      LIST_LIMIT,
      0,
      props.scope || 'ordinary',
    );
    if (seq !== loadSeq) return;
    const current = String(props.currentThreadId || '');
    options.value = list.filter((t) => t.id && t.id !== current);
  } catch (error) {
    if (seq !== loadSeq) return;
    console.warn('Failed to load threads:', error);
  } finally {
    if (seq === loadSeq) loading.value = false;
  }
}

let searchTimer: ReturnType<typeof setTimeout> | null = null;
watch(searchKeyword, () => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(loadThreads, 250);
});

/** 每次展开都重新拉：新会话、刚改的标题都要能立刻选到 */
function onOpen() {
  searchKeyword.value = '';
  loadThreads();
}

onUnmounted(() => {
  if (searchTimer) clearTimeout(searchTimer);
});
</script>

<style scoped>
/* 条目行与飞出面板的外壳样式在 PlusSubmenu 里，这里只写面板内容 */
.ts-search {
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

.ts-search:focus-within {
  border-color: #cbd0d8;
}

.ts-search input {
  width: 100%;
  border: 0;
  outline: none;
  appearance: none;
  background: transparent;
  color: #111827;
  font-size: 13px;
}

.ts-list {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  padding-right: 2px;
  scrollbar-width: thin;
  scrollbar-color: #cbd5e1 transparent;
}

.ts-list::-webkit-scrollbar {
  width: 6px;
}

.ts-list::-webkit-scrollbar-thumb {
  border-radius: 999px;
  background: #cbd5e1;
}

.ts-state {
  padding: 16px;
  color: #8a8f99;
  font-size: 13px;
  text-align: center;
}

.ts-item {
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

.ts-item:hover {
  background: #f0f1f3;
}

.ts-item.disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.ts-item.disabled:hover {
  background: transparent;
}

.ts-item:focus-visible,
.ts-footer button:focus-visible {
  outline: 1px solid #8f96a3;
  outline-offset: 0;
}

.ts-check {
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

.ts-item.checked .ts-check {
  border-color: #111827;
  background: #111827;
}

.ts-ic {
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

.ts-text {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 1px;
}

.ts-name {
  overflow: hidden;
  color: #374151;
  font-size: 13px;
  font-weight: 650;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.ts-meta {
  color: #8a8f99;
  font-size: 12px;
}

.ts-footer {
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

.ts-footer button {
  border: 0;
  background: transparent;
  color: #545861;
  cursor: pointer;
  font-size: 12px;
}

.ts-footer button:hover {
  color: #111827;
}

</style>
