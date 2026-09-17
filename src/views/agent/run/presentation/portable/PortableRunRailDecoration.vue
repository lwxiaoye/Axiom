<template>
  <span v-if="backgroundUrl" :class="['portable-rail-decoration', `is-${region}`]" aria-hidden="true">
    <img :src="backgroundUrl" alt="" />
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
  region?: 'sidebar' | 'inspiration';
}>();
const backgroundUrl = computed(() => portableRunSkinAsset(props.skin, props.layout.background.asset));
</script>

<style scoped lang="less">
.portable-rail-decoration { position: absolute; inset: 0; display: block; overflow: hidden; pointer-events: none; }
.portable-rail-decoration img { position: absolute; bottom: 0; width: auto; min-width: 720px; max-width: none; height: 100%; object-fit: cover; opacity: 0.16; filter: saturate(0.72); }
.portable-rail-decoration.is-sidebar img { left: 0; object-position: left bottom; }
.portable-rail-decoration.is-inspiration img { right: 0; object-position: right bottom; }
@media (max-width: 1024px) { .portable-rail-decoration img { min-width: 100vw; opacity: 0.12; } }
</style>
