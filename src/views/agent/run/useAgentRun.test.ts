/**
 * useAgentRun 独立运行栈收尾语义回归（2026-07-14 二轮评审补测）：
 * 1) 流正常 EOF 但缺 result 事件 → 显式收尾为失败并落历史（不许永久 pending）；
 * 2) 失败结果同样 appendRunMessage 落历史（刷新后不只剩用户问题）；
 * 3) 运行中禁止删除当前会话。
 * api/stream 模块用 jest.mock 掐断（避免 /@/ 别名与真实网络进入 node 单测链路）。
 */
const mockApi = {
  appendRunMessage: jest.fn(),
  createRunSession: jest.fn(),
  deleteRunSession: jest.fn(),
  getRunApp: jest.fn(),
  getRunMessages: jest.fn(),
  getRunSessions: jest.fn(),
  pinRunSession: jest.fn(),
  renameRunSession: jest.fn(),
  resumeRunDefinition: jest.fn(),
};
jest.mock('./agentRun.api', () => mockApi);

const mockRunAgentStream = jest.fn();
jest.mock('./agentRunStream', () => ({
  runAgentStream: (...args: unknown[]) => mockRunAgentStream(...args),
}));

jest.mock('ant-design-vue', () => ({
  message: { error: jest.fn(), warning: jest.fn() },
}));

import { useAgentRun } from './useAgentRun';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

async function setupRun() {
  mockApi.getRunApp.mockResolvedValue({ id: 'app-1', aiAppType: 'agent', status: 'published' });
  mockApi.createRunSession.mockResolvedValue({ id: 'sess-1', appId: 'app-1', title: '新对话' });
  mockApi.appendRunMessage.mockResolvedValue({ id: 1, title: '新对话' });
  mockApi.deleteRunSession.mockResolvedValue({ success: true });
  mockApi.getRunSessions.mockResolvedValue([]);
  mockApi.getRunMessages.mockResolvedValue([]);
  const run = useAgentRun(() => 'app-1');
  await run.loadApp();
  return run;
}

beforeEach(() => {
  jest.clearAllMocks();
});

test('流 EOF 无 result 事件：显式收尾失败并落历史，不永久 pending', async () => {
  const run = await setupRun();
  mockRunAgentStream.mockImplementation(async (_req: unknown, cbs: any) => {
    cbs.onDelta('部分输出');
    // 正常返回但从未触发 onResult（代理截断而 fetch 未抛错）
  });
  await run.send('你好');
  const last = run.messages.value[run.messages.value.length - 1];
  expect(last.role).toBe('assistant');
  expect(last.pending).toBeFalsy();
  expect(last.failed).toBe(true);
  expect(run.running.value).toBe(false);
  // 已有部分输出时保留正文落库；助手侧落库至少发生一次
  const assistantWrites = mockApi.appendRunMessage.mock.calls.filter((c) => c[1] === 'assistant');
  expect(assistantWrites).toHaveLength(1);
  expect(assistantWrites[0]).toEqual([
    'sess-1', 'assistant', '部分输出', undefined,
    expect.objectContaining({ turnId: expect.any(String), status: 'interrupted' }),
  ]);
});

test('失败结果也落历史（handleResult failed 分支）', async () => {
  const run = await setupRun();
  mockRunAgentStream.mockImplementation(async (_req: unknown, cbs: any) => {
    cbs.onResult({ status: 'failed', output: '', errorMessage: '工作流执行失败' });
  });
  await run.send('干活');
  const last = run.messages.value[run.messages.value.length - 1];
  expect(last.failed).toBe(true);
  expect(last.content).toBe('工作流执行失败');
  const assistantWrites = mockApi.appendRunMessage.mock.calls.filter((c) => c[1] === 'assistant');
  expect(assistantWrites).toHaveLength(1);
  expect(assistantWrites[0]).toEqual([
    'sess-1', 'assistant', '工作流执行失败', undefined,
    expect.objectContaining({ turnId: expect.any(String), status: 'failed' }),
  ]);
});

test('运行中点新对话：旧会话继续跑，切回去仍能看到流式输出', async () => {
  const run = await setupRun();
  let releaseFirst!: () => void;
  mockRunAgentStream.mockImplementationOnce(async (_req: unknown, cbs: any) => {
    cbs.onDelta('一半');
    await new Promise<void>((resolve) => { releaseFirst = resolve; });
    cbs.onResult({ status: 'succeeded', output: '一半完成' });
  });
  mockRunAgentStream.mockImplementationOnce(async (_req: unknown, cbs: any) => {
    cbs.onResult({ status: 'succeeded', output: '第二轮' });
  });
  mockApi.createRunSession
    .mockResolvedValueOnce({ id: 'sess-1', appId: 'app-1', title: '新对话' })
    .mockResolvedValueOnce({ id: 'sess-2', appId: 'app-1', title: '新对话' });

  const firstSend = run.send('问题1');
  await flush();
  expect(run.running.value).toBe(true);
  expect(run.activeSessionId.value).toBe('sess-1');
  expect(run.runningSessionIds.value).toEqual(['sess-1']);

  run.newSession();
  expect(run.activeSessionId.value).toBe('');
  expect(run.messages.value).toEqual([]);
  expect(run.running.value).toBe(false);

  await run.selectSession('sess-1');
  expect(run.messages.value.some((item) => item.content === '一半')).toBe(true);
  expect(run.running.value).toBe(true);

  run.newSession();
  const secondSend = run.send('问题2');
  await secondSend;
  expect(run.runningSessionIds.value).toContain('sess-1');
  expect(run.messages.value.map((item) => item.content)).toContain('第二轮');

  releaseFirst();
  await firstSend;
  expect(run.runningSessionIds.value).not.toContain('sess-1');
});

test('运行中禁止删除当前会话', async () => {
  const run = await setupRun();
  let release!: () => void;
  mockRunAgentStream.mockImplementation(
    () => new Promise<void>((resolve) => { release = () => resolve(); }),
  );
  const sending = run.send('长任务');
  await flush(); // 等 ensureSession 落定 activeSessionId
  expect(run.running.value).toBe(true);
  expect(run.activeSessionId.value).toBe('sess-1');
  await run.removeSession('sess-1');
  expect(mockApi.deleteRunSession).not.toHaveBeenCalled();
  release();
  await sending;
  // 运行结束后可正常删除
  await run.removeSession('sess-1');
  expect(mockApi.deleteRunSession).toHaveBeenCalledWith('sess-1');
});

test('切换子智能体历史会话时恢复文档附件卡', async () => {
  const run = await setupRun();
  mockApi.getRunMessages.mockResolvedValue([{
    id: 1,
    role: 'user',
    content: '请审阅这份文档',
    attachments: [{
      filename: 'report.docx',
      kind: 'docx',
      status: 'ok',
      file_id: 'file-1',
    }],
  }]);

  await run.selectSession('sess-history');

  expect(run.messages.value[0].attachments).toEqual([{
    filename: 'report.docx',
    kind: 'docx',
    previewUrl: undefined,
    status: 'ok',
    note: undefined,
    fileId: 'file-1',
  }]);
});

test('子智能体新发附件把卡片元数据一起落库', async () => {
  const run = await setupRun();
  mockRunAgentStream.mockImplementation(async (_req: unknown, cbs: any) => {
    cbs.onResult({ status: 'success', output: '已审阅' });
  });

  await run.send('请审阅', {
    attachments: [{
      filename: 'report.docx',
      kind: 'docx',
      text: '文档正文',
      status: 'ok',
    }],
  });

  const userWrite = mockApi.appendRunMessage.mock.calls.find((call) => call[1] === 'user');
  expect(userWrite?.slice(0, 4)).toEqual([
    'sess-1',
    'user',
    '请审阅',
    [{
      filename: 'report.docx',
      kind: 'docx',
      status: 'ok',
      note: undefined,
      file_id: undefined,
      preview_url: undefined,
    }],
  ]);
  expect(userWrite?.[4]).toMatchObject({ turnId: expect.any(String) });
});

test('同一轮的用户与助手消息使用同一 turnId 并保存终态状态', async () => {
  const run = await setupRun();
  mockRunAgentStream.mockImplementation(async (_req: unknown, cbs: any) => {
    cbs.onResult({ status: 'failed', output: '执行失败' });
  });

  await run.send('请处理');

  const userWrite = mockApi.appendRunMessage.mock.calls.find((call) => call[1] === 'user');
  const assistantWrite = mockApi.appendRunMessage.mock.calls.find((call) => call[1] === 'assistant');
  const turnId = userWrite?.[4]?.turnId;

  expect(turnId).toEqual(expect.any(String));
  expect(assistantWrite?.[4]).toMatchObject({ turnId, status: 'failed' });
});

test('编辑重发会截掉该条及之后的历史并按新文本再跑', async () => {
  const run = await setupRun();
  mockRunAgentStream.mockImplementation(async (_req: unknown, cbs: any) => {
    cbs.onResult({ status: 'success', output: '旧答' });
  });
  await run.send('第一问');
  mockRunAgentStream.mockImplementation(async (_req: unknown, cbs: any) => {
    cbs.onResult({ status: 'success', output: '第二答' });
  });
  await run.send('第二问');
  run.messages.value[0].id = 11;
  run.messages.value[1].id = 12;
  run.messages.value[2].id = 13;
  run.messages.value[3].id = 14;
  mockRunAgentStream.mockImplementation(async (req: any, cbs: any) => {
    expect(req.input).toBe('改过的第一问');
    expect(req.variables.histories).toEqual([]);
    cbs.onResult({ status: 'success', output: '新答' });
  });
  const accepted = await run.send('改过的第一问', { replaceFromIndex: 0 });
  expect(accepted).toBe(true);
  const editedWrite = mockApi.appendRunMessage.mock.calls.find(
    (call) => call[1] === 'user' && call[2] === '改过的第一问',
  );
  expect(editedWrite?.[4]).toMatchObject({ truncateFromId: 11, turnId: expect.any(String) });
  expect(run.messages.value.map((item) => [item.role, item.content])).toEqual([
    ['user', '改过的第一问'],
    ['assistant', '新答'],
  ]);
});
