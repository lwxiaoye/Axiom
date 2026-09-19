<template>
  <button
    class="run-sidebar-toggle"
    type="button"
    :title="collapsed ? '展开侧边栏' : '收起侧边栏'"
    :aria-label="collapsed ? '展开侧边栏' : '收起侧边栏'"
    :aria-expanded="!collapsed"
    @click="emit('toggle')"
  >
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <rect width="18" height="18" x="3" y="3" rx="2" />
      <path d="M9 3v18" />
    </svg>
  </button>
</template>

<script setup lang="ts">
defineProps<{ collapsed: boolean }>();

const emit = defineEmits<{
  (event: 'toggle'): void;
}>();
</script>
