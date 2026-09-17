const STATIC_FILE_PATH = '/sys/common/static/';
const DEFAULT_STATIC_PROXY_BASE = '/api';
const BUCKET_NAME_PATTERN = /^[a-z0-9][a-z0-9.-]{1,62}$/;

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '');
}

function trimLeadingSlash(value: string): string {
  return value.replace(/^\/+/, '');
}

function buildStaticPrefix(baseApiUrl: string): string {
  return `${trimTrailingSlash(baseApiUrl || '')}${STATIC_FILE_PATH}`;
}

function isLegacyArrayValue(fileUrl: string): boolean {
  return fileUrl.indexOf('[') !== -1;
}

function isBrowserLocalUrl(fileUrl: string): boolean {
  return /^(data|blob):/i.test(fileUrl);
}

/** `/api/sys/common/static/...` 已经是走前端代理的静态地址，不能再拼一层 domainUrl。 */
function isRelativeStaticFilePath(fileUrl: string): boolean {
  return !isHttpUrl(fileUrl) && !isBrowserLocalUrl(fileUrl) && fileUrl.indexOf(STATIC_FILE_PATH) !== -1;
}

function isHttpUrl(fileUrl: string): boolean {
  return /^https?:\/\//i.test(fileUrl);
}

function isStaticFileUrl(fileUrl: string, staticPrefix: string): boolean {
  if (fileUrl.startsWith(staticPrefix)) {
    return true;
  }
  try {
    return isHttpUrl(fileUrl) && new URL(fileUrl).pathname.indexOf(STATIC_FILE_PATH) !== -1;
  } catch (err) {
    return false;
  }
}

function isSameOriginOrBase(url: URL, baseApiUrl: string): boolean {
  if (!baseApiUrl) {
    return false;
  }
  try {
    const base = new URL(baseApiUrl);
    return url.origin === base.origin && url.pathname.startsWith(base.pathname.replace(/\/+$/, ''));
  } catch (err) {
    return false;
  }
}

function looksLikeBucketObjectPath(pathname: string): boolean {
  const segments = trimLeadingSlash(pathname).split('/').filter(Boolean);
  return segments.length >= 2 && BUCKET_NAME_PATTERN.test(segments[0]);
}

function shouldProxyFullObjectUrl(fileUrl: string, baseApiUrl: string): boolean {
  try {
    const url = new URL(fileUrl);
    return /^https?:$/i.test(url.protocol) && !isSameOriginOrBase(url, baseApiUrl) && looksLikeBucketObjectPath(url.pathname);
  } catch (err) {
    return false;
  }
}

/**
 * 将历史数据中已写入的静态文件完整地址还原为对象路径。
 *
 * 上传后的对象路径需要跟随当前环境的 API 域名生成访问地址；若把完整地址存进业务表，
 * 从测试环境切到生产（或通过 Vite 代理访问）时会继续请求旧主机，导致图片 404。
 */
export function toStaticFileStoragePath(fileUrl: string): string {
  const value = String(fileUrl || '').trim();
  if (!isHttpUrl(value)) {
    return value;
  }

  try {
    const pathname = new URL(value).pathname;
    const staticPathIndex = pathname.indexOf(STATIC_FILE_PATH);
    if (staticPathIndex === -1) {
      return value;
    }
    const objectPath = trimLeadingSlash(pathname.slice(staticPathIndex + STATIC_FILE_PATH.length));
    return objectPath || value;
  } catch (err) {
    return value;
  }
}

export function normalizeFileAccessHttpUrl(fileUrl: string, baseApiUrl: string, prefix = 'http'): string {
  let result = fileUrl;
  try {
    if (!fileUrl || fileUrl.length === 0 || isLegacyArrayValue(fileUrl) || isBrowserLocalUrl(fileUrl)) {
      return result;
    }

    if (isRelativeStaticFilePath(fileUrl)) {
      return fileUrl.startsWith('/') ? fileUrl : `/${fileUrl}`;
    }

    const staticPrefix = buildStaticPrefix(baseApiUrl);
    if (isStaticFileUrl(fileUrl, staticPrefix)) {
      return result;
    }

    if (isHttpUrl(fileUrl)) {
      if (shouldProxyFullObjectUrl(fileUrl, baseApiUrl)) {
        const url = new URL(fileUrl);
        return `${staticPrefix}${trimLeadingSlash(url.pathname)}`;
      }
      return result;
    }

    if (!fileUrl.startsWith(prefix)) {
      result = `${staticPrefix}${trimLeadingSlash(fileUrl)}`;
    }
  } catch (err) {}
  return result;
}

/**
 * Turn a stored object key into a same-origin static URL. This is used by
 * user-facing views running behind Vite/nginx, where the remote domain alone
 * does not include the Java service's `/admin-api` prefix.
 */
export function getProxyStaticFileUrl(fileUrl: string, proxyBase = DEFAULT_STATIC_PROXY_BASE): string {
  const value = String(fileUrl || '').trim();
  if (!value || isBrowserLocalUrl(value)) return value;

  const base = trimTrailingSlash(proxyBase || DEFAULT_STATIC_PROXY_BASE);
  const staticPrefix = `${base}${STATIC_FILE_PATH}`;
  const staticPathIndex = value.indexOf(STATIC_FILE_PATH);
  if (staticPathIndex !== -1) {
    return `${base}${value.slice(staticPathIndex)}`;
  }

  if (isHttpUrl(value)) {
    try {
      const url = new URL(value);
      if (looksLikeBucketObjectPath(url.pathname)) {
        return `${staticPrefix}${trimLeadingSlash(url.pathname)}`;
      }
    } catch (err) {
      return value;
    }
    return value;
  }
  return `${staticPrefix}${trimLeadingSlash(value)}`;
}
