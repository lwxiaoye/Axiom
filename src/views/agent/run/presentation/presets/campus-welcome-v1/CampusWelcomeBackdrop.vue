<template>
  <div :class="['campus-welcome-backdrop', { 'is-conversation': !emptyState }]" :data-device="device" aria-hidden="true">
    <img :src="campusBackground" alt="" />
  </div>
</template>

<script setup lang="ts">
import campusBackground from './assets/campus-background.jpg';

defineProps<{
  emptyState: boolean;
  device?: 'desktop' | 'tablet' | 'mobile';
}>();
</script>

<style scoped lang="less">
.campus-welcome-backdrop {
  position: absolute;
  z-index: 0;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
  background: #eef6ff;
}

.campus-welcome-backdrop img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center bottom;
  opacity: 0.98;
  transition: opacity 0.24s ease, filter 0.24s ease;
}

.campus-welcome-backdrop::after {
  position: absolute;
  content: '';
  inset: 0;
  background: linear-gradient(180deg, rgba(239, 247, 255, 0.08) 0%, rgba(239, 247, 255, 0) 60%, rgba(239, 247, 255, 0.08) 100%);
}

.campus-welcome-backdrop.is-conversation img {
  opacity: 0.9;
  filter: saturate(0.94);
}

.campus-welcome-backdrop.is-conversation::after {
  background: linear-gradient(180deg, rgba(239, 247, 255, 0.1) 0%, rgba(255, 255, 255, 0.08) 58%, rgba(239, 247, 255, 0.16) 100%);
}

@media (prefers-reduced-motion: reduce) {
  .campus-welcome-backdrop img {
    transition: none;
  }
}
</style>
