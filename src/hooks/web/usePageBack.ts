import type { Router } from 'vue-router';
import { useRouter } from 'vue-router';

/**
 * 「返回上一页」。
 *
 * 站内是否真的有上一页，window.history.length 判断不了——它把进入本站之前的
 * 条目也算进去，而且从来不减少。所以在路由守卫里自己数：只有本次会话里确实
 * 发生过站内跳转，才允许 router.back()；否则（新标签页直接打开、外部链接进来、
 * 刷新后的首屏）退回到 fallback，而不是把用户甩出这个站。
 */
let inAppNavigations = 0;

/** 由 setupRouterGuard 在应用启动时装上，晚装会漏掉最初几次跳转。 */
export function setupBackTracking(target: Router) {
  target.afterEach((_to, from) => {
    // from 无 name 时是首屏（应用刚起来的那一次），不算站内跳转
    if (from.name) inAppNavigations += 1;
  });
}

export function usePageBack(fallback: string, label = '返回') {
  const router = useRouter();
  // setup 在 afterEach 之后执行，所以「进入本页」这一跳已经计入。
  // 取一次快照即可，之后不会再变——组件在本页存活期间不会再发生跳转。
  const canGoBack = inAppNavigations > 0;

  function goBack() {
    if (canGoBack) {
      router.back();
      return;
    }
    void router.replace(fallback);
  }

  return { goBack, canGoBack, backLabel: label };
}
