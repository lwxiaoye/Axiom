<template>
  <span v-if="backgroundUrl" :class="['main-chat-skin-backdrop', { 'is-conversation': !emptyState }]" aria-hidden="true">
    <img :src="backgroundUrl" alt="" :style="imageStyle" />
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { HydratedMainChatSkin, MainChatSkinLayout } from './types';
import { mainChatSkinAsset } from './runtime';

const props = defineProps<{
  skin: HydratedMainChatSkin;
  layout: MainChatSkinLayout;
  emptyState: boolean;
}>();

const backgroundUrl = computed(() => mainChatSkinAsset(props.skin, props.layout.background.asset));
const POSITION = {
  center: 'center center',
  'center-top': 'center top',
  'center-bottom': 'center bottom',
  'left-bottom': 'left bottom',
  'right-bottom': 'right bottom',
} as const;
const imageStyle = computed(() => ({
  objectFit: props.layout.background.fit,
  objectPosition: POSITION[props.layout.background.position],
  opacity: String(props.layout.background.opacity),
}));
</script>

<style scoped lang="less">
.main-chat-skin-backdrop {
  position: absolute;
  z-index: 0;
  inset: 0;
  display: block;
  overflow: hidden;
  background: var(--main-chat-skin-page, #fff);
  pointer-events: none;
}

.main-chat-skin-backdrop img {
  display: block;
  width: 100%;
  height: 100%;
  transition: opacity 0.2s ease;
}

.main-chat-skin-backdrop::after {
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.02), rgba(255, 255, 255, 0.09));
  content: '';
}

.main-chat-skin-backdrop.is-conversation::after {
  /* 欢迎态和对话态使用同一张已发布背景。输出后只保留极轻的阅读渐变，
     不再修改 manifest 声明的背景透明度，避免白蒙层把皮肤洗掉。 */
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.025), rgba(255, 255, 255, 0.11));
}

@media (prefers-reduced-motion: reduce) {
  .main-chat-skin-backdrop img { transition: none; }
}
</style>
