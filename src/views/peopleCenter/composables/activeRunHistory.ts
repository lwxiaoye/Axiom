export interface ActiveRunHistoryMessage {
  id: number;
  role: string;
  content: string;
  runId?: string;
  dbId?: number;
  agentMode?: string;
  feedback?: 'up' | 'down' | null;
}

export interface ActiveRunTraceSource<TTrace = unknown> {
  run_id?: string;
  execution_trace?: TTrace | null;
}

/**
 * A waiting/running Run may already have a durable assistant row. This happens when the model
 * emits public text before a structured approval/input boundary: MySQL stores the text anchor,
 * while Runtime PG correctly keeps the same Run active. Re-entering the thread must replay that
 * Run into the existing row, rather than append a second assistant bubble with the same events.
 *
 * The caller keeps the persisted display projection and resumes from the authoritative event
 * cursor's short interaction suffix. This preserves the already-rendered timeline without visibly
 * replaying the whole Run, while still rebuilding a pending approval/input card.
 */
export function findActiveRunReplayTarget<T extends ActiveRunHistoryMessage>(
  messages: T[],
  runId: string,
): { messageId: number; content: string } | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role !== 'assistant' || message.runId !== runId) continue;
    return { messageId: message.id, content: message.content };
  }
  return undefined;
}

/**
 * Before an active Run persists its first assistant row, history carries the Runtime snapshot on
 * either its run-bound input row or a transient assistant-side projection. Recover it by run_id so
 * re-entering the thread can render every persisted step and its timer once, then continue after
 * the same-batch cursor instead of rebuilding a tail-only placeholder.
 */
export function findActiveRunTraceSnapshot<TTrace>(
  messages: Array<ActiveRunTraceSource<TTrace>>,
  runId: string,
): TTrace | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.run_id !== runId || !message.execution_trace) continue;
    return message.execution_trace;
  }
  return undefined;
}
