<template>
  <span
    :class="['execution-action-icon', `kind-${normalizedKind}`, `status-${status}`, reviewClass]"
    role="img"
    :aria-label="label"
    :title="label"
  >
    <!-- Codex 同源 Lucide 路径（从 ChatGPT.app asar 提取），viewBox 24 / stroke 2 -->
    <!-- eslint-disable-next-line vue/no-v-html -- 静态常量表 -->
    <svg class="eai-svg" viewBox="0 0 24 24" aria-hidden="true" v-html="glyph" />
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue';

export type ExecutionActionIconKind =
  | 'view'
  | 'files'
  | 'read'
  | 'search'
  | 'web'
  | 'knowledge'
  | 'edit'
  | 'create'
  | 'download'
  | 'load'
  | 'bash'
  | 'office'
  | 'convert'
  | 'skill'
  | 'memory'
  | 'ask'
  | 'artifact'
  | 'review'
  | 'tool'

const props = withDefaults(defineProps<{
  kind?: ExecutionActionIconKind | string;
  status?: 'running' | 'completed' | 'failed';
  reviewStatus?: 'passed' | 'warning' | 'failed' | string;
}>(), {
  kind: 'tool',
  status: 'completed',
  reviewStatus: '',
});

/**
 * 与 Codex 桌面端同一套 Lucide 图标几何（SquareTerminal / Search / Globe / FileText ...）。
 * 路径取自本机 ChatGPT.app webview Lucide 模块，避免手绘偏差。
 */
const ICONS: Record<ExecutionActionIconKind | 'fail', string> = {
  bash: '<path d="m7 11 2-2-2-2"/><path d="M11 13h4"/><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>',
  read: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
  create: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M9 15h6"/><path d="M12 18v-6"/>',
  edit: '<path d="M12 20h9"/><path d="M16.376 3.622a1 1 0 0 1 3.002 3.002L7.368 18.635a2 2 0 0 1-.855.506l-2.872.838a.5.5 0 0 1-.62-.62l.838-2.872a2 2 0 0 1 .506-.854z"/>',
  files: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  web: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
  knowledge: '<path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>',
  skill: '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/>',
  ask: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  memory: '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><path d="M9 18h6"/><path d="M10 22h4"/>',
  load: '<polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>',
  office: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M8 13h2"/><path d="M14 13h2"/><path d="M8 17h2"/><path d="M14 17h2"/>',
  convert: '<path d="M8 3 4 7l4 4"/><path d="M4 7h16"/><path d="m16 21 4-4-4-4"/><path d="M20 17H4"/>',
  review: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
  artifact: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="m9 15 2 2 4-4"/>',
  view: '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/><circle cx="12" cy="12" r="3"/>',
  tool: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  fail: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
};

const labels: Record<ExecutionActionIconKind, string> = {
  view: '查看',
  files: '文件',
  read: '读取',
  search: '搜索',
  web: '打开网页',
  knowledge: '检索知识库',
  edit: '编辑',
  create: '创建',
  download: '下载文件',
  load: '读取完整结果',
  bash: '在沙箱中执行',
  office: '处理 Office 文件',
  convert: '转换格式',
  skill: '使用技能',
  memory: '更新记忆',
  ask: '请求补充信息',
  artifact: '文件产物',
  review: '审查',
  tool: '工具',
};

const normalizedKind = computed<ExecutionActionIconKind>(() => (
  Object.prototype.hasOwnProperty.call(labels, props.kind) ? props.kind as ExecutionActionIconKind : 'tool'
));

const glyph = computed(() => (
  props.status === 'failed' ? ICONS.fail : ICONS[normalizedKind.value]
));
const label = computed(() => {
  if (props.status === 'running') return `正在${labels[normalizedKind.value]}`;
  if (props.status === 'failed') return `${labels[normalizedKind.value]}失败`;
  return labels[normalizedKind.value];
});
const reviewClass = computed(() => (
  normalizedKind.value === 'review' && props.reviewStatus ? `review-${props.reviewStatus}` : ''
));
</script>

<style scoped>
.execution-action-icon {
  display: inline-flex;
  width: 15px;
  height: 15px;
  flex: none;
  align-items: center;
  justify-content: center;
  color: inherit;
  line-height: 1;
}

/* Lucide 默认：stroke 2 / round caps；14px 显示时视觉接近 Codex 时间线 */
.eai-svg {
  display: block;
  width: 14px;
  height: 14px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.status-running { color: #5f6470; }
.status-failed,
.review-failed { color: #8e939c; }
.kind-review.review-passed,
.kind-review.review-warning { color: #8a8f99; }
</style>
