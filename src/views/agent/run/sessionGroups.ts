import type { RunSession } from './agentRun.api';

export type SessionGroup = {
  key: string;
  label: string;
  items: RunSession[];
};

function startOfDay(value: Date) {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
}

export function parseSessionTime(iso?: string | null): Date | null {
  if (!iso) return null;
  const parsed = new Date(String(iso).replace(' ', 'T'));
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** 与主对话历史同一套相对时间。 */
export function formatSessionTime(iso?: string | null): string {
  const d = parseSessionTime(iso);
  if (!d) return '';
  const now = new Date();
  const diffMin = Math.floor((now.getTime() - d.getTime()) / 60000);
  const hhmm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  if (diffMin < 1) return '刚刚';
  if (diffMin < 60) return `${diffMin} 分钟前`;
  const dayDiff = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
  if (dayDiff <= 0) return hhmm;
  if (dayDiff === 1) return `昨天 ${hhmm}`;
  if (dayDiff < 7) return `星期${'日一二三四五六'[d.getDay()]} ${hhmm}`;
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`;
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}

export function groupRunSessions(sessions: RunSession[], now = new Date()): SessionGroup[] {
  const pinned: RunSession[] = [];
  const today: RunSession[] = [];
  const yesterday: RunSession[] = [];
  const week: RunSession[] = [];
  const earlier: RunSession[] = [];
  for (const session of sessions) {
    if (session.pinned) {
      pinned.push(session);
      continue;
    }
    const d = parseSessionTime(session.updateTime || session.createTime);
    if (!d) {
      earlier.push(session);
      continue;
    }
    const dayDiff = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
    if (dayDiff <= 0) today.push(session);
    else if (dayDiff === 1) yesterday.push(session);
    else if (dayDiff < 7) week.push(session);
    else earlier.push(session);
  }
  const groups: SessionGroup[] = [];
  if (pinned.length) groups.push({ key: 'pinned', label: '置顶', items: pinned });
  if (today.length) groups.push({ key: 'today', label: '今天', items: today });
  if (yesterday.length) groups.push({ key: 'yesterday', label: '昨天', items: yesterday });
  if (week.length) groups.push({ key: 'week', label: '过去 7 天', items: week });
  if (earlier.length) groups.push({ key: 'earlier', label: '更早', items: earlier });
  return groups;
}
