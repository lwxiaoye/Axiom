<template>
  <header class="run-compact-header" :class="{ 'has-close-action': showClose }">
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

    <div class="run-compact-actions">
      <button
        v-if="showInspiration"
        type="button"
        class="run-compact-inspiration-toggle"
        :aria-expanded="!inspirationCollapsed"
        aria-label="打开场景问题推荐"
        @click="emit('toggle-inspiration')"
      >
        <BulbOutlined />
        <span>场景</span>
      </button>
      <button
        v-if="showClose"
        type="button"
        class="run-compact-close"
        aria-label="关闭子智能体对话"
        @click="emit('close')"
      >
        <CloseOutlined />
      </button>
    </div>
  </header>
</template>

<script setup lang="ts">
import { ArrowLeftOutlined, BulbOutlined, CloseOutlined } from '@ant-design/icons-vue';
import RunSidebarToggle from './RunSidebarToggle.vue';

withDefaults(defineProps<{
  title: string;
  sidebarCollapsed: boolean;
  inspirationCollapsed?: boolean;
  showInspiration?: boolean;
  showClose?: boolean;
  showBack?: boolean;
}>(), {
  inspirationCollapsed: true,
  showInspiration: false,
  showClose: false,
  showBack: false,
});

const emit = defineEmits<{
  (event: 'toggle-sidebar'): void;
  (event: 'toggle-inspiration'): void;
  (event: 'close'): void;
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

  .has-close-action .run-compact-title {
    max-width: calc(100% - 260px);
  }

  :deep(.run-compact-history-toggle),
  .run-compact-back,
  .run-compact-inspiration-toggle,
  .run-compact-close {
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

  .run-compact-actions {
    display: flex;
    margin-left: auto;
    align-items: center;
    gap: 6px;
  }

  .run-compact-inspiration-toggle {
    width: 52px;
    flex-direction: column;
    gap: 1px;
  }

  .run-compact-inspiration-toggle :deep(.anticon) {
    font-size: 14px;
  }

  .run-compact-inspiration-toggle span:last-child {
    font-size: 9.5px;
    font-weight: 650;
    line-height: 11px;
  }

  .run-compact-close {
    width: 44px;
    font-size: 16px;
  }

  :deep(.run-compact-history-toggle:hover),
  .run-compact-back:hover,
  .run-compact-inspiration-toggle:hover,
  .run-compact-close:hover {
    border-color: var(--run-left-control-hover-border, #dcdee2);
    background: #f7f7f8;
  }

  :deep(.run-compact-history-toggle:focus-visible),
  .run-compact-back:focus-visible,
  .run-compact-inspiration-toggle:focus-visible,
  .run-compact-close:focus-visible {
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

  .has-close-action .run-compact-title {
    max-width: calc(100% - 246px);
  }

  :deep(.run-compact-history-toggle),
  .run-compact-inspiration-toggle {
    width: 50px;
  }
}

@media (prefers-reduced-motion: reduce) {
  :deep(.run-compact-history-toggle),
  .run-compact-back,
  .run-compact-inspiration-toggle,
  .run-compact-close {
    transition: none;
  }
}
</style>
