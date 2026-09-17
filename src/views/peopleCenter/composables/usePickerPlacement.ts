import { computed, nextTick, onMounted, onUnmounted, ref, watch, type Ref } from 'vue';

export type PickerPlacement = 'top' | 'bottom';

interface PickerPlacementOptions {
  safeTop?: number;
  safeBottom?: number;
  gap?: number;
  minHeight?: number;
  preferredHeight?: number;
  /**
   * 面板与锚元素的关系（默认 false＝面板在锚元素**外侧**，向上开就整个在锚之上）。
   *
   * 置 true 表示面板与锚**同侧对齐**：向上开时面板底边贴锚的底边、可以盖住锚本身
   * （+ 菜单的二级飞出面板就是这样，它贴着整个菜单的下沿向上长）。这时可用高度要从
   * 锚的**底边**往上算，从顶边算会白白少掉一整个锚的高度——菜单高近 300px，少算这
   * 一截会让面板只剩两行可见。
   */
  measureFromFarEdge?: boolean;
}

/**
 * 让输入框工具面板避开浏览器顶部与底部边界。面板仍锚定触发器，空间不足时
 * 先收缩为内部滚动，极端情况下再翻转方向，避免遮住顶栏或被视口裁切。
 */
export function usePickerPlacement(
  wrapRef: Ref<HTMLElement | null>,
  isOpen: Ref<boolean>,
  options: PickerPlacementOptions = {},
) {
  const safeTop = options.safeTop ?? 72;
  const safeBottom = options.safeBottom ?? 16;
  const gap = options.gap ?? 8;
  const minHeight = options.minHeight ?? 220;
  const preferredHeight = options.preferredHeight ?? 420;

  const placement = ref<PickerPlacement>('top');
  const availableHeight = ref(preferredHeight);

  const panelStyle = computed(() => ({
    '--picker-available-height': `${availableHeight.value}px`,
  }));

  function updatePlacement() {
    if (!isOpen.value || !wrapRef.value || typeof window === 'undefined') return;
    const rect = wrapRef.value.getBoundingClientRect();
    // 同侧对齐时，向上开量到锚的底边、向下开量到锚的顶边（见 measureFromFarEdge 注释）
    const topFrom = options.measureFromFarEdge ? rect.bottom : rect.top;
    const bottomFrom = options.measureFromFarEdge ? rect.top : rect.bottom;
    const spaceAbove = Math.max(0, Math.floor(topFrom - safeTop - gap));
    const spaceBelow = Math.max(0, Math.floor(window.innerHeight - bottomFrom - safeBottom - gap));
    const shouldOpenTop = spaceAbove >= minHeight || spaceAbove >= spaceBelow;
    placement.value = shouldOpenTop ? 'top' : 'bottom';
    availableHeight.value = Math.max(160, Math.min(preferredHeight, shouldOpenTop ? spaceAbove : spaceBelow));
  }

  function scheduleUpdate() {
    if (!isOpen.value) return;
    nextTick(updatePlacement);
  }

  watch(isOpen, (open) => {
    if (open) scheduleUpdate();
  });

  onMounted(() => {
    window.addEventListener('resize', scheduleUpdate, { passive: true });
    window.addEventListener('scroll', scheduleUpdate, { passive: true, capture: true });
  });

  onUnmounted(() => {
    window.removeEventListener('resize', scheduleUpdate);
    window.removeEventListener('scroll', scheduleUpdate, true);
  });

  return {
    placement,
    panelStyle,
    updatePlacement,
  };
}
