// 单点登录核心类
import { getToken } from '/@/utils/auth';
import { getUrlParam } from '/@/utils';
import { useGlobSetting } from '/@/hooks/setting';
import { validateCasLogin } from '/@/api/sys/user';
import { useUserStore } from '/@/store/modules/user';
const globSetting = useGlobSetting();
const openSso = globSetting.openSso;

// CAS 的 service 固定是站点根地址；业务页只能在本地暂存，不能拼进 service，
// 否则会与统一认证平台登记的 service 不一致。
const SSO_REDIRECT_STORAGE_KEY = 'sso_redirect_after_login';

function isLocalPasswordLoginPage(): boolean {
  const pathname = window.location.pathname.replace(/\/+$/, '') || '/';
  return pathname === '/login';
}

function normalizeInternalRedirect(value: string | null | undefined): string | null {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.includes('\\')) {
    return null;
  }

  try {
    const target = new URL(value, window.location.origin);
    if (target.origin !== window.location.origin) return null;
    return `${target.pathname}${target.search}${target.hash}`;
  } catch {
    return null;
  }
}

function saveSsoRedirect() {
  const currentUrl = new URL(window.location.href);
  const requestedRedirect = currentUrl.pathname === '/login'
    ? currentUrl.searchParams.get('redirect')
    : `${currentUrl.pathname}${currentUrl.search}${currentUrl.hash}`;
  const redirect = normalizeInternalRedirect(requestedRedirect);

  if (redirect && redirect !== '/') {
    sessionStorage.setItem(SSO_REDIRECT_STORAGE_KEY, redirect);
  } else {
    sessionStorage.removeItem(SSO_REDIRECT_STORAGE_KEY);
  }
}

function consumeSsoRedirect(): string | null {
  const redirect = normalizeInternalRedirect(sessionStorage.getItem(SSO_REDIRECT_STORAGE_KEY));
  sessionStorage.removeItem(SSO_REDIRECT_STORAGE_KEY);
  return redirect;
}

export function useSso() {
  // 代码逻辑说明: 【QQYUN-7805】SSO登录强制用http #957---
  const locationUrl = document.location.protocol +"//" + window.location.host + '/';

  /**
   * 单点登录
   */
  async function ssoLogin() {
    if (openSso == 'true') {
      const token = getToken();
      const ticket = getUrlParam('ticket');
      if (!token) {
        if (ticket) {
          const res = await validateCasLogin({
            ticket: ticket,
            service: locationUrl,
          });
          const userStore = useUserStore();
          userStore.setToken(res.token);
          const redirect = consumeSsoRedirect();

          // 先完成用户信息初始化，再回到 CAS 跳转前的受保护页面。
          await userStore.afterLoginAction(!redirect, {});
          if (redirect) {
            window.location.replace(redirect);
          }
        } else if (!isLocalPasswordLoginPage()) {
          saveSsoRedirect();
          window.location.href = globSetting.casBaseUrl + '/login?service=' + encodeURIComponent(locationUrl);
        }
      }
    }
  }

  /**
   * 退出登录
   */
  async function ssoLoginOut() {
    window.location.href = globSetting.casBaseUrl + '/logout?service=' + encodeURIComponent(locationUrl);
  }
  return { ssoLogin, ssoLoginOut };
}
