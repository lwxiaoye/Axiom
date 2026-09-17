/** 智能体运行页返回：广场进来回广场，我的智能体进来回我的智能体。 */

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

export type AgentRunFrom = 'agent' | 'my-agent';

export function agentRunBackPath(from?: string | null): '/center/agent' | '/center/my-agent' {
  return from === 'agent' ? '/center/agent' : '/center/my-agent';
}

export function agentRunBackLabel(from?: string | null, embedded = false): string {
  if (embedded || from === 'agent') return '返回智能体广场';
  return '返回我的智能体';
}

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

export function appendAgentRunFrom(url: string, from: AgentRunFrom): string {
  const raw = String(url || '').trim();
  if (!raw) return raw;
  try {
    const parsed = new URL(raw, 'http://local.invalid');
    parsed.searchParams.set('from', from);
    const next = `${parsed.pathname}${parsed.search}${parsed.hash}`;
    if (/^https?:\/\//i.test(raw)) {
      return `${parsed.protocol}//${parsed.host}${next}`;
    }
    return next;
  } catch {
    const joiner = raw.includes('?') ? '&' : '?';
    return `${raw}${joiner}from=${encodeURIComponent(from)}`;
  }
}
