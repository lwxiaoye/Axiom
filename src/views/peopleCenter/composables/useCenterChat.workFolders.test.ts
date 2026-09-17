import { effectScope, nextTick, ref } from 'vue';
import { useCenterChat } from './useCenterChat';
import * as api from '../agentApi';
import * as drafts from './chatDrafts';

jest.mock('../agentApi', () => ({
  getThreads: jest.fn().mockResolvedValue([]),
  getThreadMessages: jest.fn().mockResolvedValue([]),
  getThreadSettings: jest.fn(),
  getThreadActiveRun: jest.fn().mockResolvedValue(null),
  getRunState: jest.fn(),
  getChatQueue: jest.fn().mockResolvedValue([]),
  subscribeChatRun: jest.fn(),
  createAgentChatCompletion: jest.fn().mockResolvedValue(''),
  cancelChatRun: jest.fn(),
}));
jest.mock('@/views/workflow/shared/runtimeRoute', () => ({ openAgentRunWindow: jest.fn() }), { virtual: true });
jest.mock('./chatDrafts', () => ({
  currentDraftUserId: jest.fn().mockResolvedValue('u'),
  listDraftRecords: jest.fn().mockResolvedValue([]),
  getDraftRecord: jest.fn().mockResolvedValue(null),
  deleteDraftRecord: jest.fn().mockResolvedValue(undefined),
  saveDraftRecord: jest.fn().mockResolvedValue(undefined),
  newAnonymousDraftId: jest.fn().mockReturnValue('new:workspace-draft'),
  isListableAnonymousDraft: jest.fn().mockReturnValue(true),
}));

describe('main chat working folder', () => {
  let scope: ReturnType<typeof effectScope>;
  let chat: ReturnType<typeof useCenterChat>;

  beforeEach(() => {
    jest.useFakeTimers();
    jest.clearAllMocks();
    (api.getThreadSettings as jest.Mock).mockResolvedValue({ model: 'm', workspace_folder: { id: 'a', name: '工作 A' } });
    (api.getThreadActiveRun as jest.Mock).mockResolvedValue(null);
    (api.createAgentChatCompletion as jest.Mock).mockImplementation(async (params) => {
      params.onRunStarted({ run_id: 'r-new', thread_id: 't-new', status: 'running' });
      params.onRunCompleted({});
      params.onStreamEnd({ sawTerminal: true });
      return '';
    });
    (globalThis as any).window = { setTimeout, clearTimeout, setInterval, clearInterval, addEventListener: jest.fn(), removeEventListener: jest.fn() };
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

  it('keeps unsent text while selecting a folder and persists its draft', async () => {
    chat.chatInput.value = '按文件夹里的素材写报告';
    chat.selectWorkFolder({ id: 'a', name: '工作 A' });
    expect(chat.chatInput.value).toBe('按文件夹里的素材写报告');
    await nextTick();
    await jest.advanceTimersByTimeAsync(500);
    expect(drafts.saveDraftRecord).toHaveBeenCalledWith(expect.objectContaining({ workspaceFolder: { id: 'a', name: '工作 A' } }));
  });

  it('restores the authoritative folder and opens a new conversation on switch', async () => {
    expect(await chat.loadThread('t1')).toBe(true);
    expect(chat.selectedWorkFolder.value?.id).toBe('a');
    chat.selectWorkFolder({ id: 'a', name: '工作 A' });
    expect(chat.currentThreadId.value).toBe('t1');
    chat.selectWorkFolder({ id: 'b', name: '工作 B' });
    expect(chat.currentThreadId.value).toBe('');
    expect(chat.selectedWorkFolder.value?.id).toBe('b');
    expect(api.cancelChatRun).not.toHaveBeenCalled();
    expect(await chat.loadThread('t1')).toBe(true);
    expect(chat.selectedWorkFolder.value?.id).toBe('a');
  });

  it('keeps the folder fixed while an upload or restoration is pending', () => {
    chat.selectWorkFolder({ id: 'a', name: '工作 A' });
    chat.uploadingFile.value = true;
    chat.selectWorkFolder({ id: 'b', name: '工作 B' });
    expect(chat.selectedWorkFolder.value?.id).toBe('a');
    chat.uploadingFile.value = false;
    chat.restoringLatestThread.value = true;
    chat.selectWorkFolder(null);
    expect(chat.selectedWorkFolder.value?.id).toBe('a');
  });

  it('sends the selected folder with the accepted Run request', async () => {
    chat.selectWorkFolder({ id: 'a', name: '工作 A' });
    chat.chatInput.value = '读取 brief.txt';
    await chat.sendChat();
    expect(api.createAgentChatCompletion).toHaveBeenCalledWith(
      expect.objectContaining({ workspace_folder_id: 'a' }),
    );
  });

  it('waits for folder materials to finish uploading before accepting a Run', async () => {
    chat.selectWorkFolder({ id: 'a', name: '工作 A' });
    chat.chatInput.value = '读取刚上传的文件';
    chat.uploadingWorkFolder.value = true;
    await chat.sendChat();
    expect(api.createAgentChatCompletion).not.toHaveBeenCalled();
    expect(chat.chatInput.value).toBe('读取刚上传的文件');
  });
});
