import type { StepStatus } from './executionTimeline';

/**
 * 失败时必须换成警告三角的工具。
 *
 * 只覆盖有外部副作用、用户可能误判「做成了」的动作：改文件、沙箱执行、点页面、
 * 启用技能、下载。读文件/搜索/打开网页这类探路失败换三角，会让还在干活的 Agent
 * 看起来像坏了，反而掉信任。
 */
const ALARMING_FAILURE_TOOLS = new Set([
  'browser_act',
  'write_file',
  'edit_file',
  'bash',
  'use_skill',
  'download_url',
]);

export function isAlarmingToolFailure(name: string): boolean {
  return ALARMING_FAILURE_TOOLS.has(String(name || ''));
}

export function isWebTimelineStepName(name: string): boolean {
  const n = String(name || '');
  return n === 'search_web' || n === 'deep_read' || n.startsWith('browser_');
}

export function stepIconStatus(step: { name: string; status: StepStatus }): StepStatus {
  if (step.status === 'failed' && isAlarmingToolFailure(step.name)) return 'failed';
  if (isWebTimelineStepName(step.name)) return 'completed';
  if (step.status === 'failed') return 'completed';
  return step.status;
}
