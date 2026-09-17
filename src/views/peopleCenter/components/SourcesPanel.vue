<template>
  <div>
    <transition name="sources-fade">
      <div v-if="open" class="sources-backdrop" @click="emit('close')"></div>
    </transition>
    <transition name="sources-slide">
      <aside v-if="open" class="sources-panel" aria-label="搜索结果">
        <header class="sources-head">
          <div class="sources-title">
            <GlobalOutlined />
            <strong>搜索结果</strong>
            <em class="sources-count">{{ sources.length }}</em>
          </div>
          <button type="button" class="sources-close" title="关闭" @click="emit('close')">
            <CloseOutlined />
          </button>
        </header>

        <div class="sources-body">
          <component
            :is="src.url ? 'a' : 'div'"
            v-for="(src, i) in sources"
            :key="i"
            class="source-card"
            :class="{ 'no-link': !src.url }"
            :href="src.url || undefined"
            :target="src.url ? '_blank' : undefined"
            rel="noopener noreferrer"
          >
            <div class="source-card-head">
              <span :class="['source-mark', src.type]">
                <ReadOutlined v-if="src.type === 'knowledge'" />
                <FileTextOutlined v-else-if="src.type === 'file'" />
                <GlobalOutlined v-else />
              </span>
              <span class="source-origin">{{ originLabel(src) }}</span>
              <span class="source-index">{{ i + 1 }}</span>
            </div>
            <p class="source-title">{{ src.title || src.url || '来源' }}</p>
            <p v-if="cleanSnippet(src.snippet)" class="source-snippet">{{ cleanSnippet(src.snippet) }}</p>
            <span v-if="src.url" class="source-go"><LinkOutlined /> 打开原网页</span>
          </component>

          <div v-if="!sources.length" class="sources-empty">暂无引用来源</div>
        </div>
      </aside>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount } from 'vue';
import {
  CloseOutlined,
  FileTextOutlined,
  GlobalOutlined,
  LinkOutlined,
  ReadOutlined,
} from '@ant-design/icons-vue';

export interface SourceItem {
  type?: string;
  title?: string;
  url?: string;
  source?: string;
  source_label?: string;
  snippet?: string;
}

const props = defineProps<{
  open: boolean;
  sources: SourceItem[];
}>();

const emit = defineEmits<{
  (e: 'close'): void;
}>();

// 摘要清洗：搜索来源的 snippet 常夹带 markdown 图片/链接语法和 <Base64-Image-Removed>
// 之类的占位标记，直接展示很脏。去掉图片、把链接压成纯文本、抹掉占位尖括号、合并空白。
function cleanSnippet(raw?: string): string {
  if (!raw) return '';
  return raw
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '') // markdown 图片
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1') // markdown 链接 → 文本
    .replace(/<[^>]*>/g, '') // <Base64-Image-Removed> 等占位/HTML 标记
    .replace(/https?:\/\/\S+/g, '') // 裸 URL
    .replace(/[*_`>#]+/g, '') // 残留 markdown 记号
    .replace(/\s+/g, ' ') // 合并空白与换行
    .trim();
}

// 来源标识：优先后端给的站名，否则从 URL 提取主机名，最后回落到类型标签。
function originLabel(src: SourceItem): string {
  if (src.source_label) return src.source_label;
  if (src.source) return src.source;
  if (src.url) {
    try {
      return new URL(src.url).hostname.replace(/^www\./, '');
    } catch {
      /* 非法 URL 落到类型标签 */
    }
  }
  if (src.type === 'knowledge') return '知识库';
  if (src.type === 'file') return '文件';
  return '网页';
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && props.open) emit('close');
}

onMounted(() => document.addEventListener('keydown', onKey));
onBeforeUnmount(() => document.removeEventListener('keydown', onKey));
</script>

<style scoped>
.sources-backdrop {
  position: fixed;
  inset: 0;
  z-index: 2400;
  background: rgba(17, 24, 39, 0.18);
  backdrop-filter: blur(1px);
}

.sources-panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  z-index: 2401;
  display: flex;
  width: min(420px, 92vw);
  flex-direction: column;
  border-left: 1px solid #e3e5ea;
  background: #fff;
  box-shadow: -18px 0 48px rgba(15, 23, 42, 0.14);
}

.sources-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 52px;
  padding: 0 12px 0 18px;
  border-bottom: 1px solid #eef0f3;
}

.sources-title {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #111827;
  font-size: 15px;
}

.sources-title strong {
  font-weight: 600;
}

.sources-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  border-radius: 999px;
  background: #f0f1f3;
  color: #4b5563;
  font-size: 12px;
  font-style: normal;
}

.sources-close {
  display: grid;
  width: 30px;
  height: 30px;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #6b7280;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.sources-close:hover {
  background: #f0f1f4;
  color: #111;
}

.sources-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: #fbfbfc;
}

/* 每条来源一张可点击卡片，整卡跳转对应网页 */
.source-card {
  display: block;
  position: relative;
  border: 1px solid #ebecf0;
  border-radius: 12px;
  background: #fff;
  padding: 12px 14px;
  text-decoration: none;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease;
}

a.source-card:hover {
  border-color: #d4d6dc;
  box-shadow: 0 6px 18px rgba(15, 23, 42, 0.06);
  transform: translateY(-1px);
}

.source-card.no-link {
  cursor: default;
}

.source-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 7px;
}

.source-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  background: #f0f1f3;
  color: #5b6069;
  font-size: 13px;
}

.source-mark.knowledge {
  background: #e8effe;
  color: #1d4ed8;
}

.source-mark.file {
  background: #fbf1e4;
  color: #b45309;
}

.source-origin {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: #6b7280;
  font-size: 12px;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.source-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 5px;
  background: #f4f5f7;
  color: #9098a3;
  font-size: 11px;
  font-weight: 600;
}

.source-title {
  margin: 0;
  color: #1a1d23;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

a.source-card:hover .source-title {
  color: #000;
}

.source-snippet {
  margin: 6px 0 0;
  color: #6b7280;
  font-size: 12.5px;
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.source-go {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 9px;
  color: #9098a3;
  font-size: 12px;
  transition: color 0.15s ease;
}

a.source-card:hover .source-go {
  color: #111827;
}

.sources-empty {
  padding: 40px 0;
  color: #9aa0ac;
  font-size: 13px;
  text-align: center;
}

.sources-slide-enter-active,
.sources-slide-leave-active {
  transition: transform 0.26s cubic-bezier(0.22, 1, 0.36, 1);
}

.sources-slide-enter-from,
.sources-slide-leave-to {
  transform: translateX(100%);
}

.sources-fade-enter-active,
.sources-fade-leave-active {
  transition: opacity 0.2s ease;
}

.sources-fade-enter-from,
.sources-fade-leave-to {
  opacity: 0;
}
</style>
