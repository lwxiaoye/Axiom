<template>
  <span v-if="backgroundUrl" :class="['portable-run-backdrop', { 'is-conversation': !emptyState }]" aria-hidden="true">
    <img :src="backgroundUrl" alt="" :style="imageStyle" />
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { PortableRunSkinLayout } from '../../../../workflow/core/type';
import type { HydratedPortableRunSkin } from '../portable';
import { portableRunSkinAsset } from '../portable';

const props = defineProps<{
  skin: HydratedPortableRunSkin;
  layout: PortableRunSkinLayout;
  emptyState?: boolean;
}>();

const POSITION = {
  center: 'center center',
  'center-top': 'center top',
  'center-bottom': 'center bottom',
  'left-bottom': 'left bottom',
  'right-bottom': 'right bottom',
} as const;
const backgroundUrl = computed(() => portableRunSkinAsset(props.skin, props.layout.background.asset));
const imageStyle = computed(() => ({
  objectFit: props.layout.background.fit,
  objectPosition: POSITION[props.layout.background.position],
  opacity: String(props.layout.background.opacity),
}));
</script>

<style scoped lang="less">
.portable-run-backdrop {
  position: absolute;
  z-index: 0;
  inset: 0;
  display: block;
  overflow: hidden;
  background: var(--run-page-bg, #fff);
  pointer-events: none;
}
.portable-run-backdrop img {
  display: block;
  width: 100%;
  height: 100%;
  transition: opacity 0.2s ease, filter 0.2s ease;
}
.portable-run-backdrop::after { position: absolute; inset: 0; background: rgba(255, 255, 255, 0.04); content: ''; }
.portable-run-backdrop.is-conversation img { filter: saturate(0.94); }
.portable-run-backdrop.is-conversation::after {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.08), rgba(255, 255, 255, 0.14));
}
@media (prefers-reduced-motion: reduce) { .portable-run-backdrop img { transition: none; } }
</style>
