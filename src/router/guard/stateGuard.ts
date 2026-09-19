import type { Router } from 'vue-router';
import { useAppStore } from '/@/store/modules/app';
import { useMultipleTabStore } from '/@/store/modules/multipleTab';
import { useUserStore } from '/@/store/modules/user';
import { usePermissionStore } from '/@/store/modules/permission';
import { PageEnum } from '/@/enums/pageEnum';
import { removeTabChangeListener } from '/@/logics/mitt/routeChange';
import { clearUserScopedStorage } from '/@/views/peopleCenter/utils/userScopedStorage';

export function createStateGuard(router: Router) {
  router.afterEach((to, from) => {
    // Only clear auth when arriving at login from another page (logout / 401).
    // Clearing on every /login query change wipes a token just written by a successful login.
    if (to.path === PageEnum.BASE_LOGIN && from.path !== PageEnum.BASE_LOGIN) {
      const tabStore = useMultipleTabStore();
      const userStore = useUserStore();
      const appStore = useAppStore();
      const permissionStore = usePermissionStore();
      // 兜底：不经 userStore.logout() 直接落到登录页（手输 /login、第三方跳转等）时，
      // Persistent 里的 token/userInfo 会被下面 resetAllState 清掉，业务侧按用户作用域
      // 写的本机数据也得一起清。要在 resetState 之前跑——之后就读不到用户 id 了；
      // 已经走过 logout() 的场景这里读到空 id，只会再清一次旧的无前缀 key，无副作用。
      clearUserScopedStorage();
      appStore.resetAllState();
      permissionStore.resetState();
      tabStore.resetState();
      userStore.resetState();
      removeTabChangeListener();
    }
  });
}
