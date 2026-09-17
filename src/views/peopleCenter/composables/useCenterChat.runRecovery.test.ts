import { effectScope, nextTick, ref } from 'vue';
import { useCenterChat } from './useCenterChat';
import * as api from '../agentApi';

jest.mock('../agentApi', () => ({
  getThreads: jest.fn().mockResolvedValue([]),
  getThreadMessages: jest.fn().mockResolvedValue([]),
  getThreadSettings: jest.fn().mockResolvedValue({ model: 'm' }),
  getThreadActiveRun: jest.fn(),
  getRunState: jest.fn(),
  getChatQueue: jest.fn().mockResolvedValue([]),
  subscribeChatRun: jest.fn(),
  addChatQueueItem: jest.fn().mockResolvedValue({ id: 'queued', content: '继续' }),
  createAgentChatCompletion: jest.fn(),
  cancelChatRun: jest.fn(),
}));
jest.mock('@/views/workflow/shared/runtimeRoute', () => ({ openAgentRunWindow: jest.fn() }), { virtual: true });
jest.mock('./chatDrafts', () => ({
  currentDraftUserId: jest.fn().mockResolvedValue('u'),
  listDraftRecords: jest.fn().mockResolvedValue([]),
  getDraftRecord: jest.fn().mockResolvedValue(null),
  deleteDraftRecord: jest.fn().mockResolvedValue(undefined),
  saveDraftRecord: jest.fn().mockResolvedValue(undefined),
  newAnonymousDraftId: jest.fn().mockReturnValue('new:draft'),
}));

describe('reopening a recovering research Run', () => {
  let scope: ReturnType<typeof effectScope>;
  let chat: ReturnType<typeof useCenterChat>;

  beforeEach(() => {
    jest.useFakeTimers();
    jest.clearAllMocks();
    (api.getThreadMessages as jest.Mock).mockResolvedValue([]);
    (globalThis as any).window = {
      setTimeout, clearTimeout, setInterval, clearInterval,
      addEventListener: jest.fn(), removeEventListener: jest.fn(),
    };
    (api.getThreadActiveRun as jest.Mock).mockResolvedValue({
      id: 'r', thread_id: 't', status: 'waiting_system', phase: 'waiting_system', agent_mode: 'research',
    });
    (api.getRunState as jest.Mock).mockResolvedValue({ status: 'waiting_system', event_cursor: 120 });
    (api.subscribeChatRun as jest.Mock).mockImplementation((_id, opts) => {
      return new Promise((_resolve, reject) => opts.signal.addEventListener('abort', () => {
        reject(Object.assign(new Error('observer detached'), { name: 'AbortError' }));
      }, { once: true }));
    });
    scope = effectScope();
    chat = scope.run(() => useCenterChat({
      activeSection: ref('chat'), appList: ref([{}]), reloadApps: jest.fn(),
      showError: jest.fn(), showNotice: jest.fn(),
    }))!;
  });

  afterEach(async () => {
    scope.stop();
    await nextTick();
    jest.clearAllTimers();
    jest.useRealTimers();
    delete (globalThis as any).window;
  });

  it('restores busy controls after reset and reopening from history', async () => {
    expect(await chat.loadThread('t')).toBe(true);
    expect(chat.researchProfile.value).toBe(true);
    expect(chat.chatLoading.value).toBe(true);
    expect(chat.chatTaskState.value).toBe('running');
    expect(chat.hasSuspendedRun()).toBe(false);
    expect(chat.chatMessages.value.some((item) => item.content.includes('等你补充'))).toBe(false);
    chat.resetChat();
    await nextTick();
    expect(chat.chatLoading.value).toBe(false);
    expect(api.cancelChatRun).not.toHaveBeenCalled();
    expect(await chat.loadThread('t')).toBe(true);
    expect(chat.chatLoading.value).toBe(true);
    expect(chat.researchProfile.value).toBe(true);
  });

  it('queues follow-up text while the same Run recovers', async () => {
    await chat.loadThread('t');
    chat.chatInput.value = '继续';
    await chat.sendChat();
    expect(api.addChatQueueItem).toHaveBeenCalled();
    expect(api.createAgentChatCompletion).not.toHaveBeenCalled();
    expect(api.cancelChatRun).not.toHaveBeenCalled();
  });

  it('continues observing after an EOF during automatic recovery', async () => {
    (api.subscribeChatRun as jest.Mock).mockImplementationOnce(async (_id, opts) => {
      opts.onStreamEnd({ sawTerminal: false });
      return '';
    });
    await chat.loadThread('t');
    await jest.advanceTimersByTimeAsync(1500);
    expect(api.subscribeChatRun).toHaveBeenCalledTimes(2);
    expect(chat.chatLoading.value).toBe(true);
    expect(chat.hasSuspendedRun()).toBe(false);
  });

  it('restores a queued Run without claiming that execution or thinking has started', async () => {
    (api.getThreadActiveRun as jest.Mock).mockResolvedValue({ id: 'r', status: 'created', agent_mode: 'research' });
    (api.getRunState as jest.Mock).mockResolvedValue({ status: 'created', event_cursor: 1 });
    await chat.loadThread('t');
    const reply = chat.chatMessages.value.find((item) => item.role === 'assistant' && item.runId === 'r')!;
    expect(reply.runStatus).toBe('created');
    expect(chat.chatLoading.value).toBe(true);
    expect(reply.agentSteps?.some((step) => step.kind === 'thinking')).not.toBe(true);
    const callbacks = (api.subscribeChatRun as jest.Mock).mock.calls[0][1];
    callbacks.onRunStarted({ run_id: 'r', thread_id: 't', status: 'running', agent_mode: 'research' });
    expect(reply.runStatus).toBe('running');
    expect(chat.chatLoading.value).toBe(true);
  });

  it.each(['waiting_user', 'waiting_confirmation'])('keeps %s input cards usable', async (status) => {
    (api.getThreadActiveRun as jest.Mock).mockResolvedValue({ id: 'r', status, agent_mode: 'research' });
    await chat.loadThread('t');
    expect(chat.chatLoading.value).toBe(false);
  });

  it.each([true, false])('reopens a failed Run with its real reason (trace field=%s)', async (hasError) => {
    const error = '联网检索暂时不可用，本轮没有取得可核验来源。';
    (api.getThreadMessages as jest.Mock).mockResolvedValue([{
      id: 42, role: 'assistant', run_id: 'r', content: '（任务执行失败，未生成回复）',
      execution_trace: { status: 'failed', agent_mode: 'research', ...(hasError ? { error } : {}) },
    }]);
    (api.getThreadActiveRun as jest.Mock).mockResolvedValue(null);
    (api.getRunState as jest.Mock).mockResolvedValue({ status: 'failed', error });
    await chat.loadThread('t');
    await nextTick();
    const reply = chat.chatMessages.value.find((message) => message.runId === 'r')!;
    expect(reply.error).toBe(error);
    expect(reply.content).toBe('');
    expect(chat.chatLoading.value).toBe(false);
    expect(api.subscribeChatRun).not.toHaveBeenCalled();
    expect(api.cancelChatRun).not.toHaveBeenCalled();
    expect(api.createAgentChatCompletion).not.toHaveBeenCalled();
  });

  it('ignores a slow historical failure response after leaving that conversation', async () => {
    let complete: (state: unknown) => void = () => undefined;
    (api.getThreadMessages as jest.Mock).mockResolvedValue([{
      id: 42, role: 'assistant', run_id: 'r', content: '（任务执行失败，未生成回复）',
      execution_trace: { status: 'failed' },
    }]);
    (api.getThreadActiveRun as jest.Mock).mockResolvedValue(null);
    (api.getRunState as jest.Mock).mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
    await chat.loadThread('t');
    chat.resetChat();
    complete({ status: 'failed', error: '不属于当前页面的错误' });
    await nextTick();
    expect(chat.chatMessages.value).toEqual([]);
    expect(api.cancelChatRun).not.toHaveBeenCalled();
  });
});
