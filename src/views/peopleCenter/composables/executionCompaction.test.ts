import { applyCompaction, restoreExecutionTrace, type ExecutionMessage } from './executionTimeline';

function target(over: Partial<ExecutionMessage> = {}): ExecutionMessage {
  return { agentSteps: [], ...over };
}

describe('applyCompaction：Codex ContextCompaction 过程', () => {
  it('started 插入 running 行，重复 started 不叠行', () => {
    const message = target();
    applyCompaction(message, { status: 'started' });
    applyCompaction(message, { status: 'started' });
    expect(message.agentSteps).toHaveLength(1);
    expect(message.agentSteps?.[0]).toMatchObject({ kind: 'compaction', status: 'running' });
    expect(message.executionCollapsed).toBe(false);
  });

  it('completed 把同一行收成 Context compacted，并记下耗时', () => {
    const message = target();
    applyCompaction(message, { status: 'started' });
    applyCompaction(message, { status: 'completed', seconds: 12 });
    expect(message.agentSteps).toEqual([
      expect.objectContaining({ kind: 'compaction', status: 'completed', seconds: 12 }),
    ]);
  });

  it('failed 不伪装成 compacted', () => {
    const message = target();
    applyCompaction(message, { status: 'started' });
    applyCompaction(message, { status: 'failed', seconds: 2 });
    expect(message.agentSteps?.[0]).toMatchObject({ kind: 'compaction', status: 'failed', seconds: 2 });
  });

  it('迟到的 compacted 在没有 started 时仍补一行完成态', () => {
    const message = target();
    applyCompaction(message, { status: 'completed', seconds: 1 });
    expect(message.agentSteps).toEqual([
      expect.objectContaining({ kind: 'compaction', status: 'completed', seconds: 1 }),
    ]);
  });

  it('历史回放把 compaction 行还原为完成态', () => {
    const restored = restoreExecutionTrace({
      steps: [{ kind: 'compaction', status: 'running', seconds: 8, planKey: 'plan-x' }],
    } as any);
    expect(restored.agentSteps).toEqual([
      { kind: 'compaction', status: 'completed', seconds: 8, planKey: 'plan-x' },
    ]);
  });
});
