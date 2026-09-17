/**
 * agent-api 鉴权头：有 token 不带未签名用户头；无 token 才回落网关身份头。
 */
jest.mock(
  '/@/store/modules/user',
  () => ({
    useUserStore: () => mockStore,
  }),
  { virtual: true },
);
jest.mock(
  '/@/utils/auth',
  () => ({
    getToken: () => mockStore.tokenFromAuth,
  }),
  { virtual: true },
);

const mockStore = {
  getToken: '',
  tokenFromAuth: '',
  getUserInfo: { id: 'uid-1', username: 'alice' } as Record<string, unknown>,
};

import { agentAuthHeaders, resolveAgentAccessToken } from './agentAuthHeaders';

describe('agentAuthHeaders', () => {
  beforeEach(() => {
    mockStore.getToken = '';
    mockStore.tokenFromAuth = '';
    mockStore.getUserInfo = { id: 'uid-1', username: 'alice' };
  });

  it('有 token 时只带 Authorization / X-Access-Token', () => {
    mockStore.tokenFromAuth = 'jwt-abc';
    const h = agentAuthHeaders();
    expect(h['X-Access-Token']).toBe('jwt-abc');
    expect(h.Authorization).toBe('jwt-abc');
    expect(h['X-User-Id']).toBeUndefined();
    expect(h['X-Username']).toBeUndefined();
    expect(resolveAgentAccessToken()).toBe('jwt-abc');
  });

  it('pinia getToken 可作 fallback', () => {
    mockStore.getToken = 'from-pinia';
    expect(resolveAgentAccessToken()).toBe('from-pinia');
  });

  it('无 token 时才带用户身份头（供无密钥本地环境）', () => {
    const h = agentAuthHeaders({ 'Content-Type': 'application/json' });
    expect(h['X-Access-Token']).toBeUndefined();
    expect(h['X-User-Id']).toBe('uid-1');
    expect(h['X-Username']).toBe('alice');
    expect(h['Content-Type']).toBe('application/json');
  });

  it('userId 字段也可作身份', () => {
    mockStore.getUserInfo = { userId: 'u2', username: 'bob' };
    const h = agentAuthHeaders();
    expect(h['X-User-Id']).toBe('u2');
  });
});
