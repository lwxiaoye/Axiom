import {
  getAiAppDraftPreviewRoute,
  getAiAppRunHref,
  openAgentRunWindow,
  resolveAgentRunHref,
} from './runtimeRoute';

describe('getAiAppDraftPreviewRoute', () => {
  it('adds draft preview flag for unpublished editor previews', () => {
    expect(getAiAppDraftPreviewRoute('draft-1')).toEqual({
      path: '/agent/run/draft-1',
      query: { previewDraft: '1' },
    });
  });
});

describe('resolveAgentRunHref / openAgentRunWindow', () => {
  const origin = 'http://localhost:3200';

  it('records resolve to the standalone run path', () => {
    expect(getAiAppRunHref({ id: '645c96acdae6470aa8438c67b680ed4a' }))
      .toBe('/agent/run/645c96acdae6470aa8438c67b680ed4a');
  });

  it('strips leftover query on same-origin run URLs', () => {
    expect(resolveAgentRunHref('/agent/run/abc?from=agent', origin)).toBe('/agent/run/abc');
    expect(resolveAgentRunHref(`${origin}/agent/run/abc?user=1&from=agent`, origin)).toBe('/agent/run/abc');
  });

  it('keeps external jump URLs intact', () => {
    expect(resolveAgentRunHref('https://ext.example/app?x=1', origin)).toBe('https://ext.example/app?x=1');
  });

  it('navigates in the same tab for same-origin run paths', () => {
    // 新标签页没有历史，运行页里的「返回」就无处可回——同源一律站内跳转。
    const opened: Array<{ url: string; name: string }> = [];
    const navigated: string[] = [];
    const ok = openAgentRunWindow(
      { id: 'wf-1' },
      (url, name) => {
        opened.push({ url, name });
        return {} as Window;
      },
      (path) => navigated.push(path),
    );
    expect(navigated).toEqual(['/agent/run/wf-1']);
    expect(opened).toEqual([]);
    expect(ok).toBe(true);
  });

  it('still opens external jump URLs in a new tab with the opener detached', () => {
    const opened: Array<{ url: string; name: string }> = [];
    const navigated: string[] = [];
    const fake = { opener: {} as Window | null };
    const ok = openAgentRunWindow(
      'https://ext.example/app?x=1',
      (url, name) => {
        opened.push({ url, name });
        return fake as unknown as Window;
      },
      (path) => navigated.push(path),
    );
    expect(opened).toEqual([{ url: 'https://ext.example/app?x=1', name: '_blank' }]);
    expect(navigated).toEqual([]);
    expect(fake.opener).toBeNull();
    expect(ok).toBe(true);
  });

  it('reports failure when there is nothing to open', () => {
    expect(openAgentRunWindow('', () => null, () => undefined)).toBe(false);
  });
});
