/** 会话历史行上的活动态：只反映本地还活着的 Run，不把列表快照里已停止的 active_run 当成「回复中」。 */

export type ThreadActivityRun = {
  id?: string;
  status?: string;
  interactive_type?: string;
} | null;

export type ThreadConversationActivity = {
  kind: 'running' | 'paused';
  label: string;
};

export function threadConversationActivity(
  localRun: ThreadActivityRun | undefined,
  listedRun: ThreadActivityRun | undefined,
  isFinishedRun: (runId?: string) => boolean,
): ThreadConversationActivity | null {
  const listed = listedRun?.id && isFinishedRun(listedRun.id) ? null : listedRun;
  const run = localRun || listed;
  const status = run?.status || '';
  if (status === 'waiting_system') {
    return run?.interactive_type === 'user_pause'
      ? { kind: 'paused', label: '已暂停' }
      : { kind: 'paused', label: '待恢复' };
  }
  if (['created', 'running', 'routing'].includes(status)) {
    return { kind: 'running', label: '回复中' };
  }
  return null;
}
