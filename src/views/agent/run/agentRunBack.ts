/** 智能体运行页与父页（广场 iframe）之间的消息协议。 */

export const AGENT_RUN_CLOSE_MESSAGE = 'axiom:agent-run-close';
export const AGENT_RUN_OPEN_FILES_MESSAGE = 'axiom:agent-run-open-files';
export const AGENT_RUN_DOWNLOAD_FILE_MESSAGE = 'axiom:agent-run-download-file';
export const AGENT_RUN_DOWNLOAD_VERSION_MESSAGE = 'axiom:agent-run-download-version';

/** 广场 iframe 里点下载时交给父页执行（父页无 sandbox，blob 下载不会被静默吞掉）。 */
export type AgentRunDownloadFilePayload = {
  id: string;
  filename: string;
  mime?: string;
  size?: number;
  source?: 'uploaded' | 'generated' | 'material' | 'research';
  expiresAt?: string | null;
  createdAt?: string | null;
};

export type AgentRunDownloadVersionPayload = {
  fileId: string;
  id: string;
  versionNo: number;
  filename: string;
};

export function isAgentRunEmbedded(win: { parent: unknown } = window): boolean {
  try {
    return win.parent !== win;
  } catch {
    return true;
  }
}

export function isAgentRunCloseMessage(data: unknown): boolean {
  return Boolean(data && typeof data === 'object' && (data as { type?: string }).type === AGENT_RUN_CLOSE_MESSAGE);
}

export function isAgentRunOpenFilesMessage(data: unknown): boolean {
  return Boolean(data && typeof data === 'object' && (data as { type?: string }).type === AGENT_RUN_OPEN_FILES_MESSAGE);
}

function asRecord(data: unknown): Record<string, unknown> | null {
  return data && typeof data === 'object' ? (data as Record<string, unknown>) : null;
}

export function isAgentRunDownloadFileMessage(
  data: unknown,
): data is { type: typeof AGENT_RUN_DOWNLOAD_FILE_MESSAGE; item: AgentRunDownloadFilePayload } {
  const row = asRecord(data);
  const item = asRecord(row?.item);
  return Boolean(
    row?.type === AGENT_RUN_DOWNLOAD_FILE_MESSAGE
    && typeof item?.id === 'string'
    && item.id
    && typeof item.filename === 'string'
    && item.filename,
  );
}

export function isAgentRunDownloadVersionMessage(
  data: unknown,
): data is { type: typeof AGENT_RUN_DOWNLOAD_VERSION_MESSAGE; item: AgentRunDownloadVersionPayload } {
  const row = asRecord(data);
  const item = asRecord(row?.item);
  return Boolean(
    row?.type === AGENT_RUN_DOWNLOAD_VERSION_MESSAGE
    && typeof item?.fileId === 'string'
    && item.fileId
    && typeof item?.id === 'string'
    && item.id
    && typeof item.filename === 'string'
    && item.filename
    && typeof item.versionNo === 'number',
  );
}

export function requestEmbeddedAgentRunClose(
  target: { postMessage: Window['postMessage'] } = window.parent,
) {
  // 广场把运行页嵌在 iframe 里；pcUrl 可能与当前页不同源，只能用 *，父页靠 type 识别。
  target.postMessage({ type: AGENT_RUN_CLOSE_MESSAGE }, '*');
}

export function requestEmbeddedAgentRunOpenFiles(
  target: { postMessage: Window['postMessage'] } = window.parent,
) {
  target.postMessage({ type: AGENT_RUN_OPEN_FILES_MESSAGE }, '*');
}

export function requestEmbeddedAgentRunDownloadFile(
  item: AgentRunDownloadFilePayload,
  target: { postMessage: Window['postMessage'] } = window.parent,
) {
  target.postMessage({ type: AGENT_RUN_DOWNLOAD_FILE_MESSAGE, item }, '*');
}

export function requestEmbeddedAgentRunDownloadVersion(
  item: AgentRunDownloadVersionPayload,
  target: { postMessage: Window['postMessage'] } = window.parent,
) {
  target.postMessage({ type: AGENT_RUN_DOWNLOAD_VERSION_MESSAGE, item }, '*');
}
