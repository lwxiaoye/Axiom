import {
  AGENT_RUN_CLOSE_MESSAGE,
  AGENT_RUN_DOWNLOAD_FILE_MESSAGE,
  AGENT_RUN_DOWNLOAD_VERSION_MESSAGE,
  AGENT_RUN_OPEN_FILES_MESSAGE,
  isAgentRunCloseMessage,
  isAgentRunDownloadFileMessage,
  isAgentRunDownloadVersionMessage,
  isAgentRunOpenFilesMessage,
  isAgentRunEmbedded,
  requestEmbeddedAgentRunClose,
  requestEmbeddedAgentRunDownloadFile,
  requestEmbeddedAgentRunDownloadVersion,
  requestEmbeddedAgentRunOpenFiles,
} from './agentRunBack';

describe('agentRunBack', () => {
  it('iframe 内视为嵌在广场里', () => {
    expect(isAgentRunEmbedded({ parent: {} })).toBe(true);
    const self = {} as { parent: unknown };
    self.parent = self;
    expect(isAgentRunEmbedded(self)).toBe(false);
  });

  it('关闭消息只认约定 type', () => {
    expect(isAgentRunCloseMessage({ type: AGENT_RUN_CLOSE_MESSAGE })).toBe(true);
    expect(isAgentRunCloseMessage({ type: 'close' })).toBe(false);
    const posted: Array<{ data: unknown; origin: string }> = [];
    requestEmbeddedAgentRunClose(
      { postMessage: (data, origin) => posted.push({ data, origin: String(origin) }) },
    );
    expect(posted).toEqual([{ data: { type: AGENT_RUN_CLOSE_MESSAGE }, origin: '*' }]);
  });

  it('打开我的文件消息只认约定 type', () => {
    expect(isAgentRunOpenFilesMessage({ type: AGENT_RUN_OPEN_FILES_MESSAGE })).toBe(true);
    expect(isAgentRunOpenFilesMessage({ type: AGENT_RUN_CLOSE_MESSAGE })).toBe(false);
    const posted: Array<{ data: unknown; origin: string }> = [];
    requestEmbeddedAgentRunOpenFiles(
      { postMessage: (data, origin) => posted.push({ data, origin: String(origin) }) },
    );
    expect(posted).toEqual([{ data: { type: AGENT_RUN_OPEN_FILES_MESSAGE }, origin: '*' }]);
  });

  it('下载消息只认带 id/filename 的约定 type', () => {
    const item = { id: 'file-1', filename: '差旅.xlsx' };
    expect(isAgentRunDownloadFileMessage({ type: AGENT_RUN_DOWNLOAD_FILE_MESSAGE, item })).toBe(true);
    expect(isAgentRunDownloadFileMessage({ type: AGENT_RUN_DOWNLOAD_FILE_MESSAGE, item: { id: '', filename: 'a' } })).toBe(false);
    expect(isAgentRunDownloadFileMessage({ type: AGENT_RUN_OPEN_FILES_MESSAGE, item })).toBe(false);
    const posted: Array<{ data: unknown; origin: string }> = [];
    requestEmbeddedAgentRunDownloadFile(
      item,
      { postMessage: (data, origin) => posted.push({ data, origin: String(origin) }) },
    );
    expect(posted).toEqual([{ data: { type: AGENT_RUN_DOWNLOAD_FILE_MESSAGE, item }, origin: '*' }]);
  });

  it('版本下载消息只认带 fileId/id/filename 的约定 type', () => {
    const item = { fileId: 'file-1', id: 'ver-2', versionNo: 2, filename: '差旅.xlsx' };
    expect(isAgentRunDownloadVersionMessage({ type: AGENT_RUN_DOWNLOAD_VERSION_MESSAGE, item })).toBe(true);
    expect(isAgentRunDownloadVersionMessage({ type: AGENT_RUN_DOWNLOAD_FILE_MESSAGE, item })).toBe(false);
    const posted: Array<{ data: unknown; origin: string }> = [];
    requestEmbeddedAgentRunDownloadVersion(
      item,
      { postMessage: (data, origin) => posted.push({ data, origin: String(origin) }) },
    );
    expect(posted).toEqual([{ data: { type: AGENT_RUN_DOWNLOAD_VERSION_MESSAGE, item }, origin: '*' }]);
  });
});
