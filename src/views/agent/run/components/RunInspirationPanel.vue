<template>
  <div :class="['run-inspiration-shell', { 'is-collapsed': collapsed }]">
    <button
      v-if="!collapsed"
      type="button"
      class="run-inspiration-mobile-backdrop"
      aria-label="关闭场景与推荐"
      @click="emit('toggle')"
    ></button>
    <button
      type="button"
      class="run-inspiration-edge-toggle"
      :title="collapsed ? '展开灵感侧栏' : '收起灵感侧栏'"
      :aria-label="collapsed ? '展开灵感侧栏' : '收起灵感侧栏'"
      :aria-expanded="!collapsed"
      @click="emit('toggle')"
    >
      <svg class="run-inspiration-edge-chevron" viewBox="0 0 16 16" aria-hidden="true">
        <path d="M6.2 3.4 11.4 8 6.2 12.6" />
      </svg>
    </button>
    <aside
      class="run-inspiration-panel"
      :style="{
        width: (collapsed ? 0 : width) + 'px',
        flexBasis: (collapsed ? 0 : width) + 'px',
        '--run-inspiration-width': width + 'px',
      }"
      aria-label="场景与推荐"
    >
    <span v-if="decoration" class="run-presentation-rail-decoration" aria-hidden="true">
      <component :is="decoration" v-bind="decorationProps || {}" region="inspiration" />
    </span>
    <div
      v-show="!collapsed"
      class="run-inspiration-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="调整灵感侧栏宽度"
      @mousedown="emit('resizeStart', $event)"
    ></div>

    <div class="run-inspiration-content">
      <header class="run-inspiration-head">
        <span class="run-inspiration-heading">
          <strong>场景与推荐</strong>
          <small>按场景快速找到问题</small>
        </span>
        <button
          type="button"
          class="run-inspiration-mobile-close"
          aria-label="关闭场景与推荐"
          @click="emit('toggle')"
        >
          <CloseOutlined />
        </button>
      </header>

      <template v-if="activeScene">
        <section v-if="scenes.length > 1" class="run-inspiration-section">
          <span class="run-inspiration-label">
            <span>使用场景</span>
          </span>
          <a-select
            v-model:value="activeKey"
            class="run-scene-select"
            popup-class-name="run-scene-select-popup"
            :options="sceneOptions"
            :list-height="352"
            aria-label="选择使用场景"
          />
        </section>

        <section class="run-inspiration-section task-section">
          <span class="run-inspiration-label">
            <span>问题推荐</span>
          </span>
          <div class="run-task-suggestions">
            <button
              v-for="task in activeScene.tasks"
              :key="task"
              type="button"
              class="run-task-suggestion"
              :title="task"
              @click="selectTask(task)"
            >
              <span class="run-task-mark" aria-hidden="true"><BulbOutlined /></span>
              <span>{{ task }}</span>
              <RightOutlined aria-hidden="true" />
            </button>
          </div>
        </section>
      </template>

      <div v-else class="run-inspiration-empty">
        <span aria-hidden="true"><BulbOutlined /></span>
        <strong>暂未配置推荐内容</strong>
        <small>请搭建者在智能体配置中添加</small>
      </div>
    </div>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch, type Component } from 'vue';
import { BulbOutlined, CloseOutlined, RightOutlined } from '@ant-design/icons-vue';
import type { RunInspirationScene } from '../agentRunPresentation';

const props = defineProps<{
  collapsed: boolean;
  width: number;
  scenes: RunInspirationScene[];
  decoration?: Component;
  decorationProps?: Record<string, unknown>;
}>();

const emit = defineEmits<{
  (event: 'toggle'): void;
  (event: 'resizeStart', value: MouseEvent): void;
  (event: 'select', value: string): void;
}>();

const activeKey = ref('');
const activeScene = computed(() => props.scenes.find((scene) => scene.key === activeKey.value) || props.scenes[0]);
const sceneOptions = computed(() => props.scenes.map((scene) => ({ label: scene.label, value: scene.key })));

function selectTask(task: string) {
  emit('select', task);
  if (typeof window !== 'undefined' && window.matchMedia('(max-width: 1024px)').matches) {
    emit('toggle');
  }
}

watch(
  () => props.scenes.map((scene) => scene.key).join(','),
  () => {
    if (!props.scenes.some((scene) => scene.key === activeKey.value)) {
      activeKey.value = props.scenes[0]?.key || '';
    }
  },
  { immediate: true },
);
</script>

<style scoped lang="less">
.run-inspiration-shell {
  position: relative;
  display: flex;
  min-width: 0;
  flex: none;
  overflow: visible;
}
.run-inspiration-mobile-backdrop,
.run-inspiration-mobile-close { display: none; }
.run-inspiration-shell.is-collapsed::before {
  position: absolute;
  top: 0;
  bottom: 0;
  left: -44px;
  width: 44px;
  content: '';
}
.run-inspiration-panel {
  position: relative;
  width: var(--run-inspiration-width, 272px);
  min-width: 0;
  flex: 0 0 auto;
  container-type: inline-size;
  overflow: hidden;
  padding: 0;
  border-left: 1px solid var(--run-right-rail-border, #ededef);
  background: var(--run-right-rail-bg, #f8f8f9);
  color: var(--run-right-heading, #191a1e);
  transition: width 0.28s cubic-bezier(0.22, 1, 0.36, 1), flex-basis 0.28s cubic-bezier(0.22, 1, 0.36, 1), border-color 0.28s ease;
}
.run-presentation-rail-decoration {
  position: absolute;
  z-index: 0;
  inset: 0;
  pointer-events: none;
}
.run-inspiration-shell.is-collapsed .run-inspiration-panel {
  border-left-color: transparent;
}
.run-inspiration-content {
  position: relative;
  z-index: 1;
  width: var(--run-inspiration-width, 272px);
  height: 100%;
  box-sizing: border-box;
  overflow-x: hidden;
  overflow-y: auto;
  padding: 26px 18px 30px;
  scrollbar-color: #d6d8dd transparent;
  scrollbar-width: thin;
}
.run-inspiration-edge-toggle {
  position: absolute;
  top: 50%;
  left: 0;
  z-index: 6;
  display: inline-flex;
  width: 24px;
  height: 24px;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid var(--run-right-control-border, #e1e2e5);
  border-radius: 7px;
  background: var(--run-right-control-bg, #fff);
  color: var(--run-right-card-text, #4d5057);
  cursor: pointer;
  box-shadow: none;
  opacity: 0;
  transform: translate(calc(-100% - 12px), -50%);
  transition:
    opacity 0.16s ease,
    color 0.16s ease,
    border-color 0.16s ease,
    background 0.16s ease,
    transform 0.28s cubic-bezier(0.22, 1, 0.36, 1);
}
.run-inspiration-shell:hover .run-inspiration-edge-toggle,
.run-inspiration-edge-toggle:focus-visible {
  opacity: 1;
}
.run-inspiration-edge-chevron {
  width: 11px;
  height: 11px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.7;
  stroke-linecap: round;
  stroke-linejoin: round;
  transition: transform 0.28s cubic-bezier(0.22, 1, 0.36, 1);
}
.run-inspiration-shell.is-collapsed .run-inspiration-edge-chevron {
  transform: rotate(180deg);
}
.run-inspiration-edge-toggle:hover {
  color: var(--run-right-heading, #1d1d20);
  border-color: var(--run-right-card-hover-border, #c8cbd0);
  background: var(--run-right-card-hover-bg, #f7f7f8);
}
.run-inspiration-edge-toggle:focus-visible { outline: 2px solid #111827; outline-offset: 2px; }
.run-inspiration-resizer {
  position: absolute;
  top: 0;
  bottom: 0;
  left: -4px;
  z-index: 4;
  width: 8px;
  cursor: col-resize;
  touch-action: none;
}
.run-inspiration-resizer::after {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 3px;
  width: 2px;
  border-radius: 1px;
  background: transparent;
  content: '';
  transition: background 0.15s ease;
}
.run-inspiration-resizer:hover::after { background: #c8cbd1; }
.run-inspiration-head {
  display: flex;
  min-height: 46px;
  align-items: flex-start;
  padding: 0 2px 18px;
  border-bottom: 1px solid var(--run-right-divider, #e7e8eb);
}
.run-inspiration-heading { display: flex; min-width: 0; flex-direction: column; gap: 4px; }
.run-inspiration-heading strong { color: var(--run-right-heading, #191a1e); font-size: 18px; font-weight: 720; letter-spacing: -0.025em; }
.run-inspiration-heading small { color: var(--run-right-muted, #858992); font-size: 12.5px; line-height: 1.45; }
.run-inspiration-section { margin-top: 23px; }
.run-inspiration-label {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  color: var(--run-right-muted, #5f636c);
  font-size: 12.5px;
  font-weight: 680;
  line-height: 1.4;
}
.run-inspiration-label small {
  flex: none;
  color: #a0a3aa;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.01em;
}
.run-scene-select { width: 100%; }
.run-scene-select :deep(.ant-select-selector) {
  height: 42px !important;
  padding: 0 13px !important;
  border-color: var(--run-right-control-border, #dfe1e5) !important;
  border-radius: 10px !important;
  background: var(--run-right-control-bg, #fff) !important;
  box-shadow: 0 1px 2px rgba(20, 22, 27, 0.025) !important;
}
.run-scene-select :deep(.ant-select-selection-item) {
  display: flex;
  align-items: center;
  color: var(--run-right-heading, #292b31);
  font-size: 13.5px;
  font-weight: 650;
  line-height: 40px !important;
}
.run-scene-select :deep(.ant-select-arrow) { color: #888c94; }
.run-scene-select:hover :deep(.ant-select-selector),
.run-scene-select.ant-select-focused :deep(.ant-select-selector) {
  border-color: var(--run-right-card-hover-border, #babdc4) !important;
  box-shadow: var(--run-right-control-focus-shadow, 0 0 0 3px rgba(35, 37, 43, 0.06)) !important;
}
.task-section { min-height: 0; }
.run-task-suggestions { display: grid; gap: 8px; }
.run-task-suggestion {
  display: grid;
  grid-template-columns: 27px minmax(0, 1fr) 14px;
  min-height: 52px;
  align-items: center;
  gap: 9px;
  padding: 9px 11px;
  border: 1px solid var(--run-right-card-border, #e3e5e8);
  border-radius: 11px;
  background: var(--run-right-card-bg, rgba(255, 255, 255, 0.9));
  color: var(--run-right-card-text, #373a41);
  font-size: 13.5px;
  line-height: 1.45;
  text-align: left;
  cursor: pointer;
  box-shadow: 0 1px 0 rgba(20, 22, 27, 0.025);
  transition:
    color 0.16s ease,
    border-color 0.16s ease,
    background 0.16s ease,
    box-shadow 0.16s ease,
    transform 0.16s ease;
}
.run-task-suggestion:hover {
  border-color: var(--run-right-card-hover-border, #cbd0d7);
  background: var(--run-right-card-hover-bg, #fff);
  color: var(--run-right-heading, #202227);
  box-shadow: 0 7px 18px rgba(22, 23, 26, 0.055);
  transform: translateY(-1px);
}
.run-task-suggestion:focus-visible,
.run-scene-select:focus-within { outline: 2px solid #111827; outline-offset: 2px; }
.run-task-mark {
  display: inline-flex;
  width: 27px;
  height: 27px;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--run-right-mark-border, #e8e9ec);
  border-radius: 8px;
  background: var(--run-right-mark-bg, #f4f4f6);
  color: var(--run-right-mark-color, #686c75);
  font-size: 12px;
}
.run-task-suggestion > :deep(.anticon-right) { color: var(--run-right-chevron, #a2a5ac); font-size: 10px; }
.run-inspiration-empty {
  display: flex;
  min-height: 180px;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 7px;
  color: var(--run-right-muted, #a0a3aa);
  text-align: center;
}
.run-inspiration-empty > span {
  display: inline-flex;
  width: 32px;
  height: 32px;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  background: var(--run-right-mark-bg, #f0f0f2);
  color: var(--run-right-mark-color, #7c8088);
}
.run-inspiration-empty strong { color: var(--run-right-card-text, #6f737b); font-size: 14px; font-weight: 650; }
.run-inspiration-empty small { color: var(--run-right-muted, #a0a3aa); font-size: 13px; }

@media (max-width: 900px) {
  .run-inspiration-shell {
    position: absolute;
    top: 0;
    right: 0;
    bottom: 0;
    z-index: 7;
  }
  .run-inspiration-panel {
    height: 100%;
    box-shadow: -10px 0 30px rgba(22, 23, 26, 0.08);
  }
  .run-inspiration-shell.is-collapsed .run-inspiration-panel { box-shadow: none; }
}

@media (max-width: 1024px) {
  .run-inspiration-shell,
  .run-inspiration-shell.is-collapsed {
    position: fixed;
    z-index: 40;
    inset: 0;
    display: block;
    pointer-events: none;
  }
  .run-inspiration-mobile-backdrop {
    position: fixed;
    z-index: 1;
    inset: 0;
    display: block;
    padding: 0;
    border: 0;
    background: rgba(15, 23, 42, 0.28);
    pointer-events: auto;
    backdrop-filter: blur(2px);
  }
  .run-inspiration-panel,
  .run-inspiration-shell.is-collapsed .run-inspiration-panel {
    position: fixed;
    z-index: 2;
    right: 0;
    bottom: 0;
    left: 0;
    width: 100% !important;
    height: min(72dvh, 620px);
    flex-basis: auto !important;
    box-sizing: border-box;
    overflow: hidden;
    padding-bottom: env(safe-area-inset-bottom);
    border: 0;
    border-top: 1px solid var(--run-right-rail-border, #ededef);
    border-radius: 22px 22px 0 0;
    box-shadow: 0 -18px 54px rgba(15, 23, 42, 0.18);
    pointer-events: auto;
    transform: translateY(0);
    transition: transform 0.26s cubic-bezier(0.22, 1, 0.36, 1);
  }
  .run-inspiration-panel::before {
    position: absolute;
    z-index: 3;
    top: 8px;
    left: 50%;
    width: 42px;
    height: 4px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--run-right-muted, #858992) 34%, transparent);
    content: '';
    transform: translateX(-50%);
  }
  .run-inspiration-shell.is-collapsed .run-inspiration-panel {
    border-top-color: transparent;
    box-shadow: none;
    pointer-events: none;
    transform: translateY(102%);
  }
  .run-inspiration-content {
    width: 100%;
    height: 100%;
    padding: 24px 16px calc(22px + env(safe-area-inset-bottom));
  }
  .run-inspiration-head {
    min-height: 50px;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
  }
  .run-inspiration-mobile-close {
    display: grid;
    width: 44px;
    height: 44px;
    flex: none;
    padding: 0;
    place-items: center;
    border: 1px solid var(--run-right-control-border, #e1e2e5);
    border-radius: 12px;
    background: var(--run-right-control-bg, #fff);
    color: var(--run-right-card-text, #4d5057);
    cursor: pointer;
  }
  .run-inspiration-resizer { display: none; }
  .run-inspiration-edge-toggle,
  .run-inspiration-shell.is-collapsed .run-inspiration-edge-toggle {
    display: none;
  }
  .run-inspiration-shell.is-collapsed .run-inspiration-edge-chevron { transform: rotate(180deg); }
  .run-task-suggestion { min-height: 54px; }
}

@media (prefers-reduced-motion: reduce) {
  .run-inspiration-panel,
  .run-inspiration-edge-toggle,
  .run-inspiration-edge-chevron,
  .run-task-suggestion { transition: none; }
}
</style>

<!-- 下拉挂到 body，必须走非 scoped，否则选中态会被主题深底盖住、字也跟着变深。 -->
<style lang="less">
.run-scene-select-popup {
  padding: 7px;
  overflow: hidden;
  border: 1px solid #e2e4e8;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 16px 40px rgba(20, 22, 27, 0.13);
}
.run-scene-select-popup .ant-select-item {
  min-height: 36px;
  margin: 1px 0;
  padding: 8px 10px;
  border-radius: 8px;
  color: #3a3d44;
  font-size: 13px;
  line-height: 20px;
}
.run-scene-select-popup .ant-select-item-option-active:not(.ant-select-item-option-selected):not(.ant-select-item-option-disabled) {
  background: #f5f5f6 !important;
  color: #22242a !important;
}
.run-scene-select-popup .ant-select-item-option-selected:not(.ant-select-item-option-disabled),
.run-scene-select-popup .ant-select-item-option-selected.ant-select-item-option-active:not(.ant-select-item-option-disabled) {
  background: #111827 !important;
  color: #fff !important;
  font-weight: 650 !important;
  box-shadow: none;
}
.run-scene-select-popup .ant-select-item-option-selected .ant-select-item-option-state,
.run-scene-select-popup .ant-select-item-option-selected .anticon {
  color: #fff !important;
}
.run-scene-select-popup .rc-virtual-list-holder {
  scrollbar-color: #c7c9ce transparent;
  scrollbar-width: thin;
}
.run-scene-select-popup .rc-virtual-list-holder::-webkit-scrollbar { width: 5px; }
.run-scene-select-popup .rc-virtual-list-holder::-webkit-scrollbar-track { background: transparent; }
.run-scene-select-popup .rc-virtual-list-holder::-webkit-scrollbar-thumb {
  border-radius: 999px;
  background: #c7c9ce;
}
</style>
