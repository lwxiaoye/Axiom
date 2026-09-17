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

export function getAiAppRunRoute(record: RuntimeRouteRecord, from?: 'agent' | 'my-agent') {
  const path = getAiAppRunHref(record);
  if (!from) return { path };
  return { path, query: { from } };
}

export function getAiAppDraftPreviewRoute(workflowAppId: string) {
  return { path: `/agent/run/${workflowAppId}`, query: { previewDraft: '1' } };
}

/** 广场 pcUrl / 我的智能体记录 → 新标签地址。同源运行页去掉 from/user/timestamp 等残留 query。 */
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

/** 广场和「我的智能体」共用：新开 `/agent/run/:id`。 */
export function openAgentRunWindow(
  target: string | RuntimeRouteRecord,
  openFn: OpenFn = (url, name) => window.open(url, name),
): Window | null {
  const href = resolveAgentRunHref(target);
  if (!href) return null;
  const child = openFn(href, '_blank');
  if (child) {
    try {
      child.opener = null;
    } catch {
      // 跨域或浏览器限制时忽略
    }
  }
  return child;
}
