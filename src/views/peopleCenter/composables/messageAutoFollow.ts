export const AUTO_FOLLOW_RESUME_DISTANCE = 40;
export const AUTO_FOLLOW_BUTTON_DISTANCE = 120;
export const AUTO_FOLLOW_PAUSE_DISTANCE = 160;

const SCROLL_DIRECTION_EPSILON = 0.5;

/** 布局可以由列表或任意外壳承载滚动，不能依赖某个入口专用的 class。 */
export function resolveMessageScrollContainer(start: HTMLElement | null): HTMLElement | null {
  const view = start?.ownerDocument.defaultView;
  if (!view) return null;
  let fallback: HTMLElement | null = null;
  for (let current = start; current; current = current.parentElement) {
    if (!/^(auto|scroll|overlay)$/.test(view.getComputedStyle(current).overflowY)) continue;
    fallback ||= current;
    if (current.scrollHeight > current.clientHeight + 2) return current;
  }
  // 内容尚短时也要绑定监听，首批文字撑出滚动条后才能继续跟随。
  return fallback;
}

export type ScrollDirection = 'away' | 'toward' | 'stationary';

export function scrollDirection(currentScrollTop: number, previousScrollTop: number): ScrollDirection {
  if (currentScrollTop < previousScrollTop - SCROLL_DIRECTION_EPSILON) return 'away';
  if (currentScrollTop > previousScrollTop + SCROLL_DIRECTION_EPSILON) return 'toward';
  return 'stationary';
}

export function shouldPauseAutoFollowForThought(opening: boolean, loading: boolean): boolean {
  return opening && loading;
}

export function hasAutoFollowReadingPause(manualReading: boolean, openThoughtCount: number): boolean {
  return manualReading || openThoughtCount > 0;
}

export function canAutoFollow(
  stickToBottom: boolean,
  manualReading: boolean,
  openThoughtCount: number,
): boolean {
  return stickToBottom && !hasAutoFollowReadingPause(manualReading, openThoughtCount);
}

export function shouldResumeManualAutoFollow(
  manualReading: boolean,
  distanceFromBottom: number,
  direction: ScrollDirection,
  pointerActive = false,
): boolean {
  return manualReading
    && !pointerActive
    && direction === 'toward'
    && distanceFromBottom >= 0
    && distanceFromBottom < AUTO_FOLLOW_RESUME_DISTANCE;
}

/** 手指还在屏上、或已经贴底/过拉时不要再写 scrollTop，避免和弹性滚动互抢。 */
export function shouldSkipProgrammaticStick(
  pointerActive: boolean,
  distanceFromBottom: number,
): boolean {
  return pointerActive || distanceFromBottom <= 1;
}
