import { getAgentIconUrl } from './agentIcon';

describe('getAgentIconUrl', () => {
  it('keeps frontend proxy static paths as-is', () => {
    const icon = '/api/sys/common/static/ai-platform/system/u1.png';
    expect(getAgentIconUrl({ appIcon: icon })).toBe(icon);
  });

  it('keeps MinIO object urls as-is', () => {
    const icon = 'http://127.0.0.1:9098/ai-platform/system/temp/a.png';
    expect(getAgentIconUrl({ icon })).toBe(icon);
  });

  it('maps object names through the frontend static proxy', () => {
    expect(getAgentIconUrl({ appIcon: 'ai-platform/system/u1.png' })).toBe(
      '/api/sys/common/static/ai-platform/system/u1.png',
    );
  });

  it('does not double-prefix proxy paths stored without a leading slash', () => {
    expect(getAgentIconUrl({ appIcon: 'api/sys/common/static/ai-platform/system/u1.png' })).toBe(
      '/api/sys/common/static/ai-platform/system/u1.png',
    );
  });
});
