/**
 * 内置助手独立页（/center/chat/campus|ppt|interview）的「打开」逻辑，
 * 供智能体广场、主对话推荐位、我的文件等入口共用。
 */

type OpenFn = (url: string, target: string) => Window | null;
type NavigateFn = (path: string) => void;

function currentOrigin() {
  return typeof window !== 'undefined' ? window.location.origin : 'http://local.invalid';
}

/**
 * 站内跳转的实现由应用启动时注入（见 router/guard）。
 * 本模块被 jest 当作「无别名的纯函数模块」直接引入，顶层 import 路由会把整个应用
 * 拉进 node 单测链路，所以不能直接依赖路由实例。
 */
let registeredNavigate: NavigateFn | null = null;

export function setAssistantNavigator(fn: NavigateFn | null) {
  registeredNavigate = fn;
}

/** 同源地址 → 站内路径（含 query/hash）；外部地址 → null。 */
export function toInternalPath(href: string, origin = currentOrigin()): string | null {
  const raw = String(href || '').trim();
  if (!raw) return null;
  if (raw.startsWith('/')) return raw;
  try {
    const parsed = new URL(raw, origin);
    if (parsed.origin !== origin) return null;
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return null;
  }
}

/**
 * 打开内置助手页。
 *
 * 同源地址走站内路由跳转，不新开标签页：新标签页没有历史，助手页里的「返回」
 * 无处可回，浏览器后退键也是灰的。没有注册导航器（或地址不同源）时退回新开
 * 标签页并断开 opener，宁可少一层体验也不要点了没反应。
 *
 * 返回是否成功发起跳转，调用方据此提示失败。
 */
export function openBuiltinAssistantPage(
  target: string,
  openFn: OpenFn = (url, name) => window.open(url, name),
  navigateFn?: NavigateFn,
): boolean {
  const href = String(target || '').trim();
  if (!href) return false;

  const internalPath = toInternalPath(href);
  const navigate = navigateFn || registeredNavigate;
  if (internalPath && navigate) {
    navigate(internalPath);
    return true;
  }

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
