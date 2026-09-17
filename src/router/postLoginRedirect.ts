import { PageEnum } from '/@/enums/pageEnum';

const BLOCKED_PATHS = new Set([
  PageEnum.BASE_LOGIN,
  PageEnum.OAUTH2_LOGIN_PAGE_PATH,
  PageEnum.TOKEN_LOGIN,
  '/500',
  '/400',
  '/404',
]);

const LOGIN_REDIRECT_PREFIX = `${PageEnum.BASE_LOGIN}?redirect=`;

function firstQueryValue(value: unknown): string {
  if (Array.isArray(value)) return String(value[0] ?? '').trim();
  return typeof value === 'string' ? value.trim() : '';
}

function decodeSafely(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function pathOnly(value: string): string {
  return value.split('?')[0].split('#')[0];
}

/** Peel /login?redirect=/login?redirect=/center/chat down to /center/chat. */
export function unwrapLoginRedirect(value: unknown): string {
  let current = firstQueryValue(value);
  for (let i = 0; i < 10 && current; i += 1) {
    const decoded = decodeSafely(current).trim();
    if (decoded.startsWith(LOGIN_REDIRECT_PREFIX)) {
      current = decoded.slice(LOGIN_REDIRECT_PREFIX.length);
      continue;
    }
    if (BLOCKED_PATHS.has(pathOnly(decoded))) {
      const query = decoded.includes('?') ? decoded.slice(decoded.indexOf('?') + 1) : '';
      current = new URLSearchParams(query).get('redirect') || '';
      continue;
    }
    return decoded;
  }
  return '';
}

/** Same-origin path only: leading slash, not protocol-relative, not an external URL. */
export function isSafeInternalRedirect(path: unknown): path is string {
  if (typeof path !== 'string') return false;
  const value = path.trim();
  if (!value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return false;
  const decoded = decodeSafely(value);
  if (decoded.startsWith('//') || /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(decoded)) return false;
  const only = pathOnly(value);
  if (!only || only === '/' || BLOCKED_PATHS.has(only)) return false;
  return true;
}

export function resolvePostLoginPath(redirect: unknown, homePath?: string | null): string {
  const fallback = isSafeInternalRedirect(homePath) ? homePath : PageEnum.BASE_HOME;
  const candidate = unwrapLoginRedirect(redirect);
  return isSafeInternalRedirect(candidate) ? candidate : fallback;
}

/** Query to take the user back after re-login. Empty when the current page is login itself. */
export function loginRedirectQuery(fullPath: unknown): Record<string, string> {
  const unwrapped = unwrapLoginRedirect(fullPath);
  if (isSafeInternalRedirect(unwrapped)) return { redirect: unwrapped };
  if (isSafeInternalRedirect(fullPath)) return { redirect: String(fullPath).trim() };
  return {};
}
