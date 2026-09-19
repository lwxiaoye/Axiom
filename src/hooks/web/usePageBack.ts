import type { Router } from 'vue-router';
import { useRouter } from 'vue-router';

/**
 * 「返回上一页」。
 *
 * 站内是否真的有上一页，window.history.length 判断不了——它把进入本站之前的
 * 条目也算进去，而且从来不减少。靠的是 vue-router 写进 history.state 的 position
 * （历史栈里的下标）：应用首屏那一条记为基准，之后 push 会加一、replace 不变、
 * back 会减一。当前 position 比基准大，说明本次会话里确实有站内上一页，才允许
 * router.back()；否则（新标签页直接打开、外部链接进来、登录后 replace 落地、刷新
 * 后的首屏）退回到 fallback，而不是把用户甩出这个站或退到空白页。
 */
let basePosition: number | null = null;

function currentPosition(): number {
  const state = typeof window === 'undefined' ? null : window.history.state;
  const position = state && typeof state.position === 'number' ? state.position : 0;
  return position;
}

/** 由 setupRouterGuard 在应用启动时装上，晚装会漏掉首屏那一跳。 */
export function setupBackTracking(target: Router) {
  target.afterEach((_to, from) => {
    // from 无 name 时是首屏（应用刚起来的那一次），把它的下标当基准
    if (!from.name && basePosition === null) basePosition = currentPosition();
  });
}

export function usePageBack(fallback: string, label = '返回') {
  const router = useRouter();
  // setup 在 afterEach 之后执行，所以「进入本页」这一跳的 position 已经写好。
  // 取一次快照即可，之后不会再变——组件在本页存活期间不会再发生跳转。
  const canGoBack = basePosition !== null && currentPosition() > basePosition;

  function goBack() {
    if (canGoBack) {
      router.back();
      return;
    }
    void router.replace(fallback);
  }

  return { goBack, canGoBack, backLabel: label };
}
