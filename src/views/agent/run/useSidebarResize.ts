import { onBeforeUnmount, ref } from 'vue';

const DEFAULT_WIDTH = 240;
const MIN_WIDTH = 188;
const MAX_WIDTH = 420;

function readWidth(key: string) {
  try {
    const raw = Number(localStorage.getItem(key) || '');
    if (Number.isFinite(raw) && raw >= MIN_WIDTH && raw <= MAX_WIDTH) return raw;
  } catch {
    // 读不到缓存就用默认宽度
  }
  return DEFAULT_WIDTH;
}

export function useSidebarResize(storageKey = 'agent-run:sidebar-width', resizeEdge: 'left' | 'right' = 'right') {
  const sidebarWidth = ref(readWidth(storageKey));
  const sidebarCollapsed = ref(false);
  const splitting = ref(false);
  let startX = 0;
  let startW = DEFAULT_WIDTH;

  function persist() {
    try {
      localStorage.setItem(storageKey, String(sidebarWidth.value));
    } catch {
      // 写缓存失败不影响拖动
    }
  }

  function onMove(event: MouseEvent) {
    const delta = resizeEdge === 'left' ? startX - event.clientX : event.clientX - startX;
    const next = startW + delta;
    sidebarWidth.value = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, next));
  }

  function onUp() {
    splitting.value = false;
    window.removeEventListener('mousemove', onMove);
    window.removeEventListener('mouseup', onUp);
    persist();
  }

  function onSplitterDown(event: MouseEvent) {
    if (sidebarCollapsed.value) return;
    event.preventDefault();
    event.stopPropagation();
    splitting.value = true;
    startX = event.clientX;
    startW = sidebarWidth.value;
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }

  onBeforeUnmount(onUp);

  function toggleSidebar() {
    if (splitting.value) onUp();
    sidebarCollapsed.value = !sidebarCollapsed.value;
  }

  return { sidebarWidth, sidebarCollapsed, splitting, onSplitterDown, toggleSidebar };
}
