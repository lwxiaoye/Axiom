import { formatSessionTime, groupRunSessions } from './sessionGroups';
import type { RunSession } from './runSession';

function session(partial: Partial<RunSession> & Pick<RunSession, 'id'>): RunSession {
  return { appId: 'app', title: partial.title || partial.id, ...partial };
}

test('置顶组优先，其余按今天/昨天/更早分组', () => {
  const now = new Date('2026-08-24T15:00:00');
  const groups = groupRunSessions(
    [
      session({ id: 'old', updateTime: '2026-07-01T10:00:00', title: '更早' }),
      session({ id: 'pin', updateTime: '2026-07-01T10:00:00', title: '钉住', pinned: true }),
      session({ id: 'today', updateTime: '2026-08-24T12:00:00', title: '今天' }),
      session({ id: 'yday', updateTime: '2026-08-23T12:00:00', title: '昨天' }),
    ],
    now,
  );
  expect(groups.map((group) => group.key)).toEqual(['pinned', 'today', 'yesterday', 'earlier']);
  expect(groups[0].items.map((item) => item.id)).toEqual(['pin']);
});

test('相对时间：分钟前 / 今天时刻', () => {
  const now = Date.now();
  const minutesAgo = new Date(now - 23 * 60 * 1000).toISOString();
  expect(formatSessionTime(minutesAgo)).toBe('23 分钟前');
});
