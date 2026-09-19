import { getToken } from '/@/utils/auth';

export type ClientAuditEvent = {
  category: 'login' | 'knowledge_access' | 'export';
  action: string;
  resource?: string;
  detail?: string;
};

/**
 * Records a completed browser-side critical action without interrupting the
 * user flow when the optional Agent API audit service is unavailable.
 */
export function recordAuditEvent(event: ClientAuditEvent): void {
  const token = String(getToken() || '').trim();
  if (!token) return;
  void fetch('/agent-api/audit/events', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Access-Token': token,
      Authorization: token,
    },
    body: JSON.stringify(event),
    // 事件常在路由切换前一刻发出；不带 keepalive 会被页面卸载打断成 ERR_ABORTED（服务端多半已收到，
    // 但控制台一片红），keepalive 让浏览器把这几百字节发完。
    keepalive: true,
  }).catch(() => undefined);
}
