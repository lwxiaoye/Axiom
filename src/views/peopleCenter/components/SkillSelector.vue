<template>
  <!-- 「使用技能」（2026-07-28 用户拍板：Skill 从此有两个对话框内入口——`@` 面板与这里）。
       与 `@` 共用同一套一次性语义：选中即带进这一轮，发送后清空。条目行与飞出定位由
       PlusSubmenu 统一给，这里只管面板内容。 -->
  <PlusSubmenu
    label="使用技能"
    :count="selected.length"
    @open="onOpen"
  >
    <!-- 条目图标＝侧栏「Skill广场」那一项的图标（ToolOutlined），两处指的是同一件事 -->
    <template #icon><ToolOutlined /></template>

    <div class="sk-search">
      <SearchOutlined />
      <input v-model="keyword" placeholder="搜索技能..." />
    </div>

    <div class="sk-list">
      <div v-if="pending && !skills.length" class="sk-state">加载中...</div>
      <div v-else-if="!filtered.length" class="sk-state">
        {{ skills.length ? '没有匹配的技能' : '暂无可用技能，可在 Skill 广场安装' }}
      </div>
      <button
        v-for="item in filtered"
        :key="item.id"
        type="button"
        :class="['sk-item', { checked: isSelected(item.id) }]"
        :title="item.description || item.name"
        @click="toggle(item)"
      >
        <span class="sk-check" aria-hidden="true">
          <CheckOutlined v-if="isSelected(item.id)" />
        </span>
        <!-- 每个技能的图标 + 品牌色沿用 Skill 广场那一套（composables/skillVisual.ts）：
             用户在广场认得的橙色 PPT 方块，到对话框里必须还是它 -->
        <span class="sk-ic" :style="{ background: visualOf(item).bg }" aria-hidden="true">
          <component :is="visualOf(item).icon" />
        </span>
        <!-- 不显示来源（上传/内置/网络）：这里是"挑一个技能用"，装没装、从哪来是
             Skill 广场的事，摆在每行右边只会跟名字抢注意力（2026-07-28 用户拍板） -->
        <span class="sk-text">
          <span class="sk-name">{{ item.name }}</span>
          <span v-if="item.description" class="sk-desc">{{ item.description }}</span>
        </span>
      </button>
    </div>

    <div class="sk-footer">
      <span>选中的技能这一轮生效，发送后自动清空</span>
    </div>
  </PlusSubmenu>
</template>

<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from 'vue';
import { ToolOutlined, SearchOutlined, CheckOutlined } from '@ant-design/icons-vue';
import type { SkillItem } from '../agentApi';
import { skillVisualOf } from '../composables/skillVisual';
import PlusSubmenu from './PlusSubmenu.vue';

const props = defineProps<{
  /** 可选技能全集（与 `@` 面板同一份数据） */
  skills: SkillItem[];
  /** 已选（一次性，随发送清空） */
  selected: SkillItem[];
}>();

const emit = defineEmits<{
  (e: 'select', item: SkillItem): void;
  (e: 'remove', id: string): void;
  /** 技能清单是懒加载的（`@` 面板打开时才拉），这个入口得自己触发一次，否则永远是空列表 */
  (e: 'ensure'): void;
}>();

const keyword = ref('');

const filtered = computed(() => {
  const q = keyword.value.trim().toLowerCase();
  if (!q) return props.skills;
  return props.skills.filter(
    (s) => s.name.toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q),
  );
});

/** 与 Skill 广场同一份判据 */
function visualOf(item: SkillItem) {
  return skillVisualOf(item.name, item.skillId);
}

function isSelected(id: string) {
  return props.selected.some((s) => s.id === id);
}

/** 已选的再点一次＝取消（与 `@` 面板不同：那里没有"再点取消"的位置） */
function toggle(item: SkillItem) {
  if (isSelected(item.id)) emit('remove', item.id);
  else emit('select', item);
}

// 技能清单是懒加载的：首次展开时父组件才去拉。这期间列表是空的，直接显示
// 「暂无可用技能」会是**假的空态**——明明马上就有。用 pending 兜一段加载中。
const pending = ref(false);
let pendingTimer: ReturnType<typeof setTimeout> | null = null;

watch(
  () => props.skills.length,
  (n) => {
    if (n) pending.value = false;
  },
);

/** 每次展开清掉上次的搜索词，并确保技能清单已拉过一次（父组件内部做了幂等） */
function onOpen() {
  keyword.value = '';
  emit('ensure');
  if (props.skills.length) return;
  pending.value = true;
  // 兜底：请求失败时父组件把清单置空、不会有任何信号回来，不能让它永远转圈
  if (pendingTimer) clearTimeout(pendingTimer);
  pendingTimer = setTimeout(() => {
    pending.value = false;
  }, 8000);
}

onUnmounted(() => {
  if (pendingTimer) clearTimeout(pendingTimer);
});
</script>

<style scoped>
.sk-search {
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

.sk-search:focus-within {
  border-color: #cbd0d8;
  background: #f6f7f9;
}

.sk-search input {
  width: 100%;
  border: 0;
  outline: none;
  appearance: none;
  background: transparent;
  color: #111827;
  font-size: 13px;
}

.sk-list {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  padding-right: 2px;
  scrollbar-width: thin;
  scrollbar-color: #cbd5e1 transparent;
}

.sk-list::-webkit-scrollbar {
  width: 6px;
}

.sk-list::-webkit-scrollbar-thumb {
  border-radius: 999px;
  background: #cbd5e1;
}

.sk-state {
  padding: 16px;
  color: #8a8f99;
  font-size: 13px;
  text-align: center;
}

.sk-item {
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

.sk-item:hover {
  background: #f0f1f3;
}

.sk-item:focus-visible {
  outline: 1px solid #8f96a3;
  outline-offset: 0;
}

.sk-check {
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

.sk-item.checked .sk-check {
  border-color: #111827;
  background: #111827;
}

/* 与文件/知识库面板的图标底片同尺寸同圆角；底色是 skillVisual 的品牌渐变（行内 style 覆盖），
   图标本身走白色——与 Skill 广场卡片上的 48px 方块是同一套视觉，只是尺寸收到 26px */
.sk-ic {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 26px;
  height: 26px;
  border-radius: 7px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: #fff;
  font-size: 14px;
}

.sk-text {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 1px;
}

.sk-name {
  overflow: hidden;
  color: #374151;
  font-size: 13px;
  font-weight: 650;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.sk-desc {
  overflow: hidden;
  color: #8a8f99;
  font-size: 12px;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.sk-footer {
  flex: none;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid #eef0f3;
  color: #8a8f99;
  font-size: 12px;
}
</style>
