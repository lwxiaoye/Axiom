import { effectScope, nextTick, ref } from 'vue';
import { useCenterChat } from './useCenterChat';
import * as api from '../agentApi';
import * as drafts from './chatDrafts';

jest.mock('../agentApi', () => ({
  getThreads: jest.fn().mockResolvedValue([]),
  getThreadMessages: jest.fn().mockResolvedValue([]),
  getThreadSettings: jest.fn().mockResolvedValue({ model: 'm' }),
  getThreadActiveRun: jest.fn().mockResolvedValue(null),
  getRunState: jest.fn(),
  getChatQueue: jest.fn().mockResolvedValue([]),
  subscribeChatRun: jest.fn(),
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
  newAnonymousDraftId: jest.fn().mockReturnValue('new:interview-draft'),
  isListableAnonymousDraft: jest.fn().mockReturnValue(true),
}));

describe('interview controls and answer drafts', () => {
  let scope: ReturnType<typeof effectScope>;
  let chat: ReturnType<typeof useCenterChat>;
  const draft = '尚未发送的回答：我负责接口设计和回归测试。';

  beforeEach(async () => {
    jest.useFakeTimers();
    jest.clearAllMocks();
    (globalThis as any).window = {
      setTimeout, clearTimeout, setInterval, clearInterval,
      addEventListener: jest.fn(), removeEventListener: jest.fn(),
    };
    (api.createAgentChatCompletion as jest.Mock).mockImplementation(async (params) => {
      params.onRunStarted({ run_id: 'r1', thread_id: 't1', status: 'running' });
      params.onRunCompleted({});
      params.onStreamEnd({ sawTerminal: true });
      return '';
    });
    scope = effectScope();
    chat = scope.run(() => useCenterChat({
      activeSection: ref('chat'), appList: ref([{}]), reloadApps: jest.fn(),
      showError: jest.fn(), showNotice: jest.fn(),
      fixedAssistantPreset: 'interview', threadScope: 'interview',
    }))!;
    chat.currentThreadId.value = 't1';
    await nextTick();
    chat.chatInput.value = draft;
    await nextTick();
  });

  afterEach(async () => {
    scope.stop();
    await nextTick();
    jest.clearAllTimers();
    jest.useRealTimers();
    delete (globalThis as any).window;
  });

  it.each(['pause', 'resume', 'hint', 'finish', 'retry'] as const)('%s sends only the control and preserves the saved draft', async (action) => {
    const input = { action, expected_version: 1, question_id: 'q1' };
    await chat.sendInterviewMessage('面试控制操作', input);
    expect(api.createAgentChatCompletion).toHaveBeenCalledTimes(1);
    expect(api.createAgentChatCompletion).toHaveBeenCalledWith(expect.objectContaining({
      message: '面试控制操作', interview_input: input, assistant_preset: 'interview',
    }));
    expect(chat.chatInput.value).toBe(draft);
    expect(drafts.deleteDraftRecord).not.toHaveBeenCalled();
    await jest.advanceTimersByTimeAsync(500);
    expect(drafts.saveDraftRecord).toHaveBeenCalledWith(expect.objectContaining({ content: draft }));
  });

  it.each(['finish', 'retry'] as const)('a rejected %s keeps the original answer instead of restoring control text', async (action) => {
    (api.createAgentChatCompletion as jest.Mock).mockRejectedValue(Object.assign(new Error('面试版本已变化'), { status: 409 }));
    await chat.sendInterviewMessage('面试控制操作', { action, expected_version: 1, question_id: 'q1' });
    expect(api.createAgentChatCompletion).toHaveBeenCalledTimes(1);
    expect(chat.chatInput.value).toBe(draft);
    expect(drafts.deleteDraftRecord).not.toHaveBeenCalled();
  });

  it('still consumes an explicitly submitted answer', async () => {
    await chat.sendInterviewMessage(draft, { action: 'answer', expected_version: 1, question_id: 'q1' });
    expect(api.createAgentChatCompletion).toHaveBeenCalledWith(expect.objectContaining({ message: draft }));
    expect(chat.chatInput.value).toBe('');
    expect(drafts.deleteDraftRecord).toHaveBeenCalled();
  });
});
