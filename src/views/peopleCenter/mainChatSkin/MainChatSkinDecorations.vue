<template>
  <span :class="['main-chat-skin-decorations', `is-${region}`]" aria-hidden="true">
    <img
      v-for="(decoration, index) in visibleDecorations"
      :key="`${decoration.asset}-${decoration.anchor}-${index}`"
      :src="assetUrl(decoration.asset)"
      alt=""
      :data-anchor="decoration.anchor"
      :style="decorationStyle(decoration)"
    />
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { mainChatSkinAsset } from './runtime';
import type { HydratedMainChatSkin, MainChatSkinDecoration, MainChatSkinLayout } from './types';

const props = defineProps<{
  skin: HydratedMainChatSkin;
  layout: MainChatSkinLayout;
  region: 'page' | 'intro' | 'composer';
}>();

const visibleDecorations = computed(() => (props.layout.decorations || []).filter((item) => {
  if (!item.visible || !mainChatSkinAsset(props.skin, item.asset)) return false;
  if (props.region === 'page') return item.anchor.startsWith('page-');
  if (props.region === 'intro') return item.anchor === 'intro-top';
  return item.anchor.startsWith('composer-');
}));

function assetUrl(key: string) {
  return mainChatSkinAsset(props.skin, key);
}

function decorationStyle(item: MainChatSkinDecoration): Record<string, string> {
  const style: Record<string, string> = {
    width: `${item.width}px`,
    opacity: String(item.opacity),
  };
  if (item.anchor.endsWith('-left')) style.left = `${item.x}px`;
  if (item.anchor.endsWith('-right')) style.right = `${item.x}px`;
  if (item.anchor.startsWith('page-top')) style.top = `${item.y}px`;
  if (item.anchor.startsWith('page-bottom')) style.bottom = `${item.y}px`;
  if (item.anchor === 'intro-top') {
    style.left = '50%';
    style.bottom = `calc(100% + ${item.y}px)`;
    style.transform = `translateX(calc(-50% + ${item.x}px))`;
  }
  if (item.anchor.startsWith('composer-top')) style.bottom = `calc(100% + ${item.y}px)`;
  return style;
}
</script>

<style scoped lang="less">
.main-chat-skin-decorations {
  position: absolute;
  z-index: 1;
  inset: 0;
  display: block;
  overflow: visible;
  pointer-events: none;
}

.main-chat-skin-decorations img {
  position: absolute;
  display: block;
  max-width: min(52vw, 480px);
  height: auto;
  filter: drop-shadow(0 7px 8px rgba(44, 89, 128, 0.08));
  user-select: none;
}

.main-chat-skin-decorations.is-page {
  z-index: 1;
  overflow: hidden;
}

.main-chat-skin-decorations.is-intro,
.main-chat-skin-decorations.is-composer {
  z-index: 2;
}
</style>
