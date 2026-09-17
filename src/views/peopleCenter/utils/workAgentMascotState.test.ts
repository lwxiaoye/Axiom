import {
  latestAssistantMessage,
  selectWorkAgentMascotState,
  workAgentDiscoverySignature,
} from './workAgentMascotState';

describe('AXIOM Agent mascot state', () => {
  it('uses authoritative running steps instead of a random animation timer', () => {
    expect(selectWorkAgentMascotState({
      loading: true,
      message: { role: 'assistant', agentSteps: [{ kind: 'thinking', status: 'running' }] },
    })).toBe('thinking');

    expect(selectWorkAgentMascotState({
      loading: true,
      message: {
        role: 'assistant',
        agentSteps: [{ kind: 'tool', name: 'search_web', status: 'running' }],
      },
    })).toBe('searching');

    expect(selectWorkAgentMascotState({
      loading: true,
      message: {
        role: 'assistant',
        agentSteps: [{ kind: 'tool', name: 'bash', status: 'running' }],
      },
    })).toBe('working');
  });

  it('keeps waiting and error states honest', () => {
    expect(selectWorkAgentMascotState({ loading: false, waiting: true })).toBe('waiting');
    expect(selectWorkAgentMascotState({
      loading: false,
      message: { role: 'assistant', runFailed: true },
    })).toBe('error');
    expect(selectWorkAgentMascotState({ loading: false })).toBe('idle');
  });

  it('only emits a discovery signature after a real lookup completes', () => {
    expect(workAgentDiscoverySignature({
      runId: 'run-1',
      agentSteps: [{ kind: 'tool', name: 'search_web', status: 'running', callId: 'call-1' }],
    })).toBe('');
    expect(workAgentDiscoverySignature({
      runId: 'run-1',
      agentSteps: [{ kind: 'tool', name: 'search_web', status: 'completed', callId: 'call-1', pages: [{}] }],
    })).toBe('run-1:0:call-1:1');
    expect(workAgentDiscoverySignature({
      runId: 'run-1',
      agentSteps: [{ kind: 'tool', name: 'bash', status: 'completed', callId: 'call-2' }],
    })).toBe('');
  });

  it('selects the newest assistant message', () => {
    const assistant = { role: 'assistant', runId: 'new' };
    expect(latestAssistantMessage([
      { role: 'assistant', runId: 'old' },
      { role: 'user' },
      assistant,
    ])).toBe(assistant);
  });
});
