export const SYSTEM_UPLOAD_PATH = '/sys/common/upload';

function normalizePath(path: string): string {
  return path.startsWith('/') ? path : `/${path}`;
}

export function buildUploadActionUrl(baseUrl: string, path = SYSTEM_UPLOAD_PATH): string {
  const normalizedPath = normalizePath(path);
  const normalizedBase = (baseUrl || '').trim().replace(/\/+$/, '');
  return normalizedBase ? `${normalizedBase}${normalizedPath}` : normalizedPath;
}

export function getUploadRequestUrl(path = SYSTEM_UPLOAD_PATH): string {
  return normalizePath(path);
}
