/**
 * agent-api 请求鉴权头（agentApi / myfiles / connectors 共用）。
 *
 * 有 JWT 时只带 token，不带未签名的 X-User-Id / X-Username：
 * agent-api 在 GATEWAY_IDENTITY_SECRET 已配置时会优先走网关身份路径并校验 HMAC，
 * 未签名头 → 401「Missing trusted gateway identity signature」，token 回源永远走不到。
 * （2026-08-08：模型列表、我的文件、连接器同坑。）
 */
import { useUserStore } from '/@/store/modules/user';
import { getToken } from '/@/utils/auth';

export function resolveAgentAccessToken(): string {
  const userStore = useUserStore();
  return String(getToken() || userStore.getToken || '').trim();
}

export function agentAuthHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { ...(extra || {}) };
  const token = resolveAgentAccessToken();
  if (token) {
    headers['X-Access-Token'] = token;
    headers.Authorization = token;
    return headers;
  }
  const userStore = useUserStore();
  const info: any = userStore.getUserInfo || {};
  const uid = info.id ?? info.userId;
  if (uid) headers['X-User-Id'] = String(uid);
  if (info.username) headers['X-Username'] = String(info.username);
  return headers;
}
