import {
  findActiveRunReplayTarget,
  findActiveRunTraceSnapshot,
  type ActiveRunHistoryMessage,
} from './activeRunHistory';

describe('findActiveRunReplayTarget', () => {
  it('returns the persisted assistant anchor without changing its restored projection', () => {
    const messages: Array<ActiveRunHistoryMessage & Record<string, unknown>> = [
      { id: 1, role: 'user', content: '改成蓝金色' },
      {
        id: 2,
        dbId: 8072,
        role: 'assistant',
        content: '已经改好了',
        runId: 'run-active',
        agentMode: 'standard',
        feedback: 'up',
        runCompletedAt: 123,
        agentSteps: [{ kind: 'tool', status: 'completed' }],
        generatedFiles: [{ id: 'file-1' }],
      },
    ];

    expect(findActiveRunReplayTarget(messages, 'run-active')).toEqual({
      messageId: 2,
      content: '已经改好了',
    });
    expect(messages[1]).toMatchObject({
      content: '已经改好了',
      runCompletedAt: 123,
      generatedFiles: [{ id: 'file-1' }],
    });
  });

  it('targets only the latest assistant message owned by the active run', () => {
    const messages: Array<ActiveRunHistoryMessage & Record<string, unknown>> = [
      { id: 1, role: 'assistant', content: '旧段', runId: 'run-active', agentSteps: ['old'] },
      { id: 2, role: 'user', content: '追加要求' },
      { id: 3, role: 'assistant', content: '最新段', runId: 'run-active', agentSteps: ['latest'] },
    ];

    expect(findActiveRunReplayTarget(messages, 'run-active')).toEqual({
      messageId: 3,
      content: '最新段',
    });
    expect(messages[0]).toMatchObject({ id: 1, content: '旧段', agentSteps: ['old'] });
    expect(messages[2]).toMatchObject({ id: 3, content: '最新段', agentSteps: ['latest'] });
  });

  it('does nothing when history has no assistant anchor for the active run', () => {
    const messages: ActiveRunHistoryMessage[] = [
      { id: 1, role: 'assistant', content: '别的轮次', runId: 'run-finished' },
    ];

    expect(findActiveRunReplayTarget(messages, 'run-active')).toBeUndefined();
    expect(messages[0].content).toBe('别的轮次');
  });
});

describe('findActiveRunTraceSnapshot', () => {
  it('recovers the active execution snapshot carried by the durable input message', () => {
    const trace = {
      status: 'running',
      preamble: '正在搭建页面骨架',
      steps: [{ kind: 'tool', status: 'completed', name: 'use_skill' }],
    };
    const messages = [
      { run_id: 'run-active', execution_trace: trace },
      { run_id: 'run-finished', execution_trace: { status: 'completed' } },
    ];

    expect(findActiveRunTraceSnapshot(messages, 'run-active')).toBe(trace);
  });

  it('does not borrow another Run snapshot when the active Run has no trace yet', () => {
    const messages = [
      { run_id: 'run-finished', execution_trace: { status: 'completed' } },
      { run_id: 'run-active', execution_trace: null },
    ];

    expect(findActiveRunTraceSnapshot(messages, 'run-active')).toBeUndefined();
  });
});
