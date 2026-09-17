/**
 * 连接器 API 契约（2026-07-28）。
 *
 * 这层最容易出的错不是逻辑，而是**路径/方法/请求体三者对不上后端**——接口写歪了在
 * 界面上表现为「点了没反应」或「保存不了」，而不会有任何报错线索。所以逐个端点钉死。
 *
 * 另一条硬性要求：请求体里只能出现令牌本身，不能把它顺手塞进 URL（URL 会进
 * nginx 访问日志、浏览器历史和 Referer）。
 */
jest.mock(
  '/@/store/modules/user',
  () => ({
    useUserStore: () => ({ getToken: 'tk', getUserInfo: { id: 7, username: 'u7' } }),
  }),
  { virtual: true },
);
jest.mock(
  '/@/utils/auth',
  () => ({
    getToken: () => 'tk',
  }),
  { virtual: true },
);

import {
  connectWithToken, disconnectConnector, getConnector, listConnectorResources, listConnectors,
  setConnectorEnabled, setConnectorResources, startInstall, startOAuth,
} from './connectors.api';

const okJson = () => ({ ok: true, json: async () => ({}) }) as any;

describe('连接器 API', () => {
  let fetchMock: jest.Mock;

  beforeEach(() => {
    fetchMock = jest.fn(async () => okJson());
    (globalThis as any).fetch = fetchMock;
  });

  function lastCall() {
    const calls = fetchMock.mock.calls;
    const [url, init] = calls[calls.length - 1];
    return { url: String(url), init: (init || {}) as RequestInit };
  }

  it('清单走 GET /agent-api/connectors 并带鉴权头', async () => {
    await listConnectors();
    const { url, init } = lastCall();
    expect(url).toBe('/agent-api/connectors');
    expect((init.headers as any)['X-Access-Token']).toBe('tk');
    expect((init.headers as any).Authorization).toBe('tk');
    // 有 token 时不得再带未签名网关身份头（否则 GATEWAY_IDENTITY_SECRET 下会 401）
    expect((init.headers as any)['X-User-Id']).toBeUndefined();
    expect((init.headers as any)['X-Username']).toBeUndefined();
  });

  it('发起登录授权带 return_to（原页跳转，回调靠它跳回原页）', async () => {
    await startOAuth('github', '/center/chat?x=1');
    const { url, init } = lastCall();
    expect(url).toBe('/agent-api/connectors/github/oauth/start');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ return_to: '/center/chat?x=1' });
  });

  it('发起仓库授权（第二段）是 POST /install/start，同样带 return_to', async () => {
    await startInstall('github', '/center/chat');
    const { url, init } = lastCall();
    expect(url).toBe('/agent-api/connectors/github/install/start');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ return_to: '/center/chat' });
  });

  it('授权等待期轮询的是单个连接器状态（GET，无请求体）', async () => {
    await getConnector('github');
    const { url, init } = lastCall();
    expect(url).toBe('/agent-api/connectors/github');
    expect(init.method).toBeUndefined();
  });

  it('令牌连接：令牌只出现在请求体，绝不进 URL', async () => {
    await connectWithToken('github', 'ghp_secret');
    const { url, init } = lastCall();
    expect(url).toBe('/agent-api/connectors/github/token');
    expect(url).not.toContain('ghp_secret');
    expect(JSON.parse(String(init.body))).toEqual({ token: 'ghp_secret' });
  });

  it('启用开关是 PATCH /enabled', async () => {
    await setConnectorEnabled('github', false);
    const { url, init } = lastCall();
    expect(url).toBe('/agent-api/connectors/github/enabled');
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(String(init.body))).toEqual({ enabled: false });
  });

  it('资源读取是 GET、保存是 PUT（读写不同方法，写错会静默不保存）', async () => {
    await listConnectorResources('github');
    expect(lastCall().url).toBe('/agent-api/connectors/github/resources');

    await setConnectorResources('github', ['acme/web']);
    const { url, init } = lastCall();
    expect(url).toBe('/agent-api/connectors/github/resources');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(String(init.body))).toEqual({ resources: ['acme/web'] });
  });

  it('断开是 DELETE', async () => {
    await disconnectConnector('github');
    const { url, init } = lastCall();
    expect(url).toBe('/agent-api/connectors/github');
    expect(init.method).toBe('DELETE');
  });

  it('后端错误把 detail 抛成可展示的话术，而不是「请求失败：400」', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => ({ detail: 'GitHub 凭据无效' }),
    } as any);
    await expect(connectWithToken('github', 'bad')).rejects.toThrow('GitHub 凭据无效');
  });

  it('非 JSON 错误响应退回状态码文案，不抛解析异常', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 502,
      json: async () => {
        throw new Error('not json');
      },
    } as any);
    await expect(listConnectors()).rejects.toThrow('请求失败：502');
  });
});
