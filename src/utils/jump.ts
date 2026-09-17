  import { useUserStore } from '/@/store/modules/user';

  function getAppJumpHeaders() {
    const userStore = useUserStore();
    const headers: Record<string, string> = { 'X-Version': 'v3' };
    const token = userStore.getToken;
    const info: any = userStore.getUserInfo || {};
    const tenantId = userStore.hasShareTenantId && userStore.shareTenantId !== 0 ? userStore.shareTenantId : userStore.getTenant;

    if (token) {
      headers['X-Access-Token'] = token;
      headers.Authorization = token;
    }
    if (tenantId !== undefined && tenantId !== null && tenantId !== '') headers['X-Tenant-Id'] = String(tenantId);
    if (info.id) headers['X-User-Id'] = String(info.id);
    if (info.username) headers['X-Username'] = String(info.username);

    return headers;
  }

  export function resolveAppJumpUrl(url: string) {
    const headers = getAppJumpHeaders();
    const normalizedHeaders = Object.fromEntries(Object.entries(headers).map(([name, value]) => [name.toLowerCase(), value]));
    const missingHeaders = new Set<string>();
    const resolvedUrl = url.replace(/\$\{([^{}]+)\}/g, (placeholder, headerName: string) => {
      const normalizedName = headerName.trim().toLowerCase();
      const value = normalizedHeaders[normalizedName];
      if (value === undefined || value === '') {
        missingHeaders.add(headerName.trim());
        return placeholder;
      }
      return encodeURIComponent(value);
    });

    if (missingHeaders.size) {
      throw new Error(`跳转地址缺少请求头变量：${Array.from(missingHeaders).join('、')}`);
    }
    return resolvedUrl;
  }