import { getProxyStaticFileUrl, normalizeFileAccessHttpUrl, toStaticFileStoragePath } from './fileUrl';

const baseApiUrl = 'http://localhost:9090/api';

describe('normalizeFileAccessHttpUrl', () => {
  it('proxies relative object names through the backend static endpoint', () => {
    expect(normalizeFileAccessHttpUrl('system/avatar/u1.png', baseApiUrl)).toBe(
      'http://localhost:9090/api/sys/common/static/system/avatar/u1.png',
    );
  });

  it('keeps backend static urls unchanged', () => {
    const url = 'http://localhost:9090/api/sys/common/static/system/avatar/u1.png';
    expect(normalizeFileAccessHttpUrl(url, baseApiUrl)).toBe(url);
  });

  it('keeps absolute backend static urls unchanged when the frontend uses a relative api proxy', () => {
    const url = 'http://localhost:9090/api/sys/common/static/system/avatar/u1.png';
    expect(normalizeFileAccessHttpUrl(url, '/api')).toBe(url);
  });

  it('converts persisted absolute static urls back to an environment-neutral object path', () => {
    expect(toStaticFileStoragePath('http://old-host:9090/api/sys/common/static/system/avatar/u1.png')).toBe(
      'system/avatar/u1.png',
    );
  });

  it('proxies MinIO bucket object urls through the backend static endpoint', () => {
    expect(normalizeFileAccessHttpUrl('http://object-storage.local:9098/ai-platform/system/avatar/u1.png', baseApiUrl)).toBe(
      'http://localhost:9090/api/sys/common/static/ai-platform/system/avatar/u1.png',
    );
  });

  it('preserves array-style legacy values', () => {
    const legacyValue = '["system/a.png","system/b.png"]';
    expect(normalizeFileAccessHttpUrl(legacyValue, baseApiUrl)).toBe(legacyValue);
  });

  it('does not re-prefix frontend proxy static paths against domainUrl', () => {
    const proxyPath = '/api/sys/common/static/ai-platform/system/u1.png';
    expect(normalizeFileAccessHttpUrl(proxyPath, 'http://127.0.0.1:9080/')).toBe(proxyPath);
  });

  it('adds a leading slash to proxy static paths stored without one', () => {
    expect(
      normalizeFileAccessHttpUrl('api/sys/common/static/ai-platform/system/u1.png', 'http://127.0.0.1:9080/'),
    ).toBe('/api/sys/common/static/ai-platform/system/u1.png');
  });
});

describe('getProxyStaticFileUrl', () => {
  it('maps an uploaded object path through the same-origin API proxy', () => {
    expect(getProxyStaticFileUrl('/system/avatar/u1.png')).toBe('/api/sys/common/static/system/avatar/u1.png');
  });

  it('normalizes an already-prefixed static path without duplicating /api', () => {
    expect(getProxyStaticFileUrl('/api/sys/common/static/system/avatar/u1.png')).toBe(
      '/api/sys/common/static/system/avatar/u1.png',
    );
  });

  it('proxies an uploaded MinIO object URL through the same-origin static endpoint', () => {
    expect(
      getProxyStaticFileUrl('http://127.0.0.1:9098/ai-platform/system/avatar/u1.jpg'),
    ).toBe('/api/sys/common/static/ai-platform/system/avatar/u1.jpg');
  });
});
