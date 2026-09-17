import {
  isLiveRunningAction,
  looksLikePlanReport,
  type AgentStep,
} from './executionTimeline';

/** 计量行对外只保留这一组阶段文案；随真实步骤实时切换，不展示 token。 */
export const GENERATION_METER_LABELS = {
  queued: '等待任务启动...',
  recovering: '等待任务恢复...',
  understand: '正在理解任务...',
  analyze: '正在分析需求...',
  plan: '正在规划执行方案...',
  next: '正在规划下一步...',
  search: '正在搜索资料...',
  organize: '正在整理信息...',
  tool: '正在调用工具...',
  file: '正在处理文件...',
  write: '正在生成内容...',
} as const;

export type GenerationMeterLabel =
  (typeof GENERATION_METER_LABELS)[keyof typeof GENERATION_METER_LABELS];

export type GenerationMeterMessage = {
  runStatus?: string;
  agentMode?: string;
  agentSteps?: AgentStep[];
  researchProgress?: { stage?: string; label?: string };
  planReport?: string;
  content?: string;
  taskPlan?: unknown[];
  reasoningSummary?: string;
  artifactPages?: unknown[];
  runStartedAt?: number;
};

export type GenerationMeterContext = {
  now: number;
  hasAssistantBody: boolean;
  isPlanConfirmation: boolean;
  hasGeneratedFiles: boolean;
};

function elapsedSec(message: GenerationMeterMessage, now: number): number {
  const base = message.runStartedAt || now;
  return Math.max(0, Math.floor((now - base) / 1000));
}

function toolName(step: AgentStep | undefined): string {
  if (!step || step.kind !== 'tool') return '';
  return String(step.name || '').toLowerCase();
}

function isSearchTool(name: string): boolean {
  return name === 'search_web' || name === 'search_knowledge' || name.includes('search');
}

function isReadWebTool(name: string): boolean {
  return name === 'deep_read' || name.startsWith('browser');
}

function isFileTool(name: string): boolean {
  return (
    name === 'bash'
    || name === 'write_file'
    || name === 'edit_file'
    || name === 'read_file'
    || name === 'glob'
    || name === 'download_url'
    || name === 'get_interview_session'
  );
}

function toolLabel(name: string, executingPlan: boolean): GenerationMeterLabel {
  const { search, organize, plan, next, file, tool, write } = GENERATION_METER_LABELS;
  if (isSearchTool(name)) return search;
  if (isReadWebTool(name)) return organize;
  if (name === 'update_plan') return executingPlan ? next : plan;
  if (name === 'commit_interview_turn') return write;
  if (isFileTool(name)) return file;
  return tool;
}

function isExecutingPlan(message: GenerationMeterMessage): boolean {
  return (message.taskPlan || []).some((item) => {
    const status = item && typeof item === 'object' ? String((item as { status?: string }).status || '') : '';
    return status === 'running' || status === 'completed';
  });
}

function hasActed(message: GenerationMeterMessage): boolean {
  return (message.agentSteps || []).some((step) => step.kind === 'tool' || step.kind === 'subagent');
}

function liveToolStatus(message: GenerationMeterMessage): GenerationMeterLabel | '' {
  const steps = message.agentSteps || [];
  const executingPlan = isExecutingPlan(message);
  for (let i = steps.length - 1; i >= 0; i -= 1) {
    const step = steps[i];
    if (!step || !('status' in step) || step.status !== 'running') continue;
    if (!isLiveRunningAction(steps, i)) continue;
    if (step.kind === 'tool') return toolLabel(toolName(step), executingPlan);
    if (step.kind === 'subagent') return GENERATION_METER_LABELS.tool;
  }
  return '';
}

function researchStatus(message: GenerationMeterMessage): GenerationMeterLabel | '' {
  const stage = String(message.researchProgress?.stage || '');
  const label = String(message.researchProgress?.label || '');
  const { write, organize, next, plan, analyze } = GENERATION_METER_LABELS;
  if (stage === 'synthesizing' || /整理研究结论|生成/.test(label)) return write;
  if (stage === 'verifying' || stage === 'reviewing' || /交叉验证|交流核验/.test(label)) return organize;
  if (stage === 'researching' || /正在研究/.test(label)) return next;
  if (stage === 'planning' || /拆解研究计划/.test(label)) return plan;
  if (stage === 'clarifying' || /对齐研究边界/.test(label)) return analyze;
  return '';
}

function hasThinkingSignal(message: GenerationMeterMessage): boolean {
  if (String(message.reasoningSummary || '').trim()) return true;
  return (message.agentSteps || []).some((step) => step.kind === 'thinking');
}

function hasPlanContent(message: GenerationMeterMessage): boolean {
  return Boolean(
    looksLikePlanReport(message.planReport || '')
    || looksLikePlanReport(message.content || '')
    || (message.taskPlan && message.taskPlan.length),
  );
}

function isPlanningPhase(message: GenerationMeterMessage, isPlanConfirmation: boolean): boolean {
  if (isPlanConfirmation) return false;
  if ((message.agentSteps || []).some((step) => toolName(step) === 'update_plan')) return true;
  if (hasPlanContent(message)) return true;
  return String(message.agentMode || '') === 'plan' && !isExecutingPlan(message);
}

function startPhaseStatus(
  message: GenerationMeterMessage,
  ctx: GenerationMeterContext,
): GenerationMeterLabel {
  const elapsed = elapsedSec(message, ctx.now);
  const thinking = hasThinkingSignal(message);
  const planning = isPlanningPhase(message, ctx.isPlanConfirmation);
  if (planning && (hasPlanContent(message) || elapsed >= 3 || (thinking && elapsed >= 2))) {
    return GENERATION_METER_LABELS.plan;
  }
  if (thinking && elapsed >= 4) return GENERATION_METER_LABELS.next;
  if (thinking || elapsed >= 2) return GENERATION_METER_LABELS.analyze;
  return GENERATION_METER_LABELS.understand;
}

export function generationMeterStatus(
  message: GenerationMeterMessage,
  ctx: GenerationMeterContext,
): GenerationMeterLabel {
  if (message.runStatus === 'created') return GENERATION_METER_LABELS.queued;
  if (message.runStatus === 'waiting_system') return GENERATION_METER_LABELS.recovering;
  const live = liveToolStatus(message);
  if (live) return live;
  if (ctx.hasAssistantBody || (message.artifactPages || []).length) {
    return GENERATION_METER_LABELS.write;
  }
  if (hasActed(message)) return GENERATION_METER_LABELS.next;
  const research = researchStatus(message);
  if (research) return research;
  if (ctx.hasGeneratedFiles) return GENERATION_METER_LABELS.write;
  return startPhaseStatus(message, ctx);
}

/** Interview-only status copy. Main chat keeps GENERATION_METER_LABELS. */
export const INTERVIEW_GENERATION_METER_LABELS: Record<GenerationMeterLabel, string> = {
  '等待任务启动...': '等待任务启动...',
  '等待任务恢复...': '等待任务恢复...',
  '正在理解任务...': '正在了解你的经历...',
  '正在分析需求...': '正在对照岗位要求...',
  '正在规划执行方案...': '正在组织本场题目...',
  '正在规划下一步...': '正在准备下一问...',
  '正在搜索资料...': '正在对照岗位要求...',
  '正在整理信息...': '正在整理面试材料...',
  '正在调用工具...': '正在查看本场面试...',
  '正在处理文件...': '正在阅读简历...',
  '正在生成内容...': '正在出题...',
};

export function interviewGenerationMeterStatus(
  message: GenerationMeterMessage,
  ctx: GenerationMeterContext,
): string {
  const status = generationMeterStatus(message, ctx);
  return INTERVIEW_GENERATION_METER_LABELS[status] || status;
}
