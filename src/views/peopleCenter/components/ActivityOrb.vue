<template>
  <span
    class="activity-orb"
    :class="{ frozen }"
    :style="rootStyle"
    aria-hidden="true"
  >
    <span class="lattice">
      <span
        v-for="cell in cells"
        :key="cell.key"
        class="cell"
        :data-mid="cell.mid ? '' : undefined"
        :style="cell.style"
      />
    </span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue';

/** AICSS Orb S2：3×3 点阵沿对角线扫过。几何按 28px 舞台编写，用 --orb-k 缩放到 size。 */
const STAGE = 28;
const SIZE = 20;
const N = 3;
const PITCH = 6;

const props = withDefaults(
  defineProps<{
    size?: number;
    frozen?: boolean;
  }>(),
  {
    size: SIZE,
    frozen: false,
  },
);

const rootStyle = computed(() => ({
  width: `${props.size}px`,
  height: `${props.size}px`,
  '--orb-k': String(props.size / STAGE),
}));

const cells = (() => {
  const list: Array<{
    key: string;
    mid: boolean;
    style: Record<string, string>;
  }> = [];
  const mid = (N - 1) / 2;
  for (let y = 0; y < N; y += 1) {
    for (let x = 0; x < N; x += 1) {
      list.push({
        key: `${x},${y}`,
        mid: x === mid && y === mid,
        style: {
          left: `${x * PITCH}px`,
          top: `${y * PITCH}px`,
          animationDelay: `${((x + y) / (2 * (N - 1))) * 1500}ms`,
        },
      });
    }
  }
  return list;
})();
</script>

<style scoped>
.activity-orb {
  --orb-ease-in-out: cubic-bezier(0.66, 0, 0.34, 1);
  --orb-rest: 0.14;
  position: relative;
  display: inline-block;
  flex: none;
  overflow: hidden;
  contain: strict;
  color: #1a1a1a;
  vertical-align: middle;
}

.lattice {
  position: absolute;
  left: 0;
  top: 0;
  width: 28px;
  height: 28px;
  transform-origin: 0 0;
  transform: scale(var(--orb-k, 1)) translate(6.5px, 6.5px);
}

.cell {
  position: absolute;
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: currentColor;
  opacity: var(--orb-rest);
  animation: orb-wave 1.7s var(--orb-ease-in-out) infinite both;
}

.frozen .cell {
  animation: none;
}

@keyframes orb-wave {
  0% {
    opacity: var(--orb-rest);
    transform: scale(1);
    animation-timing-function: cubic-bezier(0.66, 0, 0.34, 1);
  }
  28% {
    opacity: 1;
    transform: scale(1.18);
    animation-timing-function: cubic-bezier(0.66, 0, 0.34, 1);
  }
  56% {
    opacity: var(--orb-rest);
    transform: scale(1);
  }
  100% {
    opacity: var(--orb-rest);
    transform: scale(1);
  }
}

@media (prefers-reduced-motion: reduce) {
  .cell {
    animation: none;
  }
  .cell[data-mid] {
    opacity: 1;
  }
}
</style>
