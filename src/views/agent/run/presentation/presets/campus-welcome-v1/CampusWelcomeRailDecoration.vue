<template>
  <span class="campus-rail-decoration" :data-region="region" :data-device="device" aria-hidden="true">
    <img :src="campusBackground" alt="" />
  </span>
</template>

<script setup lang="ts">
import campusBackground from './assets/campus-background.jpg';

withDefaults(
  defineProps<{
    region?: 'sidebar' | 'inspiration';
    device?: 'desktop' | 'tablet' | 'mobile';
  }>(),
  {
    region: 'sidebar',
    device: 'desktop',
  },
);
</script>

<style scoped lang="less">
.campus-rail-decoration {
  position: absolute;
  inset: 0;
  display: block;
  overflow: hidden;
  pointer-events: none;
}

.campus-rail-decoration::before {
  position: absolute;
  z-index: 1;
  inset: 0;
  background: linear-gradient(180deg, rgba(249, 252, 255, 0.98) 0%, rgba(249, 252, 255, 0.82) 58%, rgba(238, 247, 255, 0.36) 100%);
  content: '';
}

.campus-rail-decoration img {
  position: absolute;
  right: -250px;
  bottom: -1px;
  width: 760px;
  max-width: none;
  opacity: 0.58;
  user-select: none;
}

.campus-rail-decoration[data-region='inspiration'] img {
  right: auto;
  left: -310px;
}
</style>
