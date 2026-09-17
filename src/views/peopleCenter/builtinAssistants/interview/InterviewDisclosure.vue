<template>
  <div class="interview-disclosure" :class="{ open }">
    <button :id="`${id}-trigger`" type="button" :aria-expanded="open" :aria-controls="id" @click="open = !open">
      <slot name="label">{{ label }}</slot><PremiumChevron :direction="open ? 'down' : 'right'" :size="14" interactive />
    </button>
    <div :id="id" class="disclosure-body" :aria-hidden="!open" :inert="!open || undefined" role="region" :aria-labelledby="`${id}-trigger`">
      <div class="disclosure-clip"><div class="disclosure-content"><slot /></div></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, useId } from 'vue';
import PremiumChevron from '../../components/PremiumChevron.vue';

defineProps<{ label: string }>();
const id = `interview-details-${useId()}`;
const open = ref(false);
</script>

<style scoped lang="less">
.interview-disclosure > button {
  display: inline-flex; align-items: center; gap: 8px; min-height: 36px; padding: 4px 0;
  border: 0; background: transparent; color: #52525b; font: inherit; font-size: 16px; cursor: pointer;
  transition: color .18s ease;
}
.interview-disclosure > button:hover { color: var(--interview-ink); }
.interview-disclosure > button:focus-visible { outline: 2px solid var(--interview-accent); outline-offset: 4px; border-radius: 3px; }
.disclosure-body { display: grid; grid-template-rows: 0fr; opacity: 0; visibility: hidden; transition: grid-template-rows .28s cubic-bezier(.2,.7,.2,1), opacity .2s ease, visibility .28s; }
.open > .disclosure-body { grid-template-rows: 1fr; opacity: 1; visibility: visible; }
.disclosure-clip { min-height: 0; overflow: hidden; }
.disclosure-content { padding: 10px 0 4px; }
@media (prefers-reduced-motion: reduce) {
  .interview-disclosure > button, .disclosure-body { transition: none; }
}
</style>
