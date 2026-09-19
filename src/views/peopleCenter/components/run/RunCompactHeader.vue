<template>
  <header class="run-compact-header">
    <button
      v-if="showBack"
      type="button"
      class="run-compact-back"
      aria-label="返回上一页"
      @click="emit('back')"
    >
      <ArrowLeftOutlined />
    </button>

    <RunSidebarToggle
      class="run-compact-history-toggle"
      :collapsed="sidebarCollapsed"
      @toggle="emit('toggle-sidebar')"
    />

    <strong class="run-compact-title" :title="title">{{ title }}</strong>
  </header>
</template>

<script setup lang="ts">
import { ArrowLeftOutlined } from '@ant-design/icons-vue';
import RunSidebarToggle from './RunSidebarToggle.vue';

withDefaults(defineProps<{
  title: string;
  sidebarCollapsed: boolean;
  showBack?: boolean;
}>(), {
  showBack: false,
});

const emit = defineEmits<{
  (event: 'toggle-sidebar'): void;
  (event: 'back'): void;
}>();
</script>

<style scoped lang="less">
.run-compact-header {
  display: none;
}

@media (max-width: 1024px) {
  .run-compact-header {
    position: relative;
    z-index: 12;
    display: flex;
    height: calc(60px + env(safe-area-inset-top));
    flex: 0 0 calc(60px + env(safe-area-inset-top));
    box-sizing: border-box;
    align-items: center;
    padding: calc(8px + env(safe-area-inset-top)) 14px 8px;
    border-bottom: 1px solid var(--run-left-rail-border, #ededef);
    background: rgba(255, 255, 255, 0.96);
    color: var(--run-left-item-title, #2b2f36);
    backdrop-filter: blur(18px);
  }

  .run-compact-title {
    position: absolute;
    top: calc(8px + env(safe-area-inset-top));
    left: 50%;
    display: flex;
    width: max-content;
    max-width: calc(100% - 194px);
    height: 44px;
    align-items: center;
    overflow: hidden;
    transform: translateX(-50%);
    font-size: 14px;
    font-weight: 680;
    line-height: 1.2;
    text-align: center;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  :deep(.run-compact-history-toggle),
  .run-compact-back {
    display: inline-flex;
    height: 44px;
    flex: 0 0 auto;
    align-items: center;
    justify-content: center;
    padding: 0;
    border: 1px solid var(--run-left-control-border, #e5e6e9);
    border-radius: 12px;
    background: var(--run-left-control-bg, #fff);
    color: var(--run-left-item-title, #2b2f36);
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
    cursor: pointer;
  }

  :deep(.run-compact-history-toggle) {
    width: 52px;
  }

  .run-compact-back {
    width: 44px;
    margin-right: 6px;
    font-size: 16px;
  }

  :deep(.run-compact-history-toggle svg) {
    width: 17px;
    height: 17px;
  }

  :deep(.run-compact-history-toggle:hover),
  .run-compact-back:hover {
    border-color: var(--run-left-control-hover-border, #dcdee2);
    background: #f7f7f8;
  }

  :deep(.run-compact-history-toggle:focus-visible),
  .run-compact-back:focus-visible {
    outline: 2px solid var(--run-focus-ring, #4f46e5);
    outline-offset: 2px;
  }
}

@media (max-width: 719px) {
  .run-compact-header {
    height: calc(56px + env(safe-area-inset-top));
    flex-basis: calc(56px + env(safe-area-inset-top));
    padding: calc(6px + env(safe-area-inset-top)) 10px 6px;
  }

  .run-compact-title {
    top: calc(6px + env(safe-area-inset-top));
    max-width: calc(100% - 182px);
    font-size: 13.5px;
  }

  :deep(.run-compact-history-toggle) {
    width: 50px;
  }
}

@media (prefers-reduced-motion: reduce) {
  :deep(.run-compact-history-toggle),
  .run-compact-back {
    transition: none;
  }
}
</style>
