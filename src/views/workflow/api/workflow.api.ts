import { defHttp } from '/@/utils/http/axios';
import { getToken } from '/@/utils/auth';
import { agentAuthHeaders } from '../../peopleCenter/utils/agentAuthHeaders';
import { buildWorkflowAppUploadHeaders } from '../shared/appPackage';
import {
  buildHttpToolTestFormData,
  type HttpToolSetConfig,
} from '../../peopleCenter/workbench/httpToolConfig';

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
  reviewSummary?: WorkflowReviewSummary;
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

export type WorkflowAclItem = {
  id?: string;
  tenantId?: string;
  appId?: string;
  subjectType: 'USER' | 'ROLE' | 'DEPARTMENT';
  subjectId: string;
  permission: 'VIEWER' | 'EDITOR';
  createTime?: string;
};

export type WorkflowPageResult<T> = {
  records: T[];
  total: number;
  size: number;
  current: number;
  pages?: number;
};

export type MetricRange =
  | 'today'
  | 'last_7_days'
  | 'last_4_weeks'
  | 'last_3_months'
  | 'last_12_months'
  | 'month_to_date'
  | 'quarter_to_date'
  | 'year_to_date'
  | 'all_time';

export type WorkflowAppMetricsDaily = {
  date: string;
  sessions: number;
  activeUsers: number;
  newUsers: number;
  returningUsers: number;
  interactions: number;
  messages: number;
};

export type WorkflowAppMetrics = {
  range: MetricRange;
  startDate: string;
  endDate: string;
  isConversational: boolean;
  totals: {
    sessions: number;
    activeUsers: number;
    newUsers: number;
    returningUsers: number;
    averageMessages: number;
    messages: number;
  };
  daily: WorkflowAppMetricsDaily[];
};

export type ConversationLogStatus = 'completed' | 'partial' | 'failed' | 'cancelled' | 'interrupted' | 'unknown';
export type ConversationLogRecord = {
  id: string;
  threadId: string;
  title: string;
  userId: string;
  username: string;
  status: ConversationLogStatus;
  messageCount: number;
  lastMessageAt?: string | null;
  upvotes: number;
  downvotes: number;
};
export type ConversationLogMessage = {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  status?: ConversationLogStatus | null;
  feedback: 'up' | 'down' | null;
  createdAt?: string | null;
};
export type ConversationLogDetail = {
  threadId: string;
  title: string;
  userId: string;
  username: string;
  messages: ConversationLogMessage[];
};
export type ConversationLogQuery = {
  range?: MetricRange;
  startAt?: string;
  endAt?: string;
  status?: ConversationLogStatus;
  keyword?: string;
  pageNo?: number;
  pageSize?: number;
};
export type ConversationLogPage = { records: ConversationLogRecord[]; total: number };

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
  marketplaceCreators = '/agent-api/workflow/app/marketplace-creators',
  queryAppById = '/agent-api/workflow/app/queryById',
  addApp = '/agent-api/workflow/app/add',
  editApp = '/agent-api/workflow/app/edit',
  deleteApp = '/agent-api/workflow/app/delete',
  cancelPublishApp = '/agent-api/workflow/app/cancelPublish',
  exportApp = '/agent-api/workflow/app/export',
  importApp = '/agent-api/workflow/app/import',
  copyApp = '/agent-api/workflow/app/copy',
  appAclList = '/agent-api/workflow/app/acl/list',
  appAclSave = '/agent-api/workflow/app/acl/save',
  queryApp = '/agent-api/workflow/app/queryByAppInfoId',
  saveAppConfig = '/agent-api/workflow/app/saveConfig',
  queryDefinition = '/agent-api/workflow/definition/queryByAppInfoId',
  saveDefinition = '/agent-api/workflow/definition/save',
  publishDefinition = '/agent-api/workflow/definition/publish',
  debugDefinition = '/agent-api/workflow/definition/debug',
  executeDefinition = '/agent-api/workflow/definition/execute',
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

export type MarketplaceCreator = {
  appId: string;
  creatorName: string;
  creatorAvatar: string;
};

export const queryMarketplaceCreators = (appIds: Array<string | number>) =>
  defHttp.post<{ records: MarketplaceCreator[] }>(
    { url: Api.marketplaceCreators, data: { appIds: appIds.map(String) } },
    { ...RAW, errorMessageMode: 'none' },
  );

export const queryWorkflowAppById = (id: string) =>
  defHttp.get<AiWorkflowApp>({ url: Api.queryAppById, params: { id } }, { ...RAW, errorMessageMode: 'none' });

export const queryWorkflowAppMetrics = (appId: string, range: MetricRange) =>
  defHttp.get<WorkflowAppMetrics>(
    { url: `/agent-api/workflow/app/${encodeURIComponent(appId)}/metrics`, params: { range } },
    { ...RAW, errorMessageMode: 'none' }
  );

export const queryAdminConversationLogs = (appId: string, params: ConversationLogQuery) =>
  defHttp.get<ConversationLogPage>(
    { url: `/agent-api/workflow/admin/app/${encodeURIComponent(appId)}/conversation-logs`, params },
    { ...RAW, errorMessageMode: 'none' },
  );

export const queryOwnerConversationLogs = (appId: string, params: ConversationLogQuery) =>
  defHttp.get<ConversationLogPage>(
    { url: `/agent-api/workflow/app/${encodeURIComponent(appId)}/conversation-logs`, params },
    { ...RAW, errorMessageMode: 'none' },
  );

export const queryAdminConversationLogDetail = (appId: string, threadId: string) =>
  defHttp.get<ConversationLogDetail>(
    { url: `/agent-api/workflow/admin/app/${encodeURIComponent(appId)}/conversation-logs/${encodeURIComponent(threadId)}` },
    { ...RAW, errorMessageMode: 'none' },
  );

export const queryOwnerConversationLogDetail = (appId: string, threadId: string) =>
  defHttp.get<ConversationLogDetail>(
    { url: `/agent-api/workflow/app/${encodeURIComponent(appId)}/conversation-logs/${encodeURIComponent(threadId)}` },
    { ...RAW, errorMessageMode: 'none' },
  );

function buildConversationLogExportParams(query: ConversationLogQuery) {
  const { pageNo: _pageNo, pageSize: _pageSize, ...filters } = query;
  return new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== undefined && value !== '') as [string, string][]);
}

async function downloadConversationLogs(path: string, query: ConversationLogQuery) {
  const response = await fetch(`${path}?${buildConversationLogExportParams(query).toString()}`, {
    headers: agentAuthHeaders({ Accept: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }),
  });
  if (!response.ok) {
    let detail = `导出失败：${response.status}`;
    try { detail = String((await response.json())?.detail || detail); } catch { /* keep fallback */ }
    throw new Error(detail);
  }
  const blob = await response.blob();
  const filename = response.headers.get('content-disposition')?.match(/filename="([^\"]+)"/i)?.[1] || 'agent-conversation-logs.xlsx';
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename.replace(/[\\/:*?"<>|]/g, '_');
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export const downloadAdminConversationLogs = (appId: string, query: ConversationLogQuery) =>
  downloadConversationLogs(`/agent-api/workflow/admin/app/${encodeURIComponent(appId)}/conversation-logs/export`, query);

export const downloadOwnerConversationLogs = (appId: string, query: ConversationLogQuery) =>
  downloadConversationLogs(`/agent-api/workflow/app/${encodeURIComponent(appId)}/conversation-logs/export`, query);

export const createWorkflowApp = (data: Partial<AiWorkflowApp>) =>
  defHttp.post<AiWorkflowApp>({ url: Api.addApp, data }, RAW);

export const updateWorkflowApp = (data: Partial<AiWorkflowApp>) =>
  defHttp.put<AiWorkflowApp>({ url: Api.editApp, data }, RAW);

export const deleteWorkflowApp = (id: string) =>
  defHttp.delete({ url: Api.deleteApp, params: { id } }, { ...RAW, joinParamsToUrl: true });

export const cancelPublishWorkflowApp = (id: string) =>
  defHttp.post<AiWorkflowApp>({ url: Api.cancelPublishApp, params: { id } }, { ...RAW, joinParamsToUrl: true });

export type WorkflowAppExportPackage = {
  format: 'axiom.agent-app';
  version: number;
  app: {
    aiAppType: AiWorkflowApp['aiAppType'];
    name: string;
    description?: string;
    appCategory?: string;
    appIcon?: string;
    configJson?: string;
  };
  definition?: {
    draftJson?: string | null;
    publishedJson?: string | null;
    publishedVersion?: number;
  };
};

export const exportWorkflowAppPackage = (id: string) =>
  defHttp.get<WorkflowAppExportPackage>({ url: Api.exportApp, params: { id } }, RAW);

export const importWorkflowAppPackage = (file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  return fetch(Api.importApp, {
    method: 'POST',
    headers: buildWorkflowAppUploadHeaders(getToken()),
    body: formData,
  }).then(async (response) => {
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = typeof data?.detail === 'string' ? data.detail : `导入失败：${response.status}`;
      throw new Error(detail);
    }
    return data as AiWorkflowApp;
  });
};

export const copyWorkflowApp = (id: string) =>
  defHttp.post<AiWorkflowApp>({ url: Api.copyApp, params: { id } }, { ...RAW, joinParamsToUrl: true });

export const queryWorkflowAppAcl = (appId: string) =>
  defHttp.get<WorkflowAclItem[]>({ url: Api.appAclList, params: { appId } }, { ...RAW, errorMessageMode: 'none' });

export const saveWorkflowAppAcl = (appId: string, items: WorkflowAclItem[]) =>
  defHttp.put({ url: Api.appAclSave, data: { appId, items } }, RAW);

export const queryWorkflowApp = (params: { appInfoId: string; aiAppType?: string }) =>
  defHttp.get<AiWorkflowApp>({ url: Api.queryApp, params }, { ...RAW, errorMessageMode: 'none' });

export const saveWorkflowAppConfig = (data: Partial<AiWorkflowApp>) =>
  defHttp.post<AiWorkflowApp>({ url: Api.saveAppConfig, data }, RAW);

export const queryWorkflowDefinition = (params: { appInfoId?: string; appId?: string }) =>
  defHttp.get<AiWorkflowDefinition>({ url: Api.queryDefinition, params }, { ...RAW, errorMessageMode: 'none' });

export const saveWorkflowDefinition = (data: { appId?: string; appInfoId?: string; workflowJson: string }) =>
  defHttp.post<AiWorkflowDefinition>({ url: Api.saveDefinition, data }, RAW);

// 发布失败（含 400 校验拒绝）由编辑器展示结构化 problems，不走全局错误提示
export const publishWorkflowDefinition = (data: { appId?: string; appInfoId?: string; workflowJson: string }) =>
  defHttp.post<AiWorkflowDefinition>({ url: Api.publishDefinition, data }, { ...RAW, errorMessageMode: 'none' });

// ---------- 发布审批 + 版本历史 + 后台管理（WS2/WS3/WS4） ----------

export type WorkflowVersionItem = {
  id: string;
  appId: string;
  versionNo: number;
  aiAppType: string;
  status: 'pending_review' | 'approved' | 'rejected' | 'cancelled' | 'archived' | string;
  changeNote: string;
  visibleRoleIds?: string[] | string;
  visibleDeptIds?: string[] | string;
  submittedBy?: string;
  submittedByName: string;
  submittedAt?: string;
  reviewedBy?: string;
  reviewedByName: string;
  reviewedAt?: string;
  reviewComment: string;
  publishedAt?: string;
  isLive?: boolean;
  appName?: string;
  ownerUserId?: string;
  ownerUsername?: string;
  definitionJson?: string;
  configJson?: string;
  routeMetadata?: RouteMetadata | null;
};

export type WorkflowReviewSummary = {
  versionId: string;
  versionNo: number;
  status: 'pending_review' | 'rejected';
  submittedAt?: string | null;
  reviewedAt?: string | null;
  reviewedByName?: string;
  reviewComment?: string;
};

/** 发布弹窗可选路由元数据（语义发现升级）：随版本冻结，审核通过后进入主对话候选召回 */
export type RouteMetadata = {
  routeDescription?: string;
  triggerExamples?: string[];
  negativeExamples?: string[];
  tags?: string[];
};

export type SubmitReviewResult = { version: WorkflowVersionItem; approvalRequired: boolean; message: string };
export type MyCapabilities = { isReviewer: boolean; isPlatformAdmin: boolean; approvalRequired: boolean };
export type AdminAppItem = Omit<AiWorkflowApp, 'aiAppType'> & {
  aiAppType: AiWorkflowApp['aiAppType'] | 'builtin';
  ownerUsername?: string;
  catalogAppId?: string;
  builtinPreset?: string;
  entryPath?: string;
  createdAt?: string;
};
export type WorkflowAdminAuditItem = {
  id: string;
  action: string;
  actorUsername: string;
  targetUserId?: string;
  reason?: string;
  createdAt?: string;
};
export type AdminAppDetail = {
  app: AdminAppItem;
  summary: {
    model: string;
    nodeTypes: string[];
    dependencyIds: string[];
    publishedVersion: number;
    liveVersion?: { versionNo: number; status: string; changeNote: string } | null;
  };
  audits: WorkflowAdminAuditItem[];
  versions?: WorkflowVersionItem[];
};
export type WorkflowVersionDiff = {
  baseVersionNo: number;
  targetVersionNo: number;
  changedFields: string[];
  base: Record<string, unknown>;
  target: Record<string, unknown>;
};

/** 提交发布审核（强制审批：进入待审队列，不即时上线）。400 + problems 由编辑器阻断展示 */
export const submitWorkflowReview = (data: {
  appId?: string;
  appInfoId?: string;
  workflowJson: string;
  changeNote?: string;
  visibleRoleIds?: string[] | string;
  visibleDeptIds?: string[] | string;
  routeMetadata?: RouteMetadata;
}) =>
  defHttp.post<SubmitReviewResult>({ url: '/agent-api/workflow/definition/submitReview', data }, { ...RAW, errorMessageMode: 'none' });

export type AgentApiKeyItem = {
  id: string;
  name: string;
  prefix: string;
  /** Returned only by the publisher-authenticated key management endpoints. */
  secret?: string | null;
  status: string;
  expiresAt?: string | null;
  lastUsedAt?: string | null;
  revokedAt?: string | null;
  createdAt?: string | null;
};

export type AgentApiKeyCreated = Pick<AgentApiKeyItem, 'id' | 'name' | 'prefix' | 'expiresAt'> & { secret: string };
export type AgentEmbedKeyItem = AgentApiKeyItem & { origin: string };
export type AgentEmbedKeyCreated = Pick<AgentEmbedKeyItem, 'id' | 'name' | 'prefix' | 'origin'> & { secret: string };
export type AgentPublicConfiguration = { apiEnabled: boolean; iframeEnabled: boolean };

export type AgentApiUsageItem = {
  id: string;
  versionId: string;
  keyId: string;
  source: 'openai' | 'embed' | string;
  status: string;
  httpStatus?: number | null;
  durationMs?: number | null;
  inputTokens?: number | null;
  outputTokens?: number | null;
  reasoningTokens?: number | null;
  usageKnown: boolean;
  providerAmountRaw?: string | null;
  providerAmountUnit?: string | null;
  startedAt?: string | null;
  completedAt?: string | null;
  errorCode?: string | null;
};

export const listAgentApiKeys = (appId: string) =>
  defHttp.get<{ items: AgentApiKeyItem[] }>(
    { url: `/agent-api/workflow/apps/${encodeURIComponent(appId)}/api-keys` },
    { ...RAW, errorMessageMode: 'none' },
  );

export const createAgentApiKey = (appId: string, name: string) =>
  defHttp.post<AgentApiKeyCreated>(
    { url: `/agent-api/workflow/apps/${encodeURIComponent(appId)}/api-keys`, data: { name } },
    { ...RAW, errorMessageMode: 'none' },
  );

export const updateAgentApiKeyStatus = (appId: string, keyId: string, enabled: boolean) =>
  defHttp.patch(
    { url: `/agent-api/workflow/apps/${encodeURIComponent(appId)}/api-keys/${encodeURIComponent(keyId)}/status`, data: { enabled } },
    { ...RAW, errorMessageMode: 'none' },
  );

export const deleteAgentApiKey = (appId: string, keyId: string) =>
  defHttp.delete(
    { url: `/agent-api/workflow/apps/${encodeURIComponent(appId)}/api-keys/${encodeURIComponent(keyId)}` },
    { ...RAW, errorMessageMode: 'none' },
  );

export const listAgentEmbedKeys = (appId: string) =>
  defHttp.get<{ items: AgentEmbedKeyItem[] }>(
    { url: `/agent-api/workflow/apps/${encodeURIComponent(appId)}/embed-keys` },
    { ...RAW, errorMessageMode: 'none' },
  );

export const createAgentEmbedKey = (appId: string, name: string, origin: string) =>
  defHttp.post<AgentEmbedKeyCreated>(
    { url: `/agent-api/workflow/apps/${encodeURIComponent(appId)}/embed-keys`, data: { name, origin } },
    { ...RAW, errorMessageMode: 'none' },
  );

export const updateAgentEmbedKeyStatus = (appId: string, keyId: string, enabled: boolean) =>
  defHttp.patch(
    { url: `/agent-api/workflow/apps/${encodeURIComponent(appId)}/embed-keys/${encodeURIComponent(keyId)}/status`, data: { enabled } },
    { ...RAW, errorMessageMode: 'none' },
  );

export const deleteAgentEmbedKey = (appId: string, keyId: string) =>
  defHttp.delete(
    { url: `/agent-api/workflow/apps/${encodeURIComponent(appId)}/embed-keys/${encodeURIComponent(keyId)}` },
    { ...RAW, errorMessageMode: 'none' },
  );

export const getAgentPublicConfiguration = (appId: string) =>
  defHttp.get<AgentPublicConfiguration>(
    { url: `/agent-api/workflow/apps/${encodeURIComponent(appId)}/public-config` },
    { ...RAW, errorMessageMode: 'none' },
  );

export const updateAgentPublicConfiguration = (appId: string, data: AgentPublicConfiguration) =>
  defHttp.put<AgentPublicConfiguration>(
    { url: `/agent-api/workflow/apps/${encodeURIComponent(appId)}/public-config`, data },
    { ...RAW, errorMessageMode: 'none' },
  );

export const queryAgentApiUsage = (appId: string, params?: { pageNo?: number; pageSize?: number }) =>
  defHttp.get<{ total: number; records: AgentApiUsageItem[] }>(
    { url: `/agent-api/workflow/apps/${encodeURIComponent(appId)}/api-usage`, params },
    { ...RAW, errorMessageMode: 'none' },
  );

export const queryWorkflowVersionPage = (params: { appId: string; pageNo?: number; pageSize?: number }) =>
  defHttp.get<WorkflowPageResult<WorkflowVersionItem> & { liveVersion: number }>(
    { url: '/agent-api/workflow/version/page', params },
    { ...RAW, errorMessageMode: 'none' }
  );

export const queryWorkflowVersionDetail = (id: string) =>
  defHttp.get<WorkflowVersionItem>({ url: '/agent-api/workflow/version/detail', params: { id } }, { ...RAW, errorMessageMode: 'none' });

/** 所有者恢复一个已通过历史版本；服务端会创建新的线上版本号并保留回滚审计。 */
export const rollbackWorkflowVersion = (appId: string, versionNo: number) =>
  defHttp.post<{ message: string; liveVersion: number }>(
    { url: '/agent-api/workflow/version/rollback', data: { appId, versionNo } },
    { ...RAW, errorMessageMode: 'none' }
  );

export const queryMyCapabilities = () =>
  defHttp.get<MyCapabilities>({ url: '/agent-api/workflow/me/capabilities' }, { ...RAW, errorMessageMode: 'none' });

// 审核台（审核员）
export const queryReviewPage = (params: { pageNo?: number; pageSize?: number; status?: string; aiAppType?: string; keyword?: string }) =>
  defHttp.get<WorkflowPageResult<WorkflowVersionItem>>({ url: '/agent-api/workflow/review/page', params }, { ...RAW, errorMessageMode: 'none' });

export const approveReview = (versionId: string, comment?: string) =>
  defHttp.post({ url: '/agent-api/workflow/review/approve', data: { versionId, comment } }, { ...RAW, errorMessageMode: 'none' });

export const rejectReview = (versionId: string, comment: string) =>
  defHttp.post({ url: '/agent-api/workflow/review/reject', data: { versionId, comment } }, { ...RAW, errorMessageMode: 'none' });

export const cancelReview = (versionId: string) =>
  defHttp.post({ url: '/agent-api/workflow/review/cancel', data: { versionId } }, { ...RAW, errorMessageMode: 'none' });

// 后台跨用户管理（平台管理员）
export const queryAdminAppPage = (params: {
  pageNo?: number;
  pageSize?: number;
  keyword?: string;
  aiAppType?: string;
  aiAppTypes?: string;
  status?: string;
  ownerUserId?: string;
}) => defHttp.get<WorkflowPageResult<AdminAppItem>>({ url: '/agent-api/workflow/admin/app/page', params }, { ...RAW, errorMessageMode: 'none' });

export const adminUnpublishApp = (id: string) =>
  defHttp.post<AdminAppItem>({ url: '/agent-api/workflow/admin/app/unpublish', params: { id } }, { ...RAW, joinParamsToUrl: true });

export const adminDeleteApp = (id: string) =>
  defHttp.delete({ url: '/agent-api/workflow/admin/app/delete', params: { id } }, { ...RAW, joinParamsToUrl: true });

export const adminRollbackApp = (appId: string, versionNo: number) =>
  defHttp.post({ url: '/agent-api/workflow/admin/app/rollback', data: { appId, versionNo } }, { ...RAW, errorMessageMode: 'none' });

export const queryAdminAppDetail = (appId: string) =>
  defHttp.get<AdminAppDetail>(
    { url: `/agent-api/workflow/admin/app/${encodeURIComponent(appId)}/detail` },
    { ...RAW, errorMessageMode: 'none' },
  );

export const queryAdminAppMetrics = (appId: string, range: MetricRange) =>
  defHttp.get<WorkflowAppMetrics>(
    { url: `/agent-api/workflow/admin/app/${encodeURIComponent(appId)}/metrics`, params: { range } },
    { ...RAW, errorMessageMode: 'none' },
  );

export const queryAdminVersionDiff = (appId: string, baseVersionNo: number, targetVersionNo: number) =>
  defHttp.get<WorkflowVersionDiff>(
    { url: `/agent-api/workflow/admin/app/${encodeURIComponent(appId)}/version-diff`, params: { baseVersionNo, targetVersionNo } },
    { ...RAW, errorMessageMode: 'none' },
  );

export const adminRestoreApp = (appId: string, reason?: string) =>
  defHttp.post({ url: '/agent-api/workflow/admin/app/restore', data: { appId, reason } }, { ...RAW, errorMessageMode: 'none' });

export const adminTransferAppOwner = (data: {
  appId: string;
  targetUserId: string;
  retainPreviousOwnerAsEditor: boolean;
  reason?: string;
}) => defHttp.post({ url: '/agent-api/workflow/admin/app/transfer-owner', data }, { ...RAW, errorMessageMode: 'none' });

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

export const executeWorkflowDefinition = (data: {
  appId?: string;
  appInfoId?: string;
  input?: string;
  variables?: Recordable;
}) => defHttp.post<WorkflowRunResponse>({ url: Api.executeDefinition, data }, RAW);

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

export type McpDiscoveredTool = { name: string; description: string; inputSchema: Recordable };

/** 服务端连接外部 MCP Server 并解析工具清单（含 SSRF 防护） */
export const discoverMcpTools = (data: { url: string; headers?: Record<string, string> }) =>
  defHttp.post<{ tools: McpDiscoveredTool[] }>(
    { url: '/agent-api/workflow/tool/mcp/discover', data },
    { ...RAW, errorMessageMode: 'none' }
  );

export type HttpToolTestResult = {
  ok: boolean;
  statusCode: number;
  durationMs: number;
  headers: Record<string, string>;
  body: string;
  truncated: boolean;
};

export async function testHttpToolRequest(data: {
  config: HttpToolSetConfig;
  toolName: string;
  values: Record<string, unknown>;
  files: Record<string, File>;
}): Promise<HttpToolTestResult> {
  const response = await fetch('/agent-api/workflow/tool/http/test', {
    method: 'POST',
    headers: buildWorkflowAppUploadHeaders(getToken()),
    body: buildHttpToolTestFormData(data.config, data.toolName, data.values, data.files),
  });
  const result = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(typeof result?.detail === 'string' ? result.detail : `测试请求失败（HTTP ${response.status}）`);
  }
  return result as HttpToolTestResult;
}

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
