import type { ExecutionActionIconKind } from '../components/ExecutionActionIcon.vue';

export type ExecutionIconStep = {
  name: string;
  operation?: string;
  intent?: string;
};

/**
 * 面试循环工具对外暴露的是动作语言（阅读简历 / 准备下一问），不是内部工具名。
 * 同一 get_interview_session 会对应多种用户可见动作，必须按 intent 选字形，
 * 不能像 bash/read_file 那样「一个工具一个图标」。
 */
const INTERVIEW_INTENT_ICON: Record<string, ExecutionActionIconKind> = {
  阅读简历: 'read',
  阅读岗位要求: 'knowledge',
  查看已答记录: 'search',
  整理本场题目: 'files',
  查看本场面试: 'search',
  准备下一问: 'ask',
  继续面试: 'search',
};

function interviewExecutionIconKind(name: string, intent?: string): ExecutionActionIconKind | null {
  if (name === 'commit_interview_turn') {
    return INTERVIEW_INTENT_ICON[String(intent || '')] || 'ask';
  }
  if (name === 'get_interview_session') {
    return INTERVIEW_INTENT_ICON[String(intent || '')] || 'search';
  }
  return null;
}

export function executionIconKind(step: ExecutionIconStep): ExecutionActionIconKind {
  // 一律先按工具名判（2026-07-27 重排）：一个工具就是一个确定的行为，比 operation 更准；
  // 且 operation 只随完成态 meta 到达——靠它的话运行中会先闪一下兜底图标再换图。
  const name = step.name;
  if (name === 'bash') return 'bash';
  if (name === 'glob') return 'files';
  if (name === 'read_file') return 'read';
  if (name === 'write_file') return 'create';
  if (name === 'edit_file') return 'edit';
  if (name === 'download_url') return 'download';
  if (name === 'browser_fetch' || name === 'browser_open') return 'web';
  if (name === 'browser_act') return 'edit';
  if (name === 'browser_close') return 'view';
  if (name === 'search_knowledge') return 'knowledge';
  if (name === 'use_skill') return 'skill';
  if (name === 'ask_user_choice') return 'ask';
  if (name === 'fetch_tool_result') return 'load';
  if (name.startsWith('search_')) return 'search';
  const interviewKind = interviewExecutionIconKind(name, step.intent);
  if (interviewKind) return interviewKind;
  const operation = step.operation || '';
  if (operation === 'list' || operation === 'glob') return 'files';
  if (operation === 'write') return 'create';
  if (operation === 'download') return 'download';
  if (operation === 'bash' || operation === 'execute') return 'bash';
  if (operation === 'fetch') return 'web';
  if (['read', 'search', 'edit', 'create', 'load'].includes(operation)) {
    return operation as ExecutionActionIconKind;
  }
  return 'tool';
}
