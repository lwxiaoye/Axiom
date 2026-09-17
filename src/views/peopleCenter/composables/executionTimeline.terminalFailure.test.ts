import { restoreExecutionTrace } from './executionTimeline';

describe('terminal error history', () => {
  it('restores the public reason independently of the failure anchor body', () => {
    const error = '联网检索暂时不可用，问题与检索记录已保留。';
    expect(restoreExecutionTrace({ status: 'failed', error })).toMatchObject({ runFailed: true, error });
  });

  it.each(['running', 'waiting_system', 'completed', 'partial', 'cancelled'])('does not carry a stale error into %s', (status) => {
    const restored = restoreExecutionTrace({ status, error: '旧失败' });
    expect(restored.error).toBeUndefined();
    expect(restored.runFailed).toBeUndefined();
  });
});
