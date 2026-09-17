<template>
  <span class="portable-composer-decorations" aria-hidden="true">
    <img
      v-for="(item, index) in decorations"
      :key="`${item.asset}-${item.anchor}-${index}`"
      :src="assetUrl(item.asset)"
      alt=""
      :style="decorationStyle(item)"
    />
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { PortableRunSkinDecoration, PortableRunSkinLayout } from '../../../../workflow/core/type';
import type { HydratedPortableRunSkin } from '../portable';
import { portableRunSkinAsset } from '../portable';

const props = defineProps<{
  skin: HydratedPortableRunSkin;
  layout: PortableRunSkinLayout;
}>();

const decorations = computed(() => (props.layout.decorations || []).filter(
  (item) => item.visible && item.anchor.startsWith('composer-') && portableRunSkinAsset(props.skin, item.asset),
));
function assetUrl(key: string) { return portableRunSkinAsset(props.skin, key); }
function decorationStyle(item: PortableRunSkinDecoration): Record<string, string> {
  const result: Record<string, string> = {
    width: `${item.width}px`,
    bottom: `calc(100% + ${item.y}px)`,
    opacity: String(item.opacity),
  };
  if (item.anchor.endsWith('-left')) result.left = `${item.x}px`;
  if (item.anchor.endsWith('-right')) result.right = `${item.x}px`;
  return result;
}
</script>

<style scoped lang="less">
.portable-composer-decorations { position: absolute; z-index: 3; inset: 0; display: block; overflow: visible; pointer-events: none; }
.portable-composer-decorations img { position: absolute; display: block; max-width: min(48vw, 480px); height: auto; filter: drop-shadow(0 7px 8px rgba(44, 89, 128, 0.08)); user-select: none; }
@media (max-width: 1024px) { .portable-composer-decorations img { max-width: 40vw; } }
</style>
