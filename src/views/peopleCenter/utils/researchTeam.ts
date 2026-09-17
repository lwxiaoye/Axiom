export type ResearchRole = 'researcher' | 'analyst' | 'verifier';
export type ResearchSearchResult = { title: string; url: string; snippet: string };
export type ResearchMember = {
  id: string;
  role: ResearchRole;
  name: string;
  task: string;
  status: string;
  findings?: string;
  review?: string;
  error?: string;
  note?: string;
  searches: { id: string; query: string; status: string; count: number; error?: string; results?: ResearchSearchResult[] }[];
};
export type ResearchTeamSnapshot = {
  id: string;
  version: number;
  stage: string;
  startedAt: string;
  updatedAt: string;
  members: ResearchMember[];
  leader?: Omit<ResearchMember, 'role' | 'searches'> & { role: 'leader' };
  activity?: ResearchActivity[];
};
export type ResearchActivity = {
  id: string; memberId: string; kind: 'search' | 'message'; at: string;
  text: string; query: string; status: string; count: number;
  results?: ResearchSearchResult[];
  errorCode?: string; error?: string; operation?: string; cacheHit?: boolean; snippetOnly?: boolean;
};

const roles = new Set(['researcher', 'analyst', 'verifier']);
const text = (value: unknown, limit = 6000) => typeof value === 'string' ? value.slice(0, limit) : '';
const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object'
  ? value as Record<string, unknown> : {};
function searchResults(value: unknown): ResearchSearchResult[] | undefined {
  if (!Array.isArray(value)) return;
  return value.slice(0, 30).flatMap(value => {
    const row = record(value);
    try {
      const url = new URL(text(row.url, 2000));
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return [];
      return [{ url: url.href, title: text(row.title, 300) || url.hostname, snippet: text(row.snippet, 280) }];
    } catch { return []; }
  });
}

export function parseResearchTeam(value: unknown): ResearchTeamSnapshot | undefined {
  const raw = record(value);
  if (!text(raw.id) || !Number.isSafeInteger(raw.version) || Number(raw.version) < 0 || !Array.isArray(raw.members)) return;
  const ids = new Set<string>();
  const members: ResearchMember[] = [];
  for (const value of raw.members.slice(0, 3)) {
    const member = record(value);
    const id = text(member.id, 160);
    if (!id || ids.has(id) || !roles.has(String(member.role))) continue;
    ids.add(id);
    members.push({
      id, role: member.role as ResearchRole, name: text(member.name, 40), task: text(member.task, 500),
      status: text(member.status, 30), findings: text(member.findings), review: text(member.review),
      error: text(member.error, 300), note: text(member.note, 300),
      searches: (Array.isArray(member.searches) ? member.searches : []).slice(-6).flatMap((value) => {
        const search = record(value);
        const query = text(search.query, 400);
        if (!query) return [];
        return [{
          id: text(search.id, 200), query, status: text(search.status, 30),
          count: Math.max(0, Math.min(10000, Number(search.count) || 0)), error: text(search.error, 300),
          results: searchResults(search.results),
        }];
      }),
    });
  }
  if (!members.length) return;
  const lead = record(raw.leader);
  const leader: ResearchTeamSnapshot['leader'] = text(lead.id) && lead.role === 'leader' ? {
    id: text(lead.id, 160), role: 'leader', name: text(lead.name, 40), task: text(lead.task, 500),
    status: text(lead.status, 30), findings: text(lead.findings), error: text(lead.error, 300), note: text(lead.note, 300),
  } : undefined;
  const owners = new Set([...ids, ...(leader ? [leader.id] : [])]);
  const activityIds = new Set<string>();
  const activity: ResearchActivity[] = (Array.isArray(raw.activity) ? raw.activity : []).slice(-100).flatMap(value => {
    const item = record(value);
    const id = text(item.id, 200);
    const memberId = text(item.memberId, 160);
    if (!id || activityIds.has(id) || !owners.has(memberId) || !['search', 'message'].includes(String(item.kind))) return [];
    activityIds.add(id);
    return [{ id, memberId, kind: item.kind as ResearchActivity['kind'], at: text(item.at, 40),
      text: text(item.text), query: text(item.query, 400), status: text(item.status, 30),
      count: Math.max(0, Math.min(10000, Number(item.count) || 0)), results: searchResults(item.results),
      errorCode: text(item.errorCode, 60), error: text(item.error, 300), operation: text(item.operation, 20),
      cacheHit: item.cacheHit === true, snippetOnly: item.snippetOnly === true }];
  });
  return { id: text(raw.id, 160), version: Number(raw.version), stage: text(raw.stage, 30),
    startedAt: text(raw.startedAt, 40), updatedAt: text(raw.updatedAt, 40), members, leader,
    activity: Array.isArray(raw.activity) ? activity : undefined };
}

export function researchActivities(team: ResearchTeamSnapshot): ResearchActivity[] {
  if (team.activity) return team.activity;
  // Existing saved runs predate the ordered feed; keep their real receipts readable.
  return team.members.flatMap(member => [
    ...member.searches.map(search => ({ ...search, memberId: member.id, kind: 'search' as const, at: '', text: '' })),
    ...(['findings', 'review'] as const).flatMap(phase => member[phase] ? [{
      id: `${member.id}:${phase}`, memberId: member.id, kind: 'message' as const,
      at: '', text: member[phase]!, query: '', status: '', count: 0,
    }] : []),
  ]);
}

export function mergeResearchTeam(previous: ResearchTeamSnapshot | undefined, incoming: unknown) {
  const next = parseResearchTeam(incoming);
  if (!next) return previous;
  if (previous?.id === next.id && previous.version >= next.version) return previous;
  if (previous && previous.id !== next.id && next.startedAt < previous.startedAt) return previous;
  return next;
}

export function memberStatus(status: string, settled: boolean) {
  if (status === 'partial') return '已结束';
  if (status === 'completed') return '已提交研究材料';
  if (status === 'failed') return '未完成';
  if (settled) return status === 'waiting' ? '已提交发现' : '已结束';
  return ({ pending: '等待开始', researching: '正在研究', waiting: '等待互审', reviewing: '正在互审' } as Record<string, string>)[status] || '等待更新';
}

const legacySourceNote = '部分完成：已有材料和来源已交由主智能体继续核验，未完成部分将在报告中说明。';
const legacyMaterialNote = '部分完成：已保存材料尚待来源核验，不能视为已查证结论。';

export function memberContribution(member: ResearchMember): string {
  const note = member.note?.trim();
  if (note && note !== legacySourceNote && note !== legacyMaterialNote) return note;
  // Saved public snapshots cannot reveal private materials or invent missing counts.
  const searches = member.searches.filter(search => search.status === 'succeeded' && search.count > 0);
  const parts: string[] = [];
  if (searches.length) {
    if (searches.every(search => search.results?.length === search.count)) {
      const urls = new Set(searches.flatMap(search => search.results!.map(result => {
        const url = new URL(result.url);
        url.hash = '';
        url.hostname = url.hostname.replace(/^www\./, '');
        url.pathname = url.pathname.replace(/\/+$/, '') || '/';
        for (const key of [...url.searchParams.keys()]) {
          if (/^(utm_|spm)/i.test(key) || /^(fbclid|gclid|yclid)$/i.test(key)) url.searchParams.delete(key);
        }
        return url.href;
      })));
      parts.push(`已找到 ${urls.size} 个网页`);
    } else {
      parts.push('已找到相关网页');
    }
  }
  if (member.findings?.trim() || member.review?.trim()) parts.push('已提交研究材料');
  else if (note === legacyMaterialNote) parts.push('已保存研究材料');
  if (!searches.length) parts.push('尚无可核验来源');
  return `${parts.join('；')}。`;
}
