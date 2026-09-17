export type WorkAgentMascotState =
  | 'idle'
  | 'thinking'
  | 'searching'
  | 'working'
  | 'discover'
  | 'waiting'
  | 'success'
  | 'error';

export interface MascotAgentStep {
  kind: string;
  status?: string;
  name?: string;
  operation?: string;
  callId?: string;
  pages?: unknown[];
}

export interface MascotChatMessage {
  role?: string;
  runId?: string;
  agentSteps?: readonly MascotAgentStep[];
  interactive?: unknown;
  error?: string | null;
  runFailed?: boolean;
}

const DISCOVERY_ACTION_RE = /(search|browse|browser|fetch|read|glob|knowledge|web|url)/i;

function isActiveStep(step: MascotAgentStep): boolean {
  if (step.status === 'running') return true;
  return step.kind === 'thinking' && step.status !== 'completed' && step.status !== 'failed';
}

function isDiscoveryAction(step: MascotAgentStep): boolean {
  if (step.kind === 'read') return true;
  if (step.kind !== 'tool') return false;
  return DISCOVERY_ACTION_RE.test(`${step.operation || ''} ${step.name || ''}`);
}

function latestActiveStep(message?: MascotChatMessage): MascotAgentStep | undefined {
  const steps = message?.agentSteps || [];
  for (let index = steps.length - 1; index >= 0; index -= 1) {
    if (isActiveStep(steps[index])) return steps[index];
  }
  return undefined;
}

export function latestAssistantMessage<T extends MascotChatMessage>(messages: readonly T[]): T | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index]?.role === 'assistant') return messages[index];
  }
  return undefined;
}

export function selectWorkAgentMascotState(input: {
  loading: boolean;
  stopping?: boolean;
  waiting?: boolean;
  message?: MascotChatMessage;
}): WorkAgentMascotState {
  if (input.waiting || input.message?.interactive) return 'waiting';
  if (!input.loading && (input.message?.runFailed || input.message?.error)) return 'error';
  if (!input.loading && !input.stopping) return 'idle';

  const step = latestActiveStep(input.message);
  if (step?.kind === 'thinking') return 'thinking';
  if (step && isDiscoveryAction(step)) return 'searching';
  return 'working';
}

/**
 * 只有真实完成的检索/查阅步骤才触发“新发现”表情；普通计时、随机间隔和未完成调用不触发。
 */
export function workAgentDiscoverySignature(message?: MascotChatMessage): string {
  const steps = message?.agentSteps || [];
  for (let index = steps.length - 1; index >= 0; index -= 1) {
    const step = steps[index];
    const completedRead = step.kind === 'read';
    const completedDiscoveryTool = step.kind === 'tool'
      && step.status === 'completed'
      && isDiscoveryAction(step);
    if (!completedRead && !completedDiscoveryTool) continue;
    const identity = step.callId || step.name || step.operation || step.kind;
    return `${message?.runId || 'run'}:${index}:${identity}:${step.pages?.length || 0}`;
  }
  return '';
}
