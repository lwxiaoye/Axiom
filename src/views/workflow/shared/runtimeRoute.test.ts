import {
  getAiAppDraftPreviewRoute,
  getAiAppRunHref,
  getAiAppRunRoute,
  openAgentRunWindow,
  resolveAgentRunHref,
} from './runtimeRoute';

describe('getAiAppRunRoute', () => {
  it('routes by app id', () => {
    expect(getAiAppRunRoute({ id: 'wf-1' })).toEqual({
      path: '/agent/run/wf-1',
    });
  });

  it('uses workflowAppId for legacy records without keeping the old workflow run page', () => {
    expect(getAiAppRunRoute({ workflowAppId: 'wf-2', appInfoId: 'app-2' })).toEqual({
      path: '/agent/run/wf-2',
    });
  });

  it('routes conversational agents to the standalone run page', () => {
    expect(getAiAppRunRoute({ id: 'ca-1', aiAppType: 'chatAgent' })).toEqual({
      path: '/agent/run/ca-1',
    });
  });

  it('routes workflow agents to the standalone run page too', () => {
    expect(getAiAppRunRoute({ id: 'wf-9', aiAppType: 'workflow' })).toEqual({
      path: '/agent/run/wf-9',
    });
  });

  it('prefers appId over version id for review records', () => {
    expect(getAiAppRunRoute({ id: 'version-1', appId: 'app-1', aiAppType: 'workflow' })).toEqual({
      path: '/agent/run/app-1',
    });
  });

  it('adds draft preview flag for unpublished editor previews', () => {
    expect(getAiAppDraftPreviewRoute('draft-1')).toEqual({
      path: '/agent/run/draft-1',
      query: { previewDraft: '1' },
    });
  });

  it('marks my-agent origin so the run page returns there', () => {
    expect(getAiAppRunRoute({ id: 'wf-1' }, 'my-agent')).toEqual({
      path: '/agent/run/wf-1',
      query: { from: 'my-agent' },
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

  it('opens a new tab to the run path', () => {
    const opened: Array<{ url: string; name: string }> = [];
    const fake = { opener: {} as Window | null };
    openAgentRunWindow(
      { id: 'wf-1' },
      (url, name) => {
        opened.push({ url, name });
        return fake as Window;
      },
    );
    expect(opened).toEqual([{ url: '/agent/run/wf-1', name: '_blank' }]);
    expect(fake.opener).toBeNull();
  });
});
