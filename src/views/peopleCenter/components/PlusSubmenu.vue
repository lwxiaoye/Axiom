<template>
  <!-- + 菜单里的二级条目（2026-07-28，对标 Manus 的「最近的文件」）：一行条目 + 向右飞出的
       面板。「我的文件」「知识库」都用它，行为与外观由这里统一给定，面板内容各自 slot。
       条目行样式与 ChatTab 的 .plus-item 保持一致——scoped 不穿透，只能在这里复刻一份。 -->
  <div
    ref="wrapRef"
    class="ps-wrap"
    @mouseenter="cancelClose"
    @mouseleave="scheduleCloseFromHover"
  >
    <button
      ref="entryRef"
      type="button"
      :class="['ps-entry', { open: isOpen }]"
      aria-haspopup="menu"
      :aria-expanded="isOpen"
      :title="desc || label"
      @click.stop="toggle"
      @mouseenter="openFromHover"
      @keydown.right.prevent="open"
      @keydown.left.prevent="close"
    >
      <span class="ps-entry-icon"><slot name="icon" /></span>
      <span class="ps-entry-name">{{ label }}</span>
      <span v-if="count" class="ps-count">{{ count }}</span>
      <PremiumChevron class="ps-entry-arrow" direction="right" :size="14" interactive />
    </button>

    <div
      v-if="isOpen"
      :class="['ps-flyout', `placement-${placement}`]"
      :style="panelStyle"
      :role="isCompactMenu ? 'dialog' : undefined"
      :aria-label="isCompactMenu ? label : undefined"
      @click.stop
      @keydown.esc.stop="close"
    >
      <div class="ps-card">
        <div class="ps-mobile-head">
          <button ref="backRef" type="button" aria-label="返回添加菜单" @click="close">
            <PremiumChevron direction="left" :size="16" interactive />
          </button>
          <strong>{{ label }}</strong>
          <span v-if="count" class="ps-mobile-count">已选 {{ count }}</span>
          <span v-else aria-hidden="true"></span>
        </div>
        <slot />
      </div>
    </div>
  </div>
</template>

<script lang="ts">
// 模块级互斥（注意：必须写在普通 <script> 里才是模块作用域，<script setup> 里的 let 是
// 每个实例各一份）。同一时刻只允许一个二级面板展开——少了它，鼠标从「我的文件」滑到
// 「知识库」的那 180ms 关闭延迟里，两个面板会并排叠着；延迟是给对角线移动兜底的，
// 不该让它变成视觉噪音。（与 ModelSelector 那套 PICKER_OPEN_EVENT 无关：那管的是
// + 菜单与外部控件之间的互斥。）
let closeOpenPanel: (() => void) | null = null;
</script>

<script setup lang="ts">
import { ref, computed, nextTick, onUnmounted } from 'vue';
import { useMediaQuery } from '@vueuse/core';
import PremiumChevron from './PremiumChevron.vue';
import { usePickerPlacement } from '../composables/usePickerPlacement';

withDefaults(
  defineProps<{
    label: string;
    /** 一句话说明。菜单收窄后（2026-07-28）不再常驻渲染，只作为条目的 title 悬停可见 */
    desc?: string;
    /** 已选数量；>0 时条目右侧出黑底计数 */
    count?: number;
  }>(),
  { desc: '', count: 0 },
);

const emit = defineEmits<{
  /** 每次展开都发一次：面板内容由父组件在这里拉取，保证每次打开都是最新的 */
  (e: 'open'): void;
}>();

const wrapRef = ref<HTMLElement | null>(null);
const entryRef = ref<HTMLButtonElement | null>(null);
const backRef = ref<HTMLButtonElement | null>(null);
const isOpen = ref(false);
const isCompactMenu = useMediaQuery('(max-width: 1024px)');

// 飞出面板锚在**整个 + 菜单**上，不是锚在自己这一行（2026-07-28 修）。
// 锚在行上时，可用高度＝这一行上方到视口顶的距离——菜单本身有七行近 300px 高，把
// 二级条目顶到了很靠上的位置，于是面板只剩 226px、一次只看得见两个技能。以菜单为锚，
// 可用高度多出整个菜单的高度，面板能开到 380px 满值，而且四个面板位置一致不跳。
// ⚠️ 依赖 `.plus-menu` 是定位上下文（它是 position:absolute）：.ps-wrap 因此**不设**
// position:relative，让 .ps-flyout 的 left/bottom 直接相对菜单算。
const anchorRef = computed(() => (wrapRef.value?.parentElement as HTMLElement | null) ?? wrapRef.value);
const { placement, panelStyle } = usePickerPlacement(anchorRef, isOpen, {
  preferredHeight: 380,
  measureFromFarEdge: true, // 面板贴菜单下沿向上长，可用高度要从菜单底边算起
});

// 悬停展开 / 移出延迟收起（标准二级菜单行为）。飞出面板与条目之间的 8px 间隙做成
// .ps-flyout 的 padding（不是 offset），指针横穿过去时不会掉出 .ps-wrap；延迟是斜穿的兜底。
let closeTimer: ReturnType<typeof setTimeout> | null = null;
let parentMenuScrollTop = 0;

function cancelClose() {
  if (closeTimer) {
    clearTimeout(closeTimer);
    closeTimer = null;
  }
}

function scheduleClose() {
  cancelClose();
  closeTimer = setTimeout(() => {
    isOpen.value = false;
  }, 180);
}

function scheduleCloseFromHover() {
  if (isCompactMenu.value) return;
  scheduleClose();
}

function openFromHover() {
  if (isCompactMenu.value) return;
  open();
}

function open() {
  cancelClose();
  if (isOpen.value) return;
  if (closeOpenPanel && closeOpenPanel !== close) closeOpenPanel(); // 别的二级面板立刻让位
  closeOpenPanel = close;
  isOpen.value = true;
  emit('open');
  if (isCompactMenu.value) {
    const menu = wrapRef.value?.closest<HTMLElement>('.plus-menu');
    parentMenuScrollTop = menu?.scrollTop || 0;
    if (menu) menu.scrollTop = 0;
    nextTick(() => backRef.value?.focus());
  }
}

function close() {
  cancelClose();
  if (closeOpenPanel === close) closeOpenPanel = null;
  isOpen.value = false;
  if (isCompactMenu.value) {
    nextTick(() => {
      const menu = wrapRef.value?.closest<HTMLElement>('.plus-menu');
      if (menu) menu.scrollTop = parentMenuScrollTop;
      entryRef.value?.focus();
    });
  }
}

// 整个 + 菜单被收起时组件直接卸载，close() 不会走到——不清掉这个模块级引用，
// 下次展开的第一个面板会去调一个已卸载实例的 close（无害但是脏的悬挂引用）
onUnmounted(() => {
  if (closeOpenPanel === close) closeOpenPanel = null;
});

function toggle() {
  if (isOpen.value) close();
  else open();
}

defineExpose({ close });
</script>

<style scoped>
/* 注意**不要**给它 position:relative——.ps-flyout 要相对 `.plus-menu`（定位上下文）算位置，
   这样面板贴的是菜单的上下边缘而不是这一行，可用高度才够（见 script 里 anchorRef 的注释）。 */
.ps-wrap {
  position: static;
}

.ps-mobile-head {
  display: none;
}

/* ===== + 菜单里的一行（与 ChatTab .plus-item 同款） ===== */
.ps-entry {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 34px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  padding: 6px 9px;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease;
}

.ps-entry:hover,
.ps-entry.open {
  background: #f5f6f8;
}

.ps-entry:focus {
  outline: none;
}

.ps-entry:focus-visible {
  outline: 1px solid #8f96a3;
  outline-offset: 0;
}

.ps-entry-icon {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  color: #6b7280;
  font-size: 15px;
}

.ps-entry-name {
  overflow: hidden;
  min-width: 0;
  flex: 1 1 auto;
  color: #1f2328;
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.ps-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  background: #111827;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
}

.ps-entry-arrow {
  flex: none;
  color: #b6bbc4;
  font-size: 11px;
}

/* ===== 向右飞出的面板 ===== */
/* 定位基准是 `.plus-menu`（.ps-wrap 特意不设 relative）：left:100% ＝ 菜单右缘，
   bottom/top 的 -6px ＝ 与菜单下/上沿基本齐平。四个面板因此位置一致，不随 hover 哪一行跳。 */
.ps-flyout {
  position: absolute;
  left: 100%;
  z-index: 101;
  padding-left: 8px; /* 与菜单之间的“桥”：做成外边距会让指针掉出 .ps-wrap */
}

.ps-flyout.placement-top {
  bottom: -6px;
}

.ps-flyout.placement-bottom {
  top: -6px;
}

.ps-card {
  display: flex;
  /* 可用高度由 usePickerPlacement 按触发器上/下方空间算；那个口径是从条目边缘起算的，
     向下展开时面板顶部还比条目顶部高 6px，极端窄视口会多出条目高度的溢出——再压一道
     视口兜底，任何情况下都不会顶穿上下边界。 */
  max-height: min(var(--picker-available-height, 380px), calc(100vh - 96px));
  flex-direction: column;
  width: min(340px, calc(100vw - 32px));
  border: 1px solid #e5e7eb;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.12);
  padding: 10px;
  animation: ps-flyout-enter 0.16s ease both;
}

.ps-flyout.placement-top .ps-card {
  transform-origin: left bottom;
}

.ps-flyout.placement-bottom .ps-card {
  transform-origin: left top;
}

/* 窄桌面放不下「390px 菜单 + 340px 面板」时的兼容回落。 */
@media (min-width: 1025px) and (max-width: 1180px) {
  .ps-flyout {
    left: auto;
    right: 0;
    padding-left: 0;
  }

  .ps-flyout.placement-top,
  .ps-flyout.placement-bottom {
    top: auto;
    bottom: calc(100% + 6px);
  }
}

/* 手机/iPad：二级选择器在同一底部面板内向前覆盖，不再左右飞出。 */
@media (max-width: 1024px) {
  .ps-entry {
    min-height: 52px;
    gap: 12px;
    padding: 8px 11px;
    border-radius: 12px;
  }

  .ps-entry-icon {
    width: 22px;
    font-size: 18px;
  }

  .ps-entry-name {
    font-size: 15px;
    font-weight: 560;
  }

  .ps-count {
    min-width: 20px;
    height: 20px;
    font-size: 12px;
  }

  .ps-entry-arrow {
    color: #8d939d;
  }

  .ps-flyout,
  .ps-flyout.placement-top,
  .ps-flyout.placement-bottom {
    position: absolute;
    z-index: 4;
    inset: 0;
    padding: 0;
  }

  .ps-card {
    width: 100%;
    height: 100%;
    max-height: none;
    box-sizing: border-box;
    border: 0;
    border-radius: 21px;
    padding: 8px 10px 10px;
    background: #fff;
    box-shadow: none;
    animation: ps-mobile-enter 0.2s cubic-bezier(0.22, 1, 0.36, 1) both;
  }

  .ps-mobile-head {
    display: grid;
    min-height: 56px;
    flex: none;
    grid-template-columns: 42px minmax(0, 1fr) 72px;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
    border-bottom: 1px solid #eceef1;
  }

  .ps-mobile-head > button {
    display: grid;
    width: 42px;
    height: 42px;
    padding: 0;
    place-items: center;
    border: 0;
    border-radius: 13px;
    background: #f2f3f5;
    color: #4d525c;
    cursor: pointer;
  }

  .ps-mobile-head > button:hover {
    background: #e8e9ec;
    color: #111;
  }

  .ps-mobile-head > button:focus-visible,
  .ps-entry:focus-visible {
    outline: 2px solid #7786d9;
    outline-offset: 1px;
  }

  .ps-mobile-head strong {
    overflow: hidden;
    color: #17191e;
    font-size: 16px;
    font-weight: 700;
    text-align: center;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .ps-mobile-count {
    justify-self: end;
    color: #767d88;
    font-size: 12px;
    white-space: nowrap;
  }
}

@keyframes ps-mobile-enter {
  from {
    opacity: 0;
    transform: translateX(18px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

@keyframes ps-flyout-enter {
  from {
    opacity: 0;
    transform: translateX(-4px) scale(0.985);
  }
  to {
    opacity: 1;
    transform: translateX(0) scale(1);
  }
}

@media (prefers-reduced-motion: reduce) {
  .ps-card {
    animation: none;
  }
}
</style>
