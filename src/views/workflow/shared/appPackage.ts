export const WORKFLOW_APP_PACKAGE_MAX_SIZE = 5 * 1024 * 1024;
export const WORKFLOW_APP_PACKAGE_EXTENSION = '.qz-agent.json';

export function buildWorkflowAppPackageFilename(name?: string) {
  const safeName = String(name || '智能体')
    .trim()
    .replace(/[\\/:*?"<>|]+/g, '-')
    .replace(/\s+/g, ' ')
    .replace(/^-+|-+$/g, '');
  return `${safeName || '智能体'}${WORKFLOW_APP_PACKAGE_EXTENSION}`;
}

export function isWorkflowAppPackageFile(file: { name?: string; size?: number }) {
  const name = String(file?.name || '').toLowerCase();
  const size = Number(file?.size || 0);
  return size <= WORKFLOW_APP_PACKAGE_MAX_SIZE && (name.endsWith('.json') || name.endsWith(WORKFLOW_APP_PACKAGE_EXTENSION));
}

export function buildWorkflowAppUploadHeaders(token?: string) {
  const headers: Record<string, string> = {};
  if (token) {
    headers['X-Access-Token'] = token;
    headers.Authorization = token;
  }
  return headers;
}
