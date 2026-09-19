export type RuntimeRouteRecord = {
  id?: string | number;
  appId?: string | number;
  workflowAppId?: string | number;
  appInfoId?: string | number;
  aiAppType?: string;
};

const RUN_PATH = /^\/agent\/run\/[^/]+\/?$/;

function currentOrigin() {
  return typeof window !== 'undefined' ? window.location.origin : 'http://local.invalid';
}

/**
 * Runtime entry is the standalone conversation page.
 * Legacy records may still expose workflowAppId/appId/id, but the UI route is always /agent/run/:appId.
 */
export function getAiAppRunHref(record: RuntimeRouteRecord) {
  return `/agent/run/${String(record.workflowAppId || record.appId || record.id || '')}`;
}

export function getAiAppDraftPreviewRoute(workflowAppId: string) {
  return { path: `/agent/run/${workflowAppId}`, query: { previewDraft: '1' } };
}

/** 广场 pcUrl / 应用记录 → 新标签地址。同源运行页去掉 from/user/timestamp 等残留 query。 */
export function resolveAgentRunHref(target: string | RuntimeRouteRecord, origin = currentOrigin()): string {
  if (typeof target !== 'string') return getAiAppRunHref(target);
  const raw = String(target || '').trim();
  if (!raw) return '';
  try {
    const parsed = new URL(raw, origin);
    if (RUN_PATH.test(parsed.pathname) && parsed.origin === origin) {
      return parsed.pathname.replace(/\/+$/, '') || parsed.pathname;
    }
    return parsed.href;
  } catch {
    return raw;
  }
}

type OpenFn = (url: string, target: string) => Window | null;
type NavigateFn = (path: string) => void;

/**
 * 站内跳转的实现由应用启动时注入（见 router/guard）。
 * 本模块被 jest.config.workflow 当作「无别名的纯函数模块」直接引入，顶层
 * import 路由会把整个应用拉进 node 单测链路，所以不能直接依赖路由实例。
 */
let registeredNavigate: NavigateFn | null = null;

export function setRuntimeNavigator(fn: NavigateFn | null) {
  registeredNavigate = fn;
}

/**
 * 广场、主对话推荐位、我的文件等入口共用的「打开智能体运行页」。
 *
 * 同源地址走站内路由跳转，不再新开标签页：新标签页没有历史，运行页里的「返回」
 * 无处可回，浏览器后退键也是灰的——用户只能手动关标签页，这不是合理的交互。
 * 外部地址（第三方智能体的 pcUrl）仍然新开标签页并断开 opener。
 *
 * 返回是否成功发起跳转，调用方据此提示失败。
 */
export function openAgentRunWindow(
  target: string | RuntimeRouteRecord,
  openFn: OpenFn = (url, name) => window.open(url, name),
  navigateFn?: NavigateFn,
): boolean {
  const href = resolveAgentRunHref(target);
  if (!href) return false;

  const internalPath = toInternalPath(href);
  const navigate = navigateFn || registeredNavigate;
  if (internalPath && navigate) {
    navigate(internalPath);
    return true;
  }
  // 没有注册导航器时退回新开标签页，宁可少一层体验也不要点了没反应。

  const child = openFn(href, '_blank');
  if (child) {
    try {
      child.opener = null;
    } catch {
      // 跨域或浏览器限制时忽略
    }
  }
  return Boolean(child);
}

/** 同源地址 → 站内路径（含 query/hash）；外部地址 → null。 */
function toInternalPath(href: string): string | null {
  if (href.startsWith('/')) return href;
  try {
    const parsed = new URL(href, currentOrigin());
    if (parsed.origin !== currentOrigin()) return null;
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return null;
  }
}
