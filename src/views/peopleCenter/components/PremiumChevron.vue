<template>
  <span
    class="premium-chevron"
    :class="[`premium-chevron--${direction}`, { 'is-interactive': interactive }]"
    :style="{ '--chevron-size': `${size}px` }"
    aria-hidden="true"
  >
    <svg viewBox="0 0 20 20" focusable="false">
      <path d="M5.75 7.5 10 11.75l4.25-4.25" />
    </svg>
  </span>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    direction?: 'right' | 'down' | 'left' | 'up';
    size?: number;
    interactive?: boolean;
  }>(),
  {
    direction: 'right',
    size: 14,
    interactive: false,
  },
);
</script>

<style scoped>
.premium-chevron {
  --chevron-size: 14px;
  display: inline-flex;
  flex: none;
  width: var(--chevron-size);
  height: var(--chevron-size);
  align-items: center;
  justify-content: center;
  color: currentColor;
  vertical-align: middle;
  transform-origin: center;
  will-change: transform;
  transition:
    transform 260ms cubic-bezier(0.22, 1, 0.36, 1),
    color 150ms ease,
    filter 150ms ease;
}

.premium-chevron svg {
  display: block;
  width: 100%;
  height: 100%;
  overflow: visible;
}

.premium-chevron path {
  fill: none;
  stroke: currentColor;
  stroke-width: 1.85;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.premium-chevron--right { transform: rotate(-90deg); }
.premium-chevron--left { transform: rotate(90deg); }
.premium-chevron--up { transform: rotate(180deg); }

.is-interactive:hover {
  color: #30343b;
  filter: drop-shadow(0 1px 1px rgba(18, 24, 33, 0.12));
}

@media (prefers-reduced-motion: reduce) {
  .premium-chevron {
    transition: none;
  }
}
</style>
