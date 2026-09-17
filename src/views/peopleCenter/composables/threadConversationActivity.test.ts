import { threadConversationActivity } from './threadConversationActivity';

const finished = (id?: string) => id === 'done-1';

describe('threadConversationActivity', () => {
  it('uses the live local run even if the list snapshot is stale', () => {
    expect(threadConversationActivity(
      { id: 'run-2', status: 'waiting_system', interactive_type: 'user_pause' },
      { id: 'run-1', status: 'running' },
      finished,
    )).toEqual({ kind: 'paused', label: '已暂停' });
  });

  it('does not keep 回复中 from a list snapshot after the run was finished locally', () => {
    expect(threadConversationActivity(
      undefined,
      { id: 'done-1', status: 'running' },
      finished,
    )).toBeNull();
  });

  it('still shows 回复中 for a list snapshot that has not been finished', () => {
    expect(threadConversationActivity(
      undefined,
      { id: 'run-9', status: 'running' },
      finished,
    )).toEqual({ kind: 'running', label: '回复中' });
  });
});
