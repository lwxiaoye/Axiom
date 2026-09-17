import { applyResearchProgress, type ExecutionMessage } from '../composables/executionTimeline';
import { memberContribution, memberStatus, mergeResearchTeam, parseResearchTeam, researchActivities } from './researchTeam';

const team = { id: 't1', version: 2, stage: 'researching', startedAt: '2026-09-07T01:00:00Z', members: [
  { id: 'm1', role: 'researcher', name: '资料研究员', status: 'researching', searches: [], checkpoint: 'secret' },
] };

test('search result cards preserve per-search sources and reject unsafe links', () => {
  const snapshot = parseResearchTeam({ ...team, activity: [
    { id: 's1', memberId: 'm1', kind: 'search', query: 'query', status: 'succeeded', count: 3,
      results: [{ url: 'https://example.com/doc', title: 'Source', snippet: 'Summary', secret: 'hidden' },
        { url: 'javascript:alert(1)' }, { url: 'https://user:password@example.com/' }] },
    { id: 's2', memberId: 'm1', kind: 'search', query: 'old', status: 'succeeded', count: 1 },
  ] })!;
  expect(snapshot.activity![0].results).toEqual([{ url: 'https://example.com/doc', title: 'Source', snippet: 'Summary' }]);
  expect(snapshot.activity![1].results).toBeUndefined();
});

test('snapshots use an allowlist and reject malformed identities', () => {
  expect(JSON.stringify(parseResearchTeam(team))).not.toContain('secret');
  expect(parseResearchTeam({ ...team, members: [{ id: 'm1', role: 'marketplace-app' }] })).toBeUndefined();
  expect(parseResearchTeam({ ...team, version: -1 })).toBeUndefined();
  expect(parseResearchTeam({
    ...team,
    members: [{ ...team.members[0], searches: [{ id: 's0', query: '', status: 'running', count: 0 }] }],
  })?.members[0].searches).toEqual([]);
});

test('search diagnostics survive replay without turning strings into cache flags', () => {
  const snapshot = parseResearchTeam({ ...team, activity: [
    { id: 'failed', memberId: 'm1', kind: 'search', status: 'failed', operation: 'read',
      errorCode: 'timeout', error: '来源访问超时', cacheHit: 'true', privatePayload: 'secret' },
    { id: 'cached', memberId: 'm1', kind: 'search', status: 'succeeded', count: 2,
      cacheHit: true, snippetOnly: true },
  ] })!;
  expect(snapshot.activity![0]).toMatchObject({ errorCode: 'timeout', operation: 'read', cacheHit: false });
  expect(snapshot.activity![1]).toMatchObject({ cacheHit: true, snippetOnly: true });
  expect(JSON.stringify(snapshot)).not.toContain('secret');
});

test('duplicates and late snapshots never rewind member progress', () => {
  const current = parseResearchTeam(team)!;
  expect(mergeResearchTeam(current, { ...team, version: 1 })).toBe(current);
  expect(mergeResearchTeam(current, team)).toBe(current);
  expect(mergeResearchTeam(current, { ...team, version: 3 })?.version).toBe(3);
});

test('team-only events preserve coverage and coverage events preserve team', () => {
  const message: ExecutionMessage = {};
  applyResearchProgress(message, { topic: '背景', topicIndex: 2, topicTotal: 4, searchCalls: 5 });
  applyResearchProgress(message, { team: parseResearchTeam(team) });
  expect(message.researchProgress).toMatchObject({ topic: '背景', topicIndex: 2, topicTotal: 4, searchCalls: 5 });
  applyResearchProgress(message, { searchCalls: 6 });
  expect(message.researchProgress?.team?.id).toBe('t1');
});

test('terminal parent does not leave child running or invent success', () => {
  expect(memberStatus('researching', true)).toBe('已结束');
  expect(memberStatus('reviewing', true)).toBe('已结束');
  expect(memberStatus('failed', true)).toBe('未完成');
  expect(memberStatus('completed', true)).toBe('已提交研究材料');
});

test('partial member keeps its saved-material note through history replay', () => {
  const snapshot = parseResearchTeam({ ...team, stage: 'synthesizing', members: [
    { ...team.members[0], status: 'partial', note: '已有材料尚待核验', draftMaterials: 'private' },
  ] })!;
  expect(snapshot.members[0]).toMatchObject({ status: 'partial', note: '已有材料尚待核验' });
  expect(memberStatus(snapshot.members[0].status, true)).toBe('已结束');
  expect(memberContribution(snapshot.members[0])).toBe('已有材料尚待核验');
  expect(JSON.stringify(snapshot)).not.toContain('private');
});

test('old partial handoff shows distinct source facts without grading or inventing review', () => {
  const snapshot = parseResearchTeam({ ...team, members: [{
    ...team.members[0], status: 'partial', findings: '真实的公开发现',
    note: '部分完成：已有材料和来源已交由主智能体继续核验，未完成部分将在报告中说明。',
    searches: [
      { id: 's1', query: '官方来源', status: 'succeeded', count: 2, results: [
        { url: 'https://www.example.com/doc/?utm_source=search#intro' },
        { url: 'https://example.org/other' },
      ] },
      { id: 's2', query: '阅读来源', status: 'succeeded', count: 1, results: [{ url: 'https://example.com/doc' }] },
      { id: 's3', query: '失败请求', status: 'failed', count: 8 },
    ],
  }] })!;
  const before = JSON.stringify(snapshot);
  expect(memberContribution(snapshot.members[0])).toBe('已找到 2 个网页；已提交研究材料。');
  expect(JSON.stringify(snapshot)).toBe(before);
  expect(snapshot.members[0].status).toBe('partial');
});

test.each([undefined, [{ url: 'https://example.com/doc' }]])('missing or bounded result lists do not invent a distinct total', results => {
  const snapshot = parseResearchTeam({ ...team, members: [{
    ...team.members[0], status: 'partial', searches: [{ id: 's1', query: '来源', status: 'succeeded', count: 8, results }],
  }] })!;
  expect(memberContribution(snapshot.members[0])).toBe('已找到相关网页。');
});

test('saved material with no source keeps the concrete limitation', () => {
  const snapshot = parseResearchTeam({ ...team, members: [{
    ...team.members[0], status: 'partial',
    note: '部分完成：已保存材料尚待来源核验，不能视为已查证结论。',
    searches: [{ id: 's1', query: '来源', status: 'succeeded', count: 0 }],
  }] })!;
  expect(memberContribution(snapshot.members[0])).toBe('已保存研究材料；尚无可核验来源。');
});

test('ordered conversation retains repeat speakers and excludes unknown owners and private fields', () => {
  const parsed = parseResearchTeam({ ...team, leader: { id: 'lead', name: '主智能体', role: 'leader', checkpoint: 'private' }, activity: [
    { id: 'a', kind: 'message', memberId: 'm1', text: '先查原始资料', checkpoint: 'private' },
    { id: 'b', kind: 'search', memberId: 'lead', query: 'SQLite', count: 5 },
    { id: 'c', kind: 'message', memberId: 'm1', text: '我找到了一处差异' },
    { id: 'd', kind: 'message', memberId: 'unknown', text: '不能混入' },
  ] })!;
  expect(researchActivities(parsed).map(item => item.id)).toEqual(['a', 'b', 'c']);
  expect(JSON.stringify(parsed)).not.toContain('private');
});
