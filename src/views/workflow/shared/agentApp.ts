import { AppTypeEnum } from '../core/constants';

/**
 * AI 应用类型，对齐 FastGPT 工作台（v1.11 §10.5.2，2026-07-02 范围定稿）：
 * chatAgent 对话 Agent（V2，唯一对话形态）/ workflow 工作流 /
 * workflowTool 工作流工具 / httpToolSet HTTP 工具 / mcpToolSet MCP 工具。
 * simple（对话 Agent V1）已排除，仅保留旧数据兼容展示；MCP 服务（发布为 Server）排除。
 */
export type AiAppKind = 'simple' | 'chatAgent' | 'workflow' | 'workflowTool' | 'httpToolSet' | 'mcpToolSet';

/** 「我的智能体」列表展示的类型（simple 仅旧数据兼容；工具类型在「我的工具」中管理） */
export const AGENT_KINDS: AiAppKind[] = ['simple', 'chatAgent', 'workflow'];

/** 「我的工具」列表展示的类型（顺序与蓝本一致：工作流工具/HTTP 工具/MCP 工具） */
export const TOOL_KINDS: AiAppKind[] = ['workflowTool', 'httpToolSet', 'mcpToolSet'];

export type AppOpenType = 'iframe' | '_blank';

export const APP_OPEN_TYPE_OPTIONS: Array<{ label: string; value: AppOpenType }> = [
  { label: '新窗口打开', value: '_blank' },
  { label: '内嵌打开', value: 'iframe' },
];

export function normalizeAppOpenType(value: unknown): AppOpenType {
  return value === 'iframe' ? 'iframe' : '_blank';
}

export type AiAppRecord = {
  id?: string | number;
  workflowAppId?: string | number;
  appType?: string;
  aiAppType?: string;
  appSubType?: string;
  appKind?: string;
  workflowType?: string;
  formOptions?: string | Record<string, unknown> | null;
};

const AI_APP_KIND_SET = new Set<AiAppKind>(['simple', 'chatAgent', 'workflow', 'workflowTool', 'httpToolSet', 'mcpToolSet']);

function normalizeAiAppKind(value: unknown): AiAppKind | undefined {
  if (typeof value === 'string' && AI_APP_KIND_SET.has(value as AiAppKind)) {
    return value as AiAppKind;
  }
  if (value === 'agent' || value === 'chat' || value === 'dialogAgent') {
    return 'chatAgent';
  }
  // 蓝本 AppTypeEnum 的持久化值兼容（advanced=工作流、plugin=工作流工具、toolSet=MCP 工具）
  if (value === AppTypeEnum.workflow) return 'workflow';
  if (value === AppTypeEnum.workflowTool) return 'workflowTool';
  if (value === 'toolSet') return 'mcpToolSet';
  return undefined;
}

function parseFormOptions(value: AiAppRecord['formOptions']) {
  if (!value) return undefined;
  if (typeof value === 'object') return value as Record<string, unknown>;
  try {
    return JSON.parse(value) as Record<string, unknown>;
  } catch {
    return undefined;
  }
}

export function getAiAppKind(record?: AiAppRecord | null): AiAppKind {
  if (!record) return 'chatAgent';
  const directKind =
    normalizeAiAppKind(record.aiAppType) ||
    normalizeAiAppKind(record.appSubType) ||
    normalizeAiAppKind(record.appKind) ||
    normalizeAiAppKind(record.workflowType);
  if (directKind) return directKind;

  const options = parseFormOptions(record.formOptions);
  const optionKind = normalizeAiAppKind(options?.aiAppType || options?.appSubType || options?.appKind);
  if (optionKind) return optionKind;

  return 'chatAgent';
}

export function serializeAiAppOptions(kind: AiAppKind, previous?: AiAppRecord['formOptions']) {
  const options = parseFormOptions(previous) || {};
  return JSON.stringify({
    ...options,
    aiAppType: AI_APP_KIND_SET.has(kind) ? kind : 'chatAgent',
  });
}

const KIND_LABEL_MAP: Record<AiAppKind, string> = {
  simple: '对话 Agent V1',
  chatAgent: '对话 Agent',
  workflow: '工作流',
  workflowTool: '工作流工具',
  httpToolSet: 'HTTP 工具',
  mcpToolSet: 'MCP 工具',
};

export function getAiAppKindLabel(record?: AiAppRecord | null) {
  return KIND_LABEL_MAP[getAiAppKind(record)];
}

export function getAiAppKindLabelByKind(kind: AiAppKind) {
  return KIND_LABEL_MAP[kind];
}

export function isToolKind(record?: AiAppRecord | null) {
  return TOOL_KINDS.includes(getAiAppKind(record));
}

export function getAiAppPrimaryAction(record?: AiAppRecord | null) {
  const kind = getAiAppKind(record);
  return kind === 'workflow' || kind === 'workflowTool' ? '编排' : '配置';
}

export function getAiAppRoute(record: AiAppRecord) {
  const workflowAppId = String(record.workflowAppId || record.id || '');
  const kind = getAiAppKind(record);
  if (kind === 'workflow' || kind === 'workflowTool') {
    return { path: '/workflow/editor', query: { workflowAppId } };
  }
  return { path: '/workflow/agent', query: { workflowAppId } };
}
