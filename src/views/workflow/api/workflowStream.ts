import type { WorkflowNodeRun, WorkflowRunResponse } from './workflow.api';

export type WorkflowStreamHandlers = {
  signal?: AbortSignal;
  token?: string;
  onNode?: (node: WorkflowNodeRun | Recordable) => void;
  onDelta?: (text: string) => void;
  onResult?: (result: WorkflowRunResponse) => void;
};

export async function runWorkflowDefinitionStream(
  url: string,
  body: Record<string, any>,
  handlers: WorkflowStreamHandlers = {},
): Promise<void> {
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json;charset=UTF-8',
      ...(handlers.token ? { 'X-Access-Token': handlers.token, Authorization: handlers.token } : {}),
    },
    body: JSON.stringify(body),
    signal: handlers.signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`运行失败：${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split(/\r?\n\r?\n/);
    buffer = parts.pop() || '';
    for (const part of parts) dispatchWorkflowStreamEvent(part, handlers);
  }
  if (buffer.trim()) dispatchWorkflowStreamEvent(buffer, handlers);
}

export function dispatchWorkflowStreamEvent(raw: string, handlers: WorkflowStreamHandlers) {
  let event = 'message';
  const dataLines: string[] = [];
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
  }

  const data = dataLines.join('\n').trim();
  if (!data || data === '[DONE]') return;

  let parsed: any;
  try {
    parsed = JSON.parse(data);
  } catch {
    parsed = { text: data, output: data };
  }

  if (event === 'node') {
    handlers.onNode?.(parsed);
    return;
  }
  if (event === 'delta') {
    handlers.onDelta?.(String(parsed.text ?? parsed.output ?? parsed.content ?? ''));
    return;
  }
  if (event === 'result') {
    handlers.onResult?.(parsed);
  }
}
