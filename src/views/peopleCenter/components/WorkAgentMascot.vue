<template>
  <div
    ref="rootRef"
    :class="[
      'work-agent-mascot',
      `variant-${props.variant}`,
      `is-${props.state}`,
      {
        'is-poked': poked,
      },
    ]"
    role="button"
    tabindex="0"
    :aria-label="props.label"
    @click="greet"
    @keydown.enter.prevent="greet"
    @keydown.space.prevent="greet"
  >
    <svg
      v-if="props.variant === 'main'"
      class="mascot-main-art"
      viewBox="0 0 57 44"
      overflow="visible"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <radialGradient id="work-agent-orb-fill" cx="32%" cy="18%" r="88%" fx="32%" fy="18%">
          <stop offset="0" stop-color="#1a79ff" />
          <stop offset="0.48" stop-color="#0b64f8" />
          <stop offset="0.78" stop-color="#0758ee" />
          <stop offset="1" stop-color="#0349d9" />
        </radialGradient>
        <linearGradient id="work-agent-eye-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#ffffff" />
          <stop offset="1" stop-color="#f7f9ff" />
        </linearGradient>
        <filter id="work-agent-orb-shadow" x="-30%" y="-250%" width="160%" height="600%" color-interpolation-filters="sRGB">
          <feGaussianBlur stdDeviation="1.8" />
        </filter>
      </defs>

      <ellipse
        class="mascot-shadow"
        cx="27"
        cy="40.2"
        rx="17.5"
        ry="1.85"
        fill="#75a9ff"
        opacity="0.24"
        filter="url(#work-agent-orb-shadow)"
      />
      <g class="mascot-orb">
        <path
          class="mascot-body"
          d="M26.8 1.2C38.1 1.2 46.5 10.1 46.5 21.1C46.5 32 38.1 40.7 26.8 40.7C15.5 40.7 7.2 32 7.2 21.1C7.2 10.1 15.6 1.2 26.8 1.2Z"
          fill="url(#work-agent-orb-fill)"
        />
        <g class="mascot-eye-look">
          <g class="mascot-eyes">
            <ellipse class="mascot-eye mascot-eye-left" cx="26.2" cy="19.5" rx="2.55" ry="4.65" fill="url(#work-agent-eye-fill)" />
            <ellipse class="mascot-eye mascot-eye-right" cx="35.55" cy="19.05" rx="2.2" ry="4.55" fill="url(#work-agent-eye-fill)" />
          </g>
        </g>
      </g>
      <g class="mascot-discovery" aria-hidden="true">
        <path d="M49.1 4.1L50.05 6.55L52.5 7.5L50.05 8.45L49.1 10.9L48.15 8.45L45.7 7.5L48.15 6.55Z" fill="#4f91ff" />
        <circle cx="53.05" cy="3.8" r="0.8" fill="#9dc3ff" />
      </g>
    </svg>

    <div v-else class="mascot-themed-art" aria-hidden="true">
      <div class="mascot-orb mascot-themed-orb">
        <img :src="themedArt.src" alt="" draggable="false" />
        <svg class="mascot-themed-eye-layer" :viewBox="themedArt.viewBox || '0 0 512 512'" overflow="visible" focusable="false">
          <g class="mascot-eyes">
            <ellipse
              class="mascot-eye mascot-eye-left"
              :cx="themedArt.leftEye.cx"
              :cy="themedArt.leftEye.cy"
              :rx="themedArt.leftEye.rx"
              :ry="themedArt.leftEye.ry"
              fill="#fff"
            />
            <ellipse
              class="mascot-eye mascot-eye-right"
              :cx="themedArt.rightEye.cx"
              :cy="themedArt.rightEye.cy"
              :rx="themedArt.rightEye.rx"
              :ry="themedArt.rightEye.ry"
              fill="#fff"
            />
          </g>
        </svg>
      </div>
      <svg class="mascot-themed-discovery" viewBox="0 0 32 32" overflow="visible" focusable="false">
        <g class="mascot-discovery">
          <path d="M15.5 2.5L18.2 10.3L26 13L18.2 15.7L15.5 23.5L12.8 15.7L5 13L12.8 10.3Z" fill="#4f91ff" />
          <circle cx="26.5" cy="5.5" r="2.1" fill="#9dc3ff" />
        </g>
      </svg>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue';
import type { WorkAgentMascotState } from '../utils/workAgentMascotState';

type WorkAgentMascotVariant = 'main' | 'campus' | 'presentation' | 'interview';

type EyeGeometry = {
  cx: number;
  cy: number;
  rx: number;
  ry: number;
};

type ThemedMascotArt = {
  src: string;
  viewBox?: string;
  leftEye: EyeGeometry;
  rightEye: EyeGeometry;
};

const THEMED_MASCOT_ART: Record<Exclude<WorkAgentMascotVariant, 'main'>, ThemedMascotArt> = {
  campus: {
    src: '/agent-icons/builtin/campus-services-mascot-v3.png',
    leftEye: { cx: 224, cy: 266, rx: 15.5, ry: 27.5 },
    rightEye: { cx: 278, cy: 264, rx: 14, ry: 27 },
  },
  presentation: {
    src: '/agent-icons/builtin/presentation-assistant-mascot-v5.svg',
    leftEye: { cx: 185, cy: 334, rx: 19, ry: 35 },
    rightEye: { cx: 258, cy: 331, rx: 17, ry: 34 },
  },
  interview: {
    src: '/agent-icons/builtin/interview-assistant-mascot-v3.svg',
    viewBox: '0 0 57 44',
    leftEye: { cx: 26.2, cy: 19.5, rx: 2.55, ry: 4.65 },
    rightEye: { cx: 35.55, cy: 19.05, rx: 2.2, ry: 4.55 },
  },
};

const props = withDefaults(defineProps<{
  state?: WorkAgentMascotState;
  variant?: WorkAgentMascotVariant;
  label?: string;
}>(), {
  state: 'idle',
  variant: 'main',
  label: '和 AI 助手互动',
});

const rootRef = ref<HTMLElement | null>(null);
const poked = ref(false);
const themedArt = computed<ThemedMascotArt>(() => (
  THEMED_MASCOT_ART[props.variant === 'main' ? 'campus' : props.variant]
));

let pokeTimer: ReturnType<typeof setTimeout> | undefined;
let trackingHost: HTMLElement | null = null;
let motionQuery: MediaQueryList | null = null;
let lookRaf = 0;
let lookX = 0;
let lookY = 0;
let targetLookX = 0;
let targetLookY = 0;
let coarsePointerId: number | null = null;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function isCoarsePointer(event: PointerEvent): boolean {
  return event.pointerType === 'touch' || event.pointerType === 'pen';
}

function applyLook() {
  rootRef.value?.style.setProperty('--mascot-look-x', `${lookX.toFixed(2)}px`);
  rootRef.value?.style.setProperty('--mascot-look-y', `${lookY.toFixed(2)}px`);
}

function stepLook() {
  lookRaf = 0;
  const easing = motionQuery?.matches ? 1 : 0.19;
  lookX += (targetLookX - lookX) * easing;
  lookY += (targetLookY - lookY) * easing;
  applyLook();
  if (Math.abs(targetLookX - lookX) > 0.01 || Math.abs(targetLookY - lookY) > 0.01) {
    lookRaf = requestAnimationFrame(stepLook);
  }
}

function requestLookFrame() {
  if (!lookRaf) lookRaf = requestAnimationFrame(stepLook);
}

function updateLookFromPointer(event: PointerEvent) {
  const rect = rootRef.value?.getBoundingClientRect();
  if (!rect) return;
  const composerRect = rootRef.value?.closest<HTMLElement>('.composer')?.getBoundingClientRect();
  const eyeCenterX = rect.left + rect.width / 2;
  const deltaX = event.clientX - eyeCenterX;
  const deltaY = event.clientY - (rect.top + rect.height / 2);
  const leftBoundary = composerRect?.left ?? trackingHost?.getBoundingClientRect().left ?? eyeCenterX - 120;
  const rightBoundary = composerRect?.right ?? trackingHost?.getBoundingClientRect().right ?? eyeCenterX + 120;
  const horizontalRange = deltaX < 0
    ? Math.max(1, eyeCenterX - leftBoundary)
    : Math.max(1, rightBoundary - eyeCenterX);
  targetLookX = clamp(deltaX / horizontalRange, -1, 1) * 1.75;
  targetLookY = Math.tanh(deltaY / 120) * 1.05;
  requestLookFrame();
}

function onTrackingPointerMove(event: PointerEvent) {
  if (motionQuery?.matches) return;
  if (isCoarsePointer(event) && coarsePointerId === null) return;
  updateLookFromPointer(event);
}

function onTrackingPointerDown(event: PointerEvent) {
  if (motionQuery?.matches || !isCoarsePointer(event) || coarsePointerId !== null) return;
  coarsePointerId = event.pointerId;
  window.addEventListener('pointermove', onCoarsePointerMove, { passive: true });
  window.addEventListener('pointerup', onCoarsePointerUp, { passive: true });
  window.addEventListener('pointercancel', onCoarsePointerUp, { passive: true });
  updateLookFromPointer(event);
}

function onCoarsePointerMove(event: PointerEvent) {
  if (motionQuery?.matches || event.pointerId !== coarsePointerId) return;
  updateLookFromPointer(event);
}

function onCoarsePointerUp(event: PointerEvent) {
  if (event.pointerId !== coarsePointerId) return;
  stopCoarseLook();
  resetLook();
}

function stopCoarseLook() {
  coarsePointerId = null;
  window.removeEventListener('pointermove', onCoarsePointerMove);
  window.removeEventListener('pointerup', onCoarsePointerUp);
  window.removeEventListener('pointercancel', onCoarsePointerUp);
}

function resetLook() {
  targetLookX = 0;
  targetLookY = 0;
  requestLookFrame();
}

function onTrackingPointerLeave() {
  if (coarsePointerId !== null) return;
  resetLook();
}

function onMotionPreferenceChange() {
  if (!motionQuery?.matches) return;
  lookX = 0;
  lookY = 0;
  targetLookX = 0;
  targetLookY = 0;
  applyLook();
}

async function greet() {
  if (pokeTimer) clearTimeout(pokeTimer);
  poked.value = false;
  await nextTick();
  poked.value = true;
  pokeTimer = setTimeout(() => {
    poked.value = false;
    pokeTimer = undefined;
  }, props.variant === 'presentation' ? 900 : props.variant === 'campus' ? 820 : 720);
}

onMounted(() => {
  trackingHost = rootRef.value?.closest<HTMLElement>('.chat-home') || null;
  trackingHost?.addEventListener('pointermove', onTrackingPointerMove, { passive: true });
  trackingHost?.addEventListener('pointerdown', onTrackingPointerDown, { passive: true });
  trackingHost?.addEventListener('pointerleave', onTrackingPointerLeave);
  motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  motionQuery.addEventListener('change', onMotionPreferenceChange);
});

onUnmounted(() => {
  if (pokeTimer) clearTimeout(pokeTimer);
  if (lookRaf) cancelAnimationFrame(lookRaf);
  stopCoarseLook();
  trackingHost?.removeEventListener('pointermove', onTrackingPointerMove);
  trackingHost?.removeEventListener('pointerdown', onTrackingPointerDown);
  trackingHost?.removeEventListener('pointerleave', onTrackingPointerLeave);
  motionQuery?.removeEventListener('change', onMotionPreferenceChange);
});
</script>

<style scoped>
.work-agent-mascot {
  --mascot-look-x: 0px;
  --mascot-look-y: 0px;
  display: block;
  overflow: visible;
  cursor: pointer;
  outline: none;
  pointer-events: auto;
  touch-action: manipulation;
  user-select: none;
  -webkit-tap-highlight-color: transparent;
}

.work-agent-mascot.variant-main,
.work-agent-mascot.variant-interview {
  aspect-ratio: 57 / 44;
}

.work-agent-mascot.variant-campus,
.work-agent-mascot.variant-presentation {
  aspect-ratio: 1;
}

.work-agent-mascot:focus-visible {
  outline: 2px solid rgba(41, 112, 241, 0.4);
  outline-offset: 2px;
  border-radius: 50%;
}

.mascot-main-art,
.mascot-themed-art,
.mascot-themed-art img,
.mascot-themed-eye-layer,
.mascot-themed-discovery {
  display: block;
  width: 100%;
  height: 100%;
}

.mascot-main-art,
.mascot-themed-art,
.mascot-themed-orb {
  overflow: visible;
}

.mascot-main-art,
.mascot-themed-art {
  transform-origin: 50% 92%;
}

.mascot-themed-art {
  position: relative;
}

.mascot-themed-orb,
.mascot-themed-eye-layer,
.mascot-themed-discovery {
  position: absolute;
  inset: 0;
}

/* 各形象保留自己的视图盒；直接移动图层，保证眼睛位移按屏幕像素等量可见。 */
.mascot-themed-eye-layer {
  transform: translate3d(var(--mascot-look-x), var(--mascot-look-y), 0);
  will-change: transform;
}

.mascot-themed-art img {
  object-fit: contain;
  pointer-events: none;
}

.mascot-themed-discovery {
  top: -3%;
  right: -5%;
  bottom: auto;
  left: auto;
  width: 34%;
  height: 34%;
  overflow: visible;
}

.mascot-orb,
.mascot-eye-look,
.mascot-eyes,
.mascot-eye,
.mascot-shadow,
.mascot-discovery {
  transform-box: fill-box;
  transform-origin: center;
}

.mascot-orb {
  transform-origin: 50% 92%;
}

.mascot-eye-look {
  transform: translate(var(--mascot-look-x), var(--mascot-look-y));
}

.mascot-discovery {
  opacity: 0;
}

.is-idle .mascot-orb,
.is-waiting .mascot-orb {
  animation: mascot-breathe 4.2s ease-in-out infinite;
}

.is-idle .mascot-eye {
  animation: mascot-blink 5.8s ease-in-out infinite;
}

.is-thinking .mascot-orb {
  animation: mascot-think 2.4s ease-in-out infinite;
}

.is-thinking .mascot-eyes {
  animation: mascot-eyes-think 2.4s ease-in-out infinite;
}

.is-searching .mascot-orb {
  animation: mascot-scan 2s cubic-bezier(0.45, 0, 0.55, 1) infinite;
}

.is-searching .mascot-eyes {
  animation: mascot-eyes-scan 2s cubic-bezier(0.45, 0, 0.55, 1) infinite;
}

.is-working .mascot-orb {
  animation: mascot-work 1.45s ease-in-out infinite;
}

.is-working .mascot-shadow {
  animation: mascot-shadow-work 1.45s ease-in-out infinite;
}

.is-waiting .mascot-eyes {
  transform: translateY(-0.35px);
}

.is-discover .mascot-orb {
  animation: mascot-discover 0.82s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.is-discover .mascot-eyes {
  animation: mascot-eyes-discover 0.82s ease-out both;
}

.is-discover .mascot-discovery {
  animation: mascot-spark 0.82s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.is-success .mascot-orb {
  animation: mascot-success 0.78s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.is-success .mascot-shadow {
  animation: mascot-shadow-success 0.78s ease-out both;
}

.is-error .mascot-orb {
  animation: mascot-error 0.46s ease-out both;
}

.is-error .mascot-eyes {
  transform: translateY(0.45px);
}

.is-poked.variant-main .mascot-main-art {
  animation: mascot-greet-main 0.72s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.is-poked.variant-main .mascot-orb {
  animation: mascot-greet-main-squash 0.72s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.is-poked.variant-main .mascot-eyes {
  animation: mascot-eyes-discover 0.72s ease-out both;
}

.is-poked.variant-main .mascot-discovery {
  animation: mascot-spark 0.72s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.is-poked.variant-campus .mascot-themed-art {
  animation: mascot-greet-campus 0.82s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.is-poked.variant-campus .mascot-orb {
  animation: none;
}

.is-poked.variant-campus .mascot-eyes {
  animation: mascot-eyes-campus 0.82s ease-in-out both;
}

.is-poked.variant-campus .mascot-discovery {
  animation: mascot-spark-campus 0.82s ease-out both;
}

.is-poked.variant-presentation .mascot-themed-art {
  animation: mascot-greet-presentation 0.9s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.is-poked.variant-presentation .mascot-orb {
  animation: none;
}

.is-poked.variant-presentation .mascot-eyes {
  animation: mascot-eyes-presentation 0.9s ease-in-out both;
}

.is-poked.variant-presentation .mascot-discovery {
  animation: mascot-spark-presentation 0.9s ease-out both;
}

.is-poked.variant-interview .mascot-themed-art {
  animation: mascot-greet-interview 0.72s ease-in-out both;
}

.is-poked.variant-interview .mascot-orb {
  animation: none;
}

.is-poked.variant-interview .mascot-eyes {
  animation: mascot-eyes-discover 0.72s ease-out both;
}

@keyframes mascot-greet-interview {
  0%, 100% { transform: translateY(0) rotate(0); }
  35% { transform: translateY(2px) rotate(-3deg); }
  68% { transform: translateY(-1px) rotate(1deg); }
}

@keyframes mascot-breathe {
  0%, 100% { transform: translateY(0) scale(1); }
  50% { transform: translateY(-0.55px) scale(1.006, 0.994); }
}

@keyframes mascot-blink {
  0%, 44%, 48%, 100% { transform: scaleY(1); }
  46% { transform: scaleY(0.16); }
}

@keyframes mascot-think {
  0%, 100% { transform: translateY(0) rotate(-0.8deg); }
  50% { transform: translateY(-0.7px) rotate(1.2deg); }
}

@keyframes mascot-eyes-think {
  0%, 100% { transform: translate(-0.45px, -0.15px); }
  50% { transform: translate(0.55px, 0.1px); }
}

@keyframes mascot-scan {
  0%, 100% { transform: rotate(-1.4deg); }
  50% { transform: rotate(1.4deg); }
}

@keyframes mascot-eyes-scan {
  0%, 100% { transform: translateX(-1.05px); }
  50% { transform: translateX(1.05px); }
}

@keyframes mascot-work {
  0%, 100% { transform: translateY(0) scale(1); }
  50% { transform: translateY(-0.9px) scale(1.01, 0.99); }
}

@keyframes mascot-shadow-work {
  0%, 100% { transform: scaleX(1); opacity: 0.24; }
  50% { transform: scaleX(0.88); opacity: 0.16; }
}

@keyframes mascot-discover {
  0% { transform: translateY(0) scale(1); }
  32% { transform: translateY(-2.4px) scale(1.025, 0.975); }
  58% { transform: translateY(0.35px) scale(0.992, 1.008); }
  100% { transform: translateY(0) scale(1); }
}

@keyframes mascot-eyes-discover {
  0%, 100% { transform: scale(1); }
  30%, 58% { transform: scale(1.08); }
}

@keyframes mascot-spark {
  0% { opacity: 0; transform: scale(0.3) rotate(-18deg); }
  28% { opacity: 1; transform: scale(1) rotate(0deg); }
  70% { opacity: 0.85; transform: scale(0.9) rotate(8deg); }
  100% { opacity: 0; transform: scale(0.55) rotate(12deg); }
}

@keyframes mascot-success {
  0% { transform: translateY(0) scale(1); }
  34% { transform: translateY(-1.9px) scale(1.018, 0.982); }
  62% { transform: translateY(0.3px) scale(0.994, 1.006); }
  100% { transform: translateY(0) scale(1); }
}

@keyframes mascot-shadow-success {
  0%, 100% { transform: scaleX(1); opacity: 0.24; }
  34% { transform: scaleX(0.78); opacity: 0.12; }
}

@keyframes mascot-error {
  0%, 100% { transform: translateX(0); }
  24% { transform: translateX(-0.8px) rotate(-0.8deg); }
  48% { transform: translateX(0.75px) rotate(0.8deg); }
  72% { transform: translateX(-0.35px); }
}

@keyframes mascot-greet-main {
  0% { transform: translateY(0); }
  24% { transform: translateY(0.7px); }
  48% { transform: translateY(-2.6px); }
  72% { transform: translateY(0.3px); }
  100% { transform: translateY(0); }
}

@keyframes mascot-greet-main-squash {
  0% { transform: scale(1); }
  24% { transform: scale(1.025, 0.975); }
  48% { transform: scale(0.985, 1.015); }
  72% { transform: scale(1.006, 0.994); }
  100% { transform: scale(1); }
}

@keyframes mascot-greet-campus {
  0%, 100% { transform: translateY(0) rotate(0deg) scale(1); }
  24% { transform: translateY(1.1px) rotate(-2.8deg) scale(1.012, 0.972); }
  46% { transform: translateY(-0.8px) rotate(2.1deg) scale(0.994, 1.01); }
  68% { transform: translateY(0.25px) rotate(-0.8deg) scale(1.004, 0.996); }
}

@keyframes mascot-eyes-campus {
  0%, 30%, 58%, 100% { transform: scaleY(1); }
  42% { transform: scaleY(0.18); }
}

@keyframes mascot-spark-campus {
  0% { opacity: 0; transform: translate(-2px, 2px) scale(0.35) rotate(-14deg); }
  38% { opacity: 0.95; transform: translate(0, 0) scale(0.88) rotate(4deg); }
  100% { opacity: 0; transform: translate(2px, -2px) scale(0.5) rotate(16deg); }
}

@keyframes mascot-greet-presentation {
  0%, 100% { transform: translateX(0) rotate(0deg) scale(1); }
  20% { transform: translateX(2.4px) rotate(2.8deg) scale(0.975, 1.01); }
  44% { transform: translateX(-1.4px) rotate(-1.8deg) scale(1.012, 0.99); }
  66% { transform: translateX(0.7px) rotate(0.9deg) scale(0.996, 1.004); }
}

@keyframes mascot-eyes-presentation {
  0%, 100% { transform: translateX(0) scale(1); }
  24% { transform: translateX(5px) scale(1.06); }
  50% { transform: translateX(-4px) scale(0.98); }
  72% { transform: translateX(1px) scale(1.02); }
}

@keyframes mascot-spark-presentation {
  0% { opacity: 0; transform: translateX(-3px) scale(0.3) rotate(-20deg); }
  28% { opacity: 1; transform: translateX(1px) scale(1) rotate(0deg); }
  62% { opacity: 0.8; transform: translateX(4px) scale(0.78) rotate(12deg); }
  100% { opacity: 0; transform: translateX(7px) scale(0.42) rotate(20deg); }
}

@media (prefers-reduced-motion: reduce) {
  .work-agent-mascot *,
  .work-agent-mascot *::before,
  .work-agent-mascot *::after {
    animation: none !important;
  }

  .mascot-main-art,
  .mascot-themed-art,
  .mascot-orb,
  .mascot-themed-eye-layer,
  .mascot-eye-look,
  .mascot-eyes,
  .mascot-eye,
  .mascot-shadow {
    transform: none !important;
  }

  .is-discover .mascot-discovery,
  .is-poked .mascot-discovery {
    opacity: 0.9;
    transform: none;
  }
}
</style>
