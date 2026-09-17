<template>
  <span ref="hostRef" class="rrem-host" @click.stop="toggle">
    <slot />
  </span>
  <Teleport to="body">
    <div
      v-if="open"
      ref="menuRef"
      class="rrem-menu"
      role="menu"
      :style="menuStyle"
      @click.stop
    >
      <button type="button" class="rrem-item" role="menuitem" :disabled="busy" @click="run('copy')">
        {{ busy === 'copy' ? '正在复制…' : '复制内容' }}
      </button>
      <button type="button" class="rrem-item" role="menuitem" :disabled="busy" @click="run('md')">
        {{ busy === 'md' ? '正在导出…' : '导出到 Markdown' }}
      </button>
      <button type="button" class="rrem-item" role="menuitem" :disabled="busy" @click="run('word')">
        {{ busy === 'word' ? '正在导出…' : '导出到 Word' }}
      </button>
      <button type="button" class="rrem-item" role="menuitem" :disabled="busy" @click="run('pdf')">
        {{ busy === 'pdf' ? '正在导出…' : '导出到 PDF' }}
      </button>
    </div>
  </Teleport>
</template>

<script lang="ts">
let closeOpenMenu: (() => void) | null = null;
</script>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { fetchUserFileText } from '../myfiles.api';
import { copyText } from '../utils/clipboard';
import {
  downloadReportDocx,
  downloadReportMarkdown,
  downloadReportPdf,
  resolveReportExportSource,
  type ReportExportSource,
} from '../utils/researchReportExport';

const props = defineProps<{
  filename: string;
  fileId?: string;
  html?: string;
}>();

const hostRef = ref<HTMLElement | null>(null);
const menuRef = ref<HTMLElement | null>(null);
const open = ref(false);
const busy = ref<'copy' | 'md' | 'word' | 'pdf' | null>(null);
const menuStyle = ref<Record<string, string>>({});
let cached: ReportExportSource | null = null;

function close() {
  if (!open.value) return;
  open.value = false;
  if (closeOpenMenu === close) closeOpenMenu = null;
}

function placeMenu() {
  const trigger = hostRef.value?.querySelector('button') || hostRef.value;
  if (!trigger) return;
  const rect = trigger.getBoundingClientRect();
  const width = menuRef.value?.offsetWidth || 200;
  const left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8));
  menuStyle.value = {
    top: `${Math.round(rect.bottom + 8)}px`,
    left: `${Math.round(left)}px`,
  };
}

async function toggle() {
  if (busy.value) return;
  if (open.value) {
    close();
    return;
  }
  closeOpenMenu?.();
  open.value = true;
  closeOpenMenu = close;
  await nextTick();
  placeMenu();
  void loadSource().catch(() => undefined);
}

watch(
  () => [props.fileId, props.html, props.filename],
  () => {
    cached = null;
  },
);

async function loadSource(): Promise<ReportExportSource> {
  if (cached) return cached;
  let html = String(props.html || '');
  if (!html && props.fileId) html = await fetchUserFileText(props.fileId);
  if (!html.trim()) throw new Error('报告内容还没准备好');
  cached = resolveReportExportSource(html, props.filename);
  return cached;
}

async function run(kind: 'copy' | 'md' | 'word' | 'pdf') {
  if (busy.value) return;
  busy.value = kind;
  try {
    const source = await loadSource();
    if (kind === 'copy') {
      const ok = await copyText(source.markdown);
      if (!ok) {
        message.warning('复制失败，请手动选择文本后复制');
        return;
      }
      message.success('已复制');
    } else if (kind === 'md') {
      downloadReportMarkdown(source);
    } else if (kind === 'word') {
      downloadReportDocx(source);
    } else {
      await downloadReportPdf(source);
    }
    close();
  } catch (err) {
    message.error(err instanceof Error ? err.message : '导出失败，请稍后重试');
  } finally {
    busy.value = null;
  }
}

function onDocClick() {
  if (open.value && !busy.value) close();
}

function onKey(ev: KeyboardEvent) {
  if (ev.key === 'Escape' && open.value && !busy.value) close();
}

function onViewport() {
  if (!open.value) return;
  placeMenu();
}

onMounted(() => {
  document.addEventListener('click', onDocClick);
  window.addEventListener('keydown', onKey);
  window.addEventListener('resize', onViewport);
  window.addEventListener('scroll', onViewport, true);
});

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick);
  window.removeEventListener('keydown', onKey);
  window.removeEventListener('resize', onViewport);
  window.removeEventListener('scroll', onViewport, true);
  if (closeOpenMenu === close) closeOpenMenu = null;
});
</script>

<style>
.rrem-host {
  display: contents;
}

.rrem-menu {
  position: fixed;
  z-index: 2200;
  min-width: 200px;
  padding: 8px 6px;
  border: 1px solid rgba(15, 23, 42, 0.06);
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 8px 28px rgba(15, 23, 42, 0.12);
  animation: rrem-in 0.16s ease both;
}

.rrem-item {
  display: block;
  width: 100%;
  margin: 0;
  padding: 12px 16px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: #111827;
  font-family: inherit;
  font-size: 14px;
  line-height: 22px;
  text-align: left;
  cursor: pointer;
}

.rrem-item:hover:not(:disabled) {
  background: #f3f4f6;
}

.rrem-item:disabled {
  cursor: wait;
  opacity: 0.65;
}

@keyframes rrem-in {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .rrem-menu {
    animation: none;
  }
}
</style>
