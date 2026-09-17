import { createAgentChatCompletion } from './agentApi';

jest.mock('/@/utils/http/axios', () => ({ defHttp: {} }), { virtual: true });
jest.mock('./utils/agentAuthHeaders', () => ({
  resolveAgentAccessToken: () => 'test-token',
  agentAuthHeaders: (headers: Record<string, string>) => headers,
}));

afterEach(() => jest.restoreAllMocks());

it('projects recovery and resumed execution without manufacturing another start event', async () => {
  const stream = [
    ['run.phase.changed', { phase: 'waiting_system' }],
    ['run.recovery.scheduled', {}],
    ['message.reasoning.delta', { text: '继续' }],
    ['run.completed', {}],
  ].map(([type, data], index) => `data: ${JSON.stringify({
    schema_version: 1, type, data, sequence: index + 1, run_id: 'r',
  })}\n\n`).join('');
  jest.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify({ run_id: 'r', thread_id: 't' }), { status: 202 }))
    .mockResolvedValueOnce(new Response(stream));
  const onRunPhase = jest.fn();
  const onRunStarted = jest.fn();
  await createAgentChatCompletion({ message: 'test', stream: true, onRunPhase, onRunStarted });
  expect(onRunPhase.mock.calls.map(([phase]) => phase)).toEqual(['waiting_system', 'waiting_system', 'running']);
  expect(onRunStarted).toHaveBeenCalledTimes(1);
});

it.each([false, true])('HTTP acceptance stays created until a real start event (started=%s)', async (started) => {
  const events = started ? ['run.started', 'run.completed'] : ['run.accepted'];
  const stream = events.map((type, index) => `data: ${JSON.stringify({
    schema_version: 1, type, sequence: index + 1, run_id: 'r', data: { thread_id: 't' },
  })}\n\n`).join('');
  const fetchMock = jest.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify({ run_id: 'r', thread_id: 't' }), { status: 202 }))
    .mockResolvedValueOnce(new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }));
  const onRunStarted = jest.fn();
  const onStreamEnd = jest.fn();
  await createAgentChatCompletion({ message: 'test', stream: true, onRunStarted, onStreamEnd });
  expect(onRunStarted.mock.calls.map(([payload]) => payload.status)).toEqual(started ? ['created', 'running'] : ['created']);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(onStreamEnd).toHaveBeenCalledWith({ sawTerminal: started });
});
