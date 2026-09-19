import { defHttp } from '/@/utils/http/axios';

export type AiWorkflowApp = {
  id: string;
  appInfoId?: string;
  aiAppType: 'simple' | 'chatAgent' | 'workflow' | 'workflowTool' | 'httpToolSet' | 'mcpToolSet';
  name?: string;
  description?: string;
  appCategory?: string;
  appIcon?: string;
  configJson?: string;
  status?: string;
  ownerUserId?: string;
  sharePermission?: 'OWNER' | 'VIEWER' | 'EDITOR';
  appInfoStatus?: string;
  publishedAt?: string;
  publishedBy?: string;
  hasUnpublishedChanges?: boolean;
};

export type AiWorkflowDefinition = {
  id: string;
  appId: string;
  appInfoId?: string;
  draftJson?: string;
  publishedJson?: string;
  publishedVersion?: number;
  status?: string;
};

export type WorkflowInteractive = {
  resumeId: string;
  type: 'userSelect' | 'formInput' | 'userInput';
  params: {
    description?: string;
    userSelectOptions?: { value: string; key: string }[];
    inputForm?: {
      label?: string;
      key: string;
      type?: string;
      required?: boolean;
      description?: string;
      defaultValue?: string;
      maxLength?: number;
      list?: { label: string; value: string }[];
    }[];
    userInputForms?: {
      label?: string;
      key: string;
      type?: string;
      required?: boolean;
      description?: string;
      defaultValue?: string;
      maxLength?: number;
      list?: { label: string; value: string }[];
    }[];
  };
};

/** 节点执行轨迹（后端 NodeRun.to_dict()，gap-audit §11-P0-3：禁止 Recordable 静默吞字段） */
export type WorkflowNodeRun = {
  nodeId: string;
  nodeType: string;
  nodeLabel: string;
  status: 'success' | 'failed' | 'skipped' | string;
  durationMs: number;
  input?: unknown;
  output?: unknown;
  outputs?: Record<string, unknown>;
  error?: string;
};

export type WorkflowRunResponse = {
  runId: string;
  status: string;
  output: string;
  errorMessage?: string;
  durationMs: number;
  nodeRuns: WorkflowNodeRun[];
  /** 节点输出快照：nodeId -> { outputKey: value }，如问题分类节点的 cqResult */
  outputs?: Record<string, Record<string, unknown>>;
  /** 连线轨迹快照（waiting/active/skipped），调试后回写画布边样式 */
  edges?: { source: string; sourceHandle?: string; target: string; status: string }[];
  /** 交互节点挂起（userSelect/formInput）：带 resumeId，应答后调 resumeWorkflowDefinition */
  interactive?: WorkflowInteractive;
};

/** A paused, process-local single-step workflow debug session. */
export type WorkflowDebugSession = {
  sessionId: string;
  runId: string;
  status: 'paused' | 'completed' | 'failed' | 'stopped' | string;
  currentNodeId?: string | null;
  nextNodeId?: string | null;
  output: string;
  errorMessage?: string | null;
  nodeRuns: WorkflowNodeRun[];
  lastNodeRun?: WorkflowNodeRun | null;
  outputs?: Record<string, Record<string, unknown>>;
  variables?: Record<string, unknown>;
  edges?: { source: string; sourceHandle?: string; target: string; status: string }[];
  uncaughtErrors?: string[];
  createdAt?: number;
  updatedAt?: number;
  expiresAt?: number;
};

export type WorkflowEvaluationRunRecord = {
  runId: string;
  appId: string;
  mode: 'execute' | 'debug' | 'debug_step' | string;
  definitionHash: string;
  previewOnly: boolean;
  status: string;
  input: string;
  output: string;
  errorMessage?: string | null;
  durationMs?: number | null;
  startedAt?: string | null;
  completedAt?: string | null;
  updatedAt?: string | null;
};

export type WorkflowEvaluationModelCall = {
  id: string;
  model: string;
  purpose: string;
  nodeId: string;
  status: string;
  durationMs?: number | null;
  inputTokens?: number | null;
  outputTokens?: number | null;
  reasoningTokens?: number | null;
  cacheReadTokens?: number | null;
  startedAt?: string | null;
  completedAt?: string | null;
  errorCode?: string | null;
};

export type WorkflowEvaluationRunDetail = WorkflowEvaluationRunRecord & {
  variables: Record<string, unknown>;
  nodeRuns: WorkflowNodeRun[];
  outputs: Record<string, Record<string, unknown>>;
  edges: { source: string; sourceHandle?: string; target: string; status: string }[];
  modelCalls: WorkflowEvaluationModelCall[];
};

export type WorkflowToolCatalogItem = {
  id: string;
  name: string;
  category: string;
  description: string;
  nodeType: string;
  inputKeys?: string[];
  outputKeys?: string[];
};

/** 四 Tab 模板摘要（蓝本 NodeTemplateListItemType 子集）：摘要不可落图，动态项须再取 previewNode */
export type NodeTemplateSummary = {
  id: string;
  pluginId?: string;
  flowNodeType: string;
  templateType: string;
  avatar?: string;
  name: string;
  intro?: string;
  isFolder?: boolean;
  isTool?: boolean;
  source?: string;
  version?: string;
  status?: string;
};

export type NodeTemplateTag = { id: string; label: string };

export type WorkflowPageResult<T> = {
  records: T[];
  total: number;
  size: number;
  current: number;
  pages?: number;
};

export type WorkflowModelOption = {
  label: string;
  value: string;
  provider?: string;
  source?: string;
  available?: boolean;
};

/**
 * 工作流运行时已迁移到 agent-api（Python/FastAPI，v1.9 ADR-031 修订）。
 * /agent-api 前缀经 nginx/vite 代理直达 FastAPI，响应为原始 JSON（无 jeecg Result 包装），
 * 因此所有调用带 isTransformResponse: false。
 */
enum Api {
  pageApp = '/agent-api/workflow/app/page',
  marketplaceApps = '/agent-api/workflow/app/marketplace',
  queryAppById = '/agent-api/workflow/app/queryById',
  queryApp = '/agent-api/workflow/app/queryByAppInfoId',
  queryDefinition = '/agent-api/workflow/definition/queryByAppInfoId',
  saveDefinition = '/agent-api/workflow/definition/save',
  debugDefinition = '/agent-api/workflow/definition/debug',
  builtinTools = '/agent-api/workflow/tool/builtin',
  modelOptions = '/agent-api/workflow/model/options',
}

/** apiUrl 置空：/agent-api 直连 FastAPI，不拼 defHttp 默认的 /api 前缀 */
const RAW = { isTransformResponse: false, apiUrl: '' } as const;

// 读类请求统一静默：失败由调用方内联提示（AGENTS.md：初始化失败走稳定空态，不弹全局错误）
export const queryWorkflowAppPage = (params: {
  pageNo?: number;
  pageSize?: number;
  keyword?: string;
  scope?: 'all' | 'owned' | 'shared';
  aiAppType?: string;
}) => defHttp.get<WorkflowPageResult<AiWorkflowApp>>({ url: Api.pageApp, params }, { ...RAW, errorMessageMode: 'none' });

/** 广场：当前用户可运行的自建已发布智能体（结构对齐 app_info 目录行，可与内置智能体直接合并） */
export const queryMarketplaceWorkflowApps = () =>
  defHttp.get<Record<string, unknown>[]>({ url: Api.marketplaceApps }, { ...RAW, errorMessageMode: 'none' });

export const queryWorkflowAppById = (id: string) =>
  defHttp.get<AiWorkflowApp>({ url: Api.queryAppById, params: { id } }, { ...RAW, errorMessageMode: 'none' });

export const queryWorkflowApp = (params: { appInfoId: string; aiAppType?: string }) =>
  defHttp.get<AiWorkflowApp>({ url: Api.queryApp, params }, { ...RAW, errorMessageMode: 'none' });

export const queryWorkflowDefinition = (params: { appInfoId?: string; appId?: string }) =>
  defHttp.get<AiWorkflowDefinition>({ url: Api.queryDefinition, params }, { ...RAW, errorMessageMode: 'none' });

export const saveWorkflowDefinition = (data: { appId?: string; appInfoId?: string; workflowJson: string }) =>
  defHttp.post<AiWorkflowDefinition>({ url: Api.saveDefinition, data }, RAW);

export const debugWorkflowDefinition = (data: {
  appId?: string;
  appInfoId?: string;
  input?: string;
  workflowJson?: string;
  variables?: Recordable;
}) => defHttp.post<WorkflowRunResponse>({ url: Api.debugDefinition, data }, RAW);

export const createWorkflowDebugSession = (data: {
  appId?: string;
  appInfoId?: string;
  input?: string;
  workflowJson?: string;
  variables?: Recordable;
}) => defHttp.post<WorkflowDebugSession>({ url: '/agent-api/workflow/definition/debug/session', data }, RAW);

export const stepWorkflowDebugSession = (sessionId: string, variables?: Recordable) =>
  defHttp.post<WorkflowDebugSession>(
    { url: `/agent-api/workflow/definition/debug/session/${encodeURIComponent(sessionId)}/step`, data: { variables } },
    RAW,
  );

export const stopWorkflowDebugSession = (sessionId: string) =>
  defHttp.delete<WorkflowDebugSession>(
    { url: `/agent-api/workflow/definition/debug/session/${encodeURIComponent(sessionId)}` },
    RAW,
  );

export const queryWorkflowEvaluationRuns = (appId: string, params: {
  startAt?: string;
  endAt?: string;
  status?: string;
  mode?: string;
  minDurationMs?: number;
  minTotalTokens?: number;
  pageNo?: number;
  pageSize?: number;
}) => defHttp.get<{ records: WorkflowEvaluationRunRecord[]; total: number }>(
  { url: `/agent-api/workflow/app/${encodeURIComponent(appId)}/evaluation-runs`, params },
  { ...RAW, errorMessageMode: 'none' },
);

export const queryWorkflowEvaluationRunDetail = (appId: string, runId: string) =>
  defHttp.get<WorkflowEvaluationRunDetail>(
    { url: `/agent-api/workflow/app/${encodeURIComponent(appId)}/evaluation-runs/${encodeURIComponent(runId)}` },
    { ...RAW, errorMessageMode: 'none' },
  );

export type PromptDebugResult = {
  model: string;
  rawOutput: string;
  durationMs: number;
  usage: { promptTokens: number; completionTokens: number; totalTokens: number };
};

export type PromptDebugRequest = {
  appId: string;
  prompt: string;
  question: string;
  model?: string;
  variables?: Record<string, unknown>;
  temperature?: number;
  maxToken?: number;
  topP?: number;
  stopSign?: string;
  responseFormat?: string;
  jsonSchema?: string;
};

export const debugWorkflowPrompt = (data: PromptDebugRequest) =>
  defHttp.post<PromptDebugResult>(
    { url: '/agent-api/workflow/prompt/debug', data },
    { ...RAW, errorMessageMode: 'none' },
  );

export const generateWorkflowPrompt = (data: {
  appId: string;
  currentPrompt?: string;
  goal?: string;
  model?: string;
  variableKeys?: string[];
}) => defHttp.post<{ prompt: string; model: string; durationMs: number; usage: PromptDebugResult['usage'] }>(
  { url: '/agent-api/workflow/prompt/generate', data },
  { ...RAW, errorMessageMode: 'none' },
);

/** 恢复交互挂起的运行（userSelect 传选项 value，formInput 传对象） */
export const resumeWorkflowDefinition = (data: {
  appId?: string;
  appInfoId?: string;
  previewVersionId?: string;
  previewDraft?: boolean;
  workflowJson?: string;
  resumeId: string;
  value: string | Recordable;
}) => defHttp.post<WorkflowRunResponse>({ url: '/agent-api/workflow/definition/resume', data }, RAW);

// ---------- 四 Tab 节点模板目录（蓝本两阶段协议：摘要列表 + previewNode） ----------

export const queryNodeTemplateTags = () =>
  defHttp.get<NodeTemplateTag[]>(
    { url: '/agent-api/workflow/template/tags' },
    { ...RAW, errorMessageMode: 'none' }
  );

export const queryNodeTemplateList = (params: {
  tab: 'systemTools' | 'myTools' | 'agent';
  searchKey?: string;
  parentId?: string;
  tags?: string;
  excludeAppId?: string;
}) =>
  defHttp.get<NodeTemplateSummary[]>(
    { url: '/agent-api/workflow/template/list', params },
    { ...RAW, errorMessageMode: 'none' }
  );

/** 完整 preview node：失败（400）时前端必须放弃落图（蓝本「获取工具详情失败」语义） */
export const queryNodeTemplatePreview = (params: { id: string; excludeAppId?: string }) =>
  defHttp.get<Recordable>(
    { url: '/agent-api/workflow/template/previewNode', params },
    { ...RAW, errorMessageMode: 'none' }
  );

// 目录类初始化请求：后端接口未部署时走前端稳定空态，不弹全局错误
export const queryBuiltinWorkflowTools = () =>
  defHttp.get<WorkflowToolCatalogItem[]>({ url: Api.builtinTools }, { ...RAW, errorMessageMode: 'none' });

export const queryWorkflowModelOptions = () =>
  defHttp.get<WorkflowModelOption[]>({ url: Api.modelOptions }, { ...RAW, errorMessageMode: 'none' });
