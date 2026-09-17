<template>
  <div class="cc-step" :aria-label="ariaLabel">
    <template v-if="active">
      <span class="cc-bullet" :style="{ color: bulletColor }" aria-hidden="true">•</span>
      <span class="cc-label">
        <span
          v-for="(ch, index) in chars"
          :key="index"
          class="cc-ch"
          :style="{ color: charColor(index) }"
        >{{ ch }}</span>
      </span>
      <span class="cc-elapsed">({{ elapsedLabel }})</span>
    </template>
    <span v-else class="cc-label">{{ doneLabel }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';

/**
 * Codex TUI compaction process:
 *   status row: shimmer "•" + shimmer header + dim "(Ns)"
 *   transcript after ItemCompleted: dim "Context compacted"
 * Sweep math is copied from codex-rs/tui/src/shimmer.rs (2s cosine band).
 */
const PROCESS_START = Date.now();
const LABEL = 'Compacting context';
const SWEEP_MS = 2000;
const PADDING = 10;
const BAND_HALF = 5;
const BASE = [115, 115, 115] as const;
const HIGHLIGHT = [245, 245, 245] as const;

const props = withDefaults(defineProps<{
  active?: boolean;
  failed?: boolean;
  seconds?: number;
  startedAt?: number;
}>(), {
  active: false,
  failed: false,
});

const chars = LABEL.split('');
const nowTick = ref(Date.now());
const reduced = ref(false);
let raf = 0;
let elapsedTimer = 0;

const doneLabel = computed(() => (
  props.failed ? 'Context compaction failed' : 'Context compacted'
));
const liveSeconds = computed(() => {
  if (typeof props.seconds === 'number' && props.seconds >= 0 && !props.active) {
    return props.seconds;
  }
  const started = props.startedAt || nowTick.value;
  return Math.max(0, Math.floor((nowTick.value - started) / 1000));
});
const elapsedLabel = computed(() => formatElapsed(liveSeconds.value));
const ariaLabel = computed(() => (
  props.active ? `Compacting context ${elapsedLabel.value}` : doneLabel.value
));
const bulletColor = computed(() => charColor(-PADDING));

function formatElapsed(seconds: number): string {
  const n = Math.max(0, Math.floor(seconds));
  if (n < 60) return `${n}s`;
  if (n < 3600) {
    const m = Math.floor(n / 60);
    const s = n % 60;
    return `${m}m ${String(s).padStart(2, '0')}s`;
  }
  const h = Math.floor(n / 3600);
  const m = Math.floor((n % 3600) / 60);
  const s = n % 60;
  return `${h}h ${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`;
}

function blend(a: readonly [number, number, number], b: readonly [number, number, number], t: number) {
  const k = Math.min(1, Math.max(0, t));
  return [
    Math.round(a[0] + (b[0] - a[0]) * k),
    Math.round(a[1] + (b[1] - a[1]) * k),
    Math.round(a[2] + (b[2] - a[2]) * k),
  ] as const;
}

function bandT(index: number): number {
  const period = chars.length + PADDING * 2;
  const pos = ((nowTick.value - PROCESS_START) % SWEEP_MS) / SWEEP_MS * period;
  const dist = Math.abs(index + PADDING - pos);
  if (dist > BAND_HALF) return 0;
  const x = Math.PI * (dist / BAND_HALF);
  return 0.5 * (1 + Math.cos(x));
}

function charColor(index: number): string {
  if (reduced.value) return '#737373';
  const [r, g, b] = blend(BASE, HIGHLIGHT, bandT(index) * 0.9);
  return `rgb(${r}, ${g}, ${b})`;
}

function tick() {
  nowTick.value = Date.now();
  raf = requestAnimationFrame(tick);
}

function stopMotion() {
  if (raf) {
    cancelAnimationFrame(raf);
    raf = 0;
  }
  if (elapsedTimer) {
    window.clearInterval(elapsedTimer);
    elapsedTimer = 0;
  }
}

function startMotion() {
  stopMotion();
  nowTick.value = Date.now();
  if (reduced.value) {
    elapsedTimer = window.setInterval(() => {
      nowTick.value = Date.now();
    }, 1000);
    return;
  }
  raf = requestAnimationFrame(tick);
}

watch(() => props.active, (active) => {
  if (active) startMotion();
  else stopMotion();
});

onMounted(() => {
  reduced.value = typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (props.active) startMotion();
});

onUnmounted(() => {
  stopMotion();
});
</script>

<style scoped>
.cc-step {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-height: 22px;
}

.cc-bullet {
  display: inline-block;
  width: 10px;
  font-size: 15px;
  line-height: 22px;
  font-weight: 700;
}

.cc-label {
  font-family: inherit;
  font-size: 15px;
  line-height: 22px;
  font-weight: 400;
  color: #737373;
  letter-spacing: 0;
}

.cc-ch {
  font-weight: 600;
}

.cc-elapsed {
  font-size: 15px;
  line-height: 22px;
  color: #a3a3a3;
  font-weight: 400;
}

@media (prefers-reduced-motion: reduce) {
  .cc-ch,
  .cc-bullet {
    font-weight: 400;
    color: #737373 !important;
  }
}
</style>
