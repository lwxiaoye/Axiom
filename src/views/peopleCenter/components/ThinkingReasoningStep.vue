<template>
  <div class="tr-step">
    <div
      class="tr-header"
      role="button"
      tabindex="0"
      :aria-expanded="expanded"
      aria-label="Toggle thoughts"
      @click="onHeaderClick"
      @keydown.enter.prevent="onHeaderClick"
      @keydown.space.prevent="onHeaderClick"
    >
      <span v-if="done" class="tr-label">
        Thoughts<template v-if="elapsed"> for {{ elapsed }}</template>
      </span>
      <span v-else class="tr-label">
        <span class="tr-shimmer">Thinking</span>
      </span>
      <PremiumChevron
        class="tr-chevron"
        :direction="expanded ? 'down' : 'right'"
        :size="13"
        interactive
      />
    </div>
    <div
      class="tr-collapsible"
      :class="{ 'is-collapsed': !expanded, 'is-live': streaming }"
    >
      <div class="tr-inner">
        <div v-if="streaming && displayed" class="tr-body">
          <p class="tr-p">{{ displayed }}</p>
        </div>
        <div v-else-if="paragraphs.length" class="tr-body">
          <p v-for="(para, i) in paragraphs" :key="i" class="tr-p">{{ para }}</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import PremiumChevron from './PremiumChevron.vue';
import { cancelStreamAnimationFrame, requestStreamAnimationFrame } from '../composables/streamAnimationFrame';
import {
  formatThoughtSeconds,
  splitThoughtParagraphs,
  thoughtStreamCatchupStep,
  THOUGHT_STREAM_FRAME_MS,
} from '../composables/thoughtReasoning';

const props = withDefaults(defineProps<{
  text?: string;
  seconds?: number;
  active?: boolean;
  open?: boolean;
}>(), {
  text: '',
  active: false,
  open: false,
});

const emit = defineEmits<{ toggle: [] }>();

const done = computed(() => !props.active);
const expanded = computed(() => Boolean(props.open));
const streaming = computed(() => Boolean(props.active));
const elapsed = computed(() => (done.value ? formatThoughtSeconds(props.seconds) : ''));
const displayed = ref('');
const paragraphs = computed(() => (
  streaming.value ? [] : splitThoughtParagraphs(displayed.value || props.text || '')
));

let target = '';
let raf = 0;
let lastPaintAt = 0;

function prefersReducedMotion() {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function cancelPaint() {
  if (raf) {
    cancelStreamAnimationFrame(raf);
    raf = 0;
  }
}

function paint(now: number) {
  raf = 0;
  if (!target) {
    displayed.value = '';
    return;
  }
  if (
    typeof document !== 'undefined' && document.hidden
    || prefersReducedMotion()
    || !streaming.value
    || !expanded.value
    || !target.startsWith(displayed.value)
  ) {
    displayed.value = target;
    return;
  }
  if (now - lastPaintAt < THOUGHT_STREAM_FRAME_MS) {
    raf = requestStreamAnimationFrame(paint);
    return;
  }
  const backlog = target.length - displayed.value.length;
  if (backlog <= 0) {
    displayed.value = target;
    return;
  }
  displayed.value = target.slice(0, displayed.value.length + thoughtStreamCatchupStep(backlog));
  lastPaintAt = now;
  if (displayed.value !== target) {
    raf = requestStreamAnimationFrame(paint);
  }
}

function syncText(next: string, instant: boolean) {
  target = String(next || '');
  if (instant || !expanded.value || prefersReducedMotion() || (typeof document !== 'undefined' && document.hidden)) {
    cancelPaint();
    displayed.value = target;
    return;
  }
  if (!target.startsWith(displayed.value)) {
    displayed.value = target;
    cancelPaint();
    return;
  }
  if (displayed.value !== target && !raf) {
    raf = requestStreamAnimationFrame(paint);
  }
}

watch(
  () => props.text,
  (next) => syncText(String(next || ''), !streaming.value),
  { immediate: true },
);

watch(streaming, (live) => {
  if (!live) syncText(String(props.text || ''), true);
});

watch(expanded, (open) => {
  if (!open) syncText(String(props.text || ''), true);
});

onUnmounted(cancelPaint);

function onHeaderClick() {
  emit('toggle');
}
</script>

<style scoped>
.tr-step {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  width: 100%;
  max-width: 100%;
  min-width: 0;
}

.tr-header {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 22px;
  margin: 0;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  text-align: left;
  appearance: none;
  -webkit-appearance: none;
}

.tr-label {
  font-family: inherit;
  font-size: 15px;
  line-height: 22px;
  font-weight: 400;
  color: #737373;
  letter-spacing: 0;
}

.tr-chevron {
  flex: none;
  color: #b0b4bb;
  opacity: 0;
  pointer-events: none;
  transition:
    opacity 0.15s ease,
    color 0.15s ease;
}

.tr-header:hover .tr-chevron,
.tr-header:focus-visible .tr-chevron {
  opacity: 1;
  color: #6b6f76;
}

.tr-collapsible {
  display: grid;
  grid-template-rows: 1fr;
  width: 100%;
  opacity: 1;
  transition:
    grid-template-rows 220ms ease,
    opacity 180ms ease;
}

.tr-collapsible.is-collapsed {
  grid-template-rows: 0fr;
  opacity: 0;
  pointer-events: none;
}

.tr-collapsible.is-live {
  display: block;
  opacity: 1;
  transition: none;
}

.tr-collapsible.is-live.is-collapsed {
  display: none;
}

.tr-inner {
  min-height: 0;
  overflow: hidden;
}

.tr-collapsible.is-live .tr-inner {
  overflow: visible;
}

.tr-body {
  margin: 10px 0 0;
  padding: 0 0 0 14px;
  border-left: 1px solid #ededed;
}

.tr-p {
  margin: 0 0 14px;
  color: #999;
  font-family:
    ui-sans-serif,
    -apple-system,
    system-ui,
    'Segoe UI',
    'PingFang SC',
    'Hiragino Sans GB',
    'Microsoft YaHei UI',
    'Microsoft YaHei',
    'Helvetica Neue',
    Arial,
    sans-serif;
  font-size: 15px;
  font-weight: 400;
  line-height: 1.55;
  letter-spacing: 0;
  white-space: pre-wrap;
  word-break: break-word;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

.tr-p:last-child {
  margin-bottom: 0;
}

.tr-shimmer {
  display: inline-block;
  color: transparent;
  -webkit-text-fill-color: transparent;
  background: linear-gradient(
    90deg,
    #737373 0%,
    #737373 30%,
    rgba(115, 115, 115, 0.42) 45%,
    rgba(115, 115, 115, 0.42) 55%,
    #737373 70%,
    #737373 100%
  );
  background-size: 300% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  animation: tr-shine 2.25s cubic-bezier(0.25, 0.1, 0.25, 1) infinite;
}

@keyframes tr-shine {
  0%, 18% { background-position: 100% 0; }
  82%, 100% { background-position: 0% 0; }
}

@media (prefers-reduced-motion: reduce) {
  .tr-shimmer {
    animation: none;
    color: #737373;
    -webkit-text-fill-color: #737373;
    background: none;
  }

  .tr-collapsible {
    transition: none;
  }
}
</style>
