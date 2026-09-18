import { defHttp } from '/@/utils/http/axios';
import type { KnowledgePermission } from '../knowledge/knowledge.types';
import { agentAuthHeaders, resolveAgentAccessToken } from './utils/agentAuthHeaders';
import { createSequenceGate } from './utils/sseSequence';
import { parseResearchTeam, type ResearchTeamSnapshot } from './utils/researchTeam';
import { isAssistantPreset, type AssistantPreset } from './builtinAssistants';
import type { InterviewInput } from './builtinAssistants/interview/types';

export type AgentAuthConfig = {
  baseUrl?: string;
  token?: string;
};

export type AgentChatMessage = {
  role: 'user' | 'assistant' | 'system';
  content: string;
};

export type AgentModelItem = {
  id: string;
  name: string;
  is_default?: boolean;
};

export type WorkflowModelOption = {
  label: string;
  value: string;
  available?: boolean;
};

export type AgentItem = {
  id: string;
  name: string;
  description?: string;
  icon?: string;
  category?: string;
  is_recommend?: boolean;
  status?: number;
};

export type SkillItem = {
  id: string;
  recordId?: string;
  skillId?: string;
  name: string;
  description?: string;
  icon?: string;
  enabled?: boolean;
  version?: string;
  author?: string;
  source?: string;
  installStatus?: string;
};

export type KnowledgeSelection = {
  id: string;
  name: string;
  permission?: KnowledgePermission;
};

export type ThreadItem = {
  id: string;
  title: string;
  model?: string;
  assistant_preset?: AssistantPreset;
  pinned?: boolean;
  created_at?: string;
  updated_at?: string;
  active_run?: ActiveRun | null;
};

export type ThreadScope = 'ordinary' | AssistantPreset;

export type BuiltinAppItem = {
  id: string;
  appName: string;
  appRemark?: string;
  appIcon?: string;
  appCategory?: string;
  appCategory_dictText?: string;
  appType: 'builtin';
  aiAppType?: string;
  pcUrl: string;
  h5Url?: string;
  openType?: '_blank';
  status: number;
  orderNum?: number;
  formOptions?: string;
  builtinPreset: AssistantPreset;
  runtimeKind: 'harness_builtin';
  systemManaged: true;
  createBy?: string;
  createByName?: string;
  createByAvatar?: string;
  createTime?: string;
};

/** composer 「最近的对话」选中项（2026-07-28）：只带 id + 标题，转录正文由后端按归属渲染。 */
export type ThreadReference = {
  id: string;
  title: string;
};

export type ActiveRun = {
  id: string;
  status: string;
  model?: string;
  kind?: string;
  agent_mode?: 'standard' | 'plan' | 'research';
  phase?: string;
  interactive_type?: string;
  plan?: Record<string, any>;
  goal_contract?: Record<string, any>;
  approved_plan_version?: number;
  subagent_id?: string;
  resume_token?: string;
  started_at?: string;
  completed_at?: string;
};

export type { AssistantPreset } from './builtinAssistants';

export type SubagentItem = {
  id: string;
  name: string;
  description?: string;
  icon?: string;
  type?: string;
  scope?: 'owned' | 'shared';
};

function normalizeBaseUrl(url?: string) {
  return String(url || '').trim().replace(/\/$/, '');
}

function getAgentApiConfig(): AgentAuthConfig {
  return {
    baseUrl: '/agent-api',
    token: resolveAgentAccessToken(),
  };
}

function getHeaders(_config: AgentAuthConfig): Record<string, string> {
  // 有 token 时只带 token；细节见 utils/agentAuthHeaders.ts
  return agentAuthHeaders({ 'Content-Type': 'application/json' });
}

function apiErrorMessage(data: unknown, fallback: string): string {
  if (!data || typeof data !== 'object') return fallback;
  const payload = data as { detail?: unknown; message?: unknown };
  const detail = payload.detail ?? payload.message;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (typeof item === 'string') return item;
      if (!item || typeof item !== 'object') return '';
      const issue = item as { loc?: unknown; msg?: unknown; message?: unknown };
      const message = String(issue.msg ?? issue.message ?? '').trim();
      const loc = Array.isArray(issue.loc) ? issue.loc : [];
      const attachmentIndex = loc[0] === 'body' && loc[1] === 'attachments' && typeof loc[2] === 'number'
        ? loc[2]
        : (loc[0] === 'attachments' && typeof loc[1] === 'number' ? loc[1] : undefined);
      if (attachmentIndex !== undefined) return `第 ${attachmentIndex + 1} 个附件信息格式不正确${message ? `：${message}` : ''}`;
      return message;
    }).filter(Boolean);
    if (messages.length) return messages.join('；');
  }
  if (detail && typeof detail === 'object') {
    const nested = detail as { message?: unknown; msg?: unknown };
    const message = nested.message ?? nested.msg;
    if (typeof message === 'string' && message.trim()) return message;
  }
  return fallback;
}

export async function requestAgentApi<T>(path: string, init?: RequestInit): Promise<T> {
  const config = getAgentApiConfig();
  const baseUrl = normalizeBaseUrl(config.baseUrl);
  const url = `${baseUrl}${path}`;

  const response = await fetch(url, {
    ...init,
    headers: {
      ...getHeaders(config),
      'X-Harness-Protocol-Version': '1',
      ...(init?.headers || {}),
    },
  });

  if (!response.ok) {
    let message = `请求失败：${response.status}`;
    try {
      const data = await response.json();
      message = apiErrorMessage(data, message);
    } catch {
      // Keep the status-based message when the response is not JSON.
    }
    // 带上 HTTP 状态码：调用方需要区分「业务性拒绝」和「真错误」——例如运行中引导撞上
    // 409（本轮已结束）应当安静地退回排队，而不是弹一个红色报错。
    throw Object.assign(new Error(message), { status: response.status });
  }

  return response.json();
}

function threadPath(path: string, scope?: ThreadScope): string {
  if (!scope) return path;
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}scope=${encodeURIComponent(scope)}`;
}

function normalizeBuiltinApp(item: any): BuiltinAppItem | null {
  if (!item || !isAssistantPreset(item.builtinPreset)) return null;
  const id = String(item.id || '').trim();
  const pcUrl = String(item.pcUrl || '').trim();
  if (!id || !pcUrl) return null;
  return {
    ...item,
    id,
    appName: String(item.appName || '系统内置应用'),
    appRemark: item.appRemark ? String(item.appRemark) : undefined,
    appIcon: item.appIcon ? String(item.appIcon) : undefined,
    appCategory: item.appCategory ? String(item.appCategory) : undefined,
    appCategory_dictText: item.appCategory_dictText ? String(item.appCategory_dictText) : undefined,
    appType: 'builtin',
    pcUrl,
    h5Url: item.h5Url ? String(item.h5Url) : undefined,
    status: Number(item.status || 0),
    orderNum: item.orderNum == null ? undefined : Number(item.orderNum),
    builtinPreset: item.builtinPreset,
    runtimeKind: 'harness_builtin',
    systemManaged: true,
  };
}

/** 智能体广场里的内置智能体（校园百事通 / 面试助手 / 演示文稿助手）。
 *  上架记录由 agent-api 的 app_info 提供；管理员停用后自然不再返回。 */
export async function listBuiltinApps(): Promise<BuiltinAppItem[]> {
  const data = await requestAgentApi<BuiltinAppItem[]>('/chat/builtin-apps');
  return Array.isArray(data) ? data : [];
}

export async function getBuiltinApp(preset: AssistantPreset): Promise<BuiltinAppItem> {
  const data: any = await requestAgentApi(
    `/chat/builtin-apps/${encodeURIComponent(preset)}`,
    { method: 'GET' },
  );
  const item = normalizeBuiltinApp(data);
  if (!item) throw new Error('系统内置应用信息不完整');
  return item;
}

async function requestAgentApiStream(path: string, init?: RequestInit): Promise<Response> {
  const config = getAgentApiConfig();
  const baseUrl = normalizeBaseUrl(config.baseUrl);
  const url = `${baseUrl}${path}`;

  const response = await fetch(url, {
    ...init,
    headers: {
      ...getHeaders(config),
      'X-Harness-Protocol-Version': '1',
      ...(init?.headers || {}),
    },
  });

  if (!response.ok) {
    let message = `请求失败：${response.status}`;
    try {
      const data = await response.json();
      message = apiErrorMessage(data, message);
    } catch {
      // Keep the status-based message when the response is not JSON.
    }
    // 带上 HTTP 状态码：调用方需要区分「业务性拒绝」和「真错误」——例如运行中引导撞上
    // 409（本轮已结束）应当安静地退回排队，而不是弹一个红色报错。
    throw Object.assign(new Error(message), { status: response.status });
  }

  return response;
}

/** kind 区分两类语义不同的用量事件（后端 2026-07-26 起下发；旧事件无该字段）：
 *  estimate=模型调用前的本地估算；actual=模型返回的真实 prompt_tokens（对同一份 prompt 的
 *  校准修正）。二者差值是估算误差、不是上下文增长——计量行只能用 actual 建基线算增量。 */
export type ContextUsage = {
  tokens: number; window: number; ratio: number; kind?: 'estimate' | 'actual';
};

export type InteractivePayload = {
  run_id?: string;
  resume_id?: string;
  type?: string; // userSelect | formInput
  params?: any;
  /** ask_user_choice 消歧提问卡：直接打字=自由回答（后端转交 resume），发新消息时前端收起本卡 */
  ask_user?: boolean;
  kind?: 'clarification' | 'plan_confirmation';
  revision_gate?: boolean;
  plan_version?: number;
  approved_version?: number;
  plan_steps?: Array<{
    key?: string;
    title: string;
    status?: string;
    detail?: string;
    acceptance?: string;
  }>;
  previous_steps?: Array<{
    key?: string;
    title: string;
    status?: string;
    detail?: string;
    acceptance?: string;
  }>;
  goal_contract?: {
    goal?: string;
    deliverable?: string;
    success_criteria?: string[];
    forbidden?: string[];
    budget_hint?: string;
  };
  /** 挂起来源子智能体身份（后端 input.required 直带，事件回放同样携带）。
   *  @ 模式不产生 subagentCalls chip，只能靠这两个字段标注来源；消歧卡两字段为空不下发。 */
  subagent_id?: string;
  subagent_name?: string;
};

export type CitationSource = {
  type?: string;
  title?: string;
  url?: string;
  source?: string;
  snippet?: string;
  /** type=image 的缩略图直链（可选） */
  thumbnail?: string;
};

/** 搜索附带图片（tool.completed meta 实时下发 + citations type=image 持久化）：
 *  正文 [图N] 标记按 index 反查，渲染成图片卡（图文混排）。 */
export type ImageSource = {
  /** 全消息全局编号（[图N] 的 N），后端跨多次搜索递增 */
  index?: number;
  url: string;
  thumbnail?: string;
  title?: string;
  /** 图片所在页面 URL（图卡脚注跳转） */
  source?: string;
};

export type GeneratedFile = {
  id: string;
  filename: string;
  mime?: string;
  size: number;
  source?: 'uploaded' | 'generated' | 'material' | 'research';
  expiresAt?: string | null;
  createdAt?: string | null;
  /** 产物审查 v1（Phase C）：结构化硬校验结论；无该字段=未审查（旧消息/审查关闭） */
  review?: { status: 'passed' | 'warning' | 'failed' | 'unknown'; summary?: string } | null;
  /** 版本号（Phase B）：本次写入产生的版本；v1 不展示 */
  versionNo?: number;
  /** 审查未通过存为草稿版（§4.3.4）：当前文件保持上一版，草稿在版本历史可下载 */
  draft?: boolean;
  /** 产物血缘（P1）：由哪个 Run/工具/技能生成，文件卡展示来源并支持跳回执行步骤 */
  origin?: { runId?: string; tool?: string; skills?: string[] } | null;
  /**
   * 交付物判据（2026-07-27 用户拍板，后端 files/deliverable.py 算好下发）：
   * false=生成脚本之类的过程文件，落库了但不出产物卡。旧消息没有这个字段，
   * 前端按后缀兜底（见 `isDeliverableFile`），历史记录同样干净。
   */
  deliverable?: boolean;
};

export type ResearchProgressPayload = {
  team?: ResearchTeamSnapshot;
  stage?: string;
  topic?: string;
  topicIndex?: number;
  topicTotal?: number;
  sourcesFound?: number;
  searchCalls?: number;
  citationCount?: number;
  label?: string;
};

const GENERATED_FILE_SOURCES = new Set(['uploaded', 'generated', 'material', 'research']);

function mapGeneratedFileSource(item: any, eventSource?: string): GeneratedFile['source'] {
  const raw = String(item?.source || eventSource || 'generated');
  if (GENERATED_FILE_SOURCES.has(raw)) return raw as GeneratedFile['source'];
  return 'generated';
}

function mapSavedArtifactFiles(files: any[], eventSource?: string): GeneratedFile[] {
  return (Array.isArray(files) ? files : [])
    .filter((item: any) => item && item.id && item.filename)
    .map((item: any) => ({
      id: String(item.id),
      filename: String(item.filename),
      size: Math.max(0, Number(item.size) || 0),
      mime: item.mime ? String(item.mime) : undefined,
      source: mapGeneratedFileSource(item, eventSource),
      review: item.review && typeof item.review === 'object' ? item.review : undefined,
      origin: item.origin && typeof item.origin === 'object' ? item.origin : undefined,
      deliverable: item.deliverable !== false && !/\.research\.md$/i.test(String(item.filename || '')),
    }));
}

/** 附件读取置信度（attachments.status 事件，P0 附件生命周期）：未完整读取的附件 */
export type AttachmentIssue = {
  filename: string;
  kind?: string;
  status: 'partial' | 'failed';
  note?: string;
  file_id?: string;
};

export type ToolStepEvent = {
  phase: 'started' | 'progress' | 'completed' | 'failed';
  name: string;
  /** Responses function call identity; exact pairing key for concurrent same-name tools. */
  callId?: string;
  preview?: string;
  error?: string;
  /** 工具入参摘要（如 search_web 的搜索词），思考时间线上随动作展示 */
  detail?: string;
  /** 模型现写的本次调用意图（任务语言一句话，args.intent）：执行流行标题优先用它 */
  intent?: string;
  /** 实际执行的命令全文（bash 的 args.command，cap 4000）：可展开输出面板展示 */
  command?: string;
  /** v1 事件的服务端时间，用于断线重连后继续计时 */
  timestamp?: number;
  /** 长任务当前真实阶段；不伪造百分比 */
  stage?: string;
  label?: string;
  elapsedMs?: number;
  heartbeat?: boolean;
  /** tool.progress 的结构化载荷（stage=artifact_page 逐页直播：index/total/title/svg） */
  progressDetail?: { index?: number; total?: number; title?: string; svg?: string; html?: string };
  /** 结构化元信息（tool.completed 随带）：search_web 的结果数/来源 URL/已读页面/附带图片 */
  meta?: {
    /** 可观察行动事实：用于 Codex 式“已读取/已编辑 文件 +N -N”行。 */
    action?: {
      operation?: 'read' | 'list' | 'search' | 'edit' | 'create' | 'execute' | string;
      target?: string;
      file_id?: string;
      added?: number;
      removed?: number;
    };
    count?: number;
    urls?: string[];
    read?: Array<{ title: string; url: string }>;
    /** 本次搜索新收集的图片（带全局编号 index，[图N] 反查） */
    images?: ImageSource[];
    files?: GeneratedFile[];
    /** 浏览器工具的页面快照（data: URI，只给人看）。缺这个字段时 executionTimeline
     *  仍照读不误，只有 vue-tsc 会报 TS2339——jest 的 isolatedModules 只转译不查类型、
     *  vite build 也不拦，于是漏了整整一批才被发现。 */
    shot?: string;
    output_count?: number;
    exit_code?: number | null;
    review_status?: 'passed' | 'warning' | 'failed' | 'unknown' | string;
  };
};

/** 模型已经生成、即将真实执行的工具调用清单；不包含隐藏思维或大段参数正文。 */
export type PlanUpdateEvent = {
  round: number;
  items: Array<{ key: string; name: string; detail?: string }>;
};

/** 模型拆解的语义任务步骤（task.plan，Codex 式 update_plan）：整表回传的高层步骤 + 状态，
 *  面向用户可读（理解需求/制作/审查/交付…），供「任务协作」渲染，进行中转圈、完成打勾。 */
export type TaskPlanStatus = 'pending' | 'running' | 'completed' | 'skipped' | 'invalidated';
export type TaskPlanEvent = {
  goal_revision?: number;
  plan_version?: number;
  approved_version?: number;
  diverged?: boolean;
  goal_contract?: {
    goal?: string;
    deliverable?: string;
    success_criteria?: string[];
    forbidden?: string[];
    budget_hint?: string;
  };
  steps: Array<{
    key?: string;
    title: string;
    status: TaskPlanStatus;
    detail?: string;
    acceptance?: string;
    blocked?: boolean;
  }>;
};

export type RouteSelected = { subagent_id: string; name: string };
/** R5 消歧：匹配到多个候选智能体，交用户选择（clarification.required 事件）。
 *  skill_ids/attachments 回放原轮一次性上下文（P2a）：重订阅时前端本地已无原轮快照，
 *  据此在点选重发时精确复用原轮技能/附件（附件为文本 ref，无 image_url data URL）。 */
export type Clarification = {
  prompt: string;
  options: { id: string; name: string }[];
  skill_ids?: string[];
  attachments?: Array<{ filename: string; kind?: string; text: string }>;
};
/** 敏感工具审批（approval.required 事件，§11）：用户通过后同幂等键重试即执行 */
export type ApprovalRequest = { call_id: string; tool_name?: string; prompt?: string };
/** R6 外部应用推荐（recommendation 事件，§8.4）：前往使用卡，不派发 */
export type Recommendation = { id: string; name: string; description?: string; url?: string };
/** call_subagent 编排（subagent.* 事件，ADR-046）：主模型自主调用子智能体的生命周期 +
 *  内部干活流程（node/delta/reasoning，供「子智能体工作窗口」实时展示） */
export type SubagentStepEvent = {
  phase: 'preparing' | 'started' | 'completed' | 'failed' | 'node' | 'delta' | 'reasoning' | 'review';
  id: string;
  name: string;
  /** 委派瞬间经服务端 ACL/发布态校验的工作台头像；用于历史运行档稳定回放。 */
  icon?: string;
  preview?: string;
  error?: string;
  /** 委派的任务描述（started 随带，截断）：时间线上展示"主对话让子智能体做什么" */
  task?: string;
  /** node 阶段：工作流节点名 + 状态（success/failed/…） */
  label?: string;
  status?: string;
  /** delta 阶段：子智能体节点的公开输出文本（增量） */
  text?: string;
  // ---- 执行团队一期（2026-07-27）----
  /** started/completed：模型按场景生成的岗位名（委派时冻结） */
  roleName?: string;
  /** started：AXIOM Agent 在本次任务里的场景化身份（执行团队主管卡标题） */
  managerRole?: string;
  /** started：该成员的子任务清单 */
  subtasks?: string[];
  /** started：委派时的验收标准 */
  acceptanceCriteria?: string[];
  /** review：验收单逐条裁定；completed：验收摘要 */
  review?: {
    verdicts: Array<{ criterion: string; passed: boolean | null; evidence?: string }>;
    passedCount: number;
    total: number;
  };
  acceptance?: { passedCount: number; total: number };
  /** completed：子智能体已落库产物回执（必须有 file id） */
  files?: GeneratedFile[];
};

export type RecommendAgentsPayload = {
  ids: string[];
  intent?: 'explicit_request' | 'capability_gap';
  confidence?: string;
  /** 服务端根据真实命中字段生成的推荐理由，key 为智能体 id。 */
  reasons?: Record<string, string>;
};

export type ModelConnectionPayload = {
  status: 'recovering' | 'recovered' | 'failed';
  transport?: string;
  attempt?: number;
  maxRetries?: number;
  delaySeconds?: number;
};

type RunStartedPayload = {
  run_id: string;
  thread_id: string;
  /** 202 only binds the durable Run; the worker's SSE event confirms execution. */
  status?: 'created' | 'running';
  timestamp?: number;
  agent_mode?: string;
  model?: string;
  resume_meta?: Record<string, unknown>;
};

type StreamCallbacks = {
  onDelta?: (delta: string, content: string) => void;
  /** 当前连接的 reasoning_content 增量；不进入正文。 */
  onReasoningDelta?: (delta: string, reasoning: string) => void;
  /** 当前 reasoning burst 已结束；时间线收束为可展开 Thought 正文。 */
  onReasoningCompleted?: (payload?: { text?: string; seconds?: number }) => void | Promise<void>;
  /** 当前 SSE 连接已经关闭；无论正常、断线或取消，都必须撤下瞬时尾窗。 */
  onReasoningConnectionEnd?: () => void | Promise<void>;
  /** message.completed 携带数据库中的权威全文；调用方必须立即定稿，不能等待视觉动画追赶。 */
  onMessageCompleted?: (content: string) => void;
  /** 过程说明（message.commentary）：刚流式下发过的这段正文其实是开场白/衔接语，不属于
   *  最终回答——content 已在本层从累积中剔除，回调携带（说明文本, 剔除后的正文全文）。 */
  onCommentary?: (text: string, content: string, kind?: string, evidence?: {
    evidenceItemIds: string[];
    nextAction?: string;
  }) => void | Promise<void>;
  /** 模型上游流断开后的自动恢复状态；平台事实，不属于模型正文或 commentary。 */
  onModelConnection?: (payload: ModelConnectionPayload) => void;
  onRunPhase?: (phase: 'waiting_system' | 'running') => void;
  onRunStarted?: (payload: RunStartedPayload) => void;
  onRunCompleted?: (payload: { message_id?: number; timestamp?: number }) => void;
  onRunPartial?: (payload: { message_id?: number; timestamp?: number; reason_codes?: string[] }) => void;
  onRunCancelled?: (payload: { timestamp?: number; reason?: string }) => void;
  onThreadId?: (threadId: string) => void;
  onMessageId?: (id: number) => void;
  /** 当轮用户消息落库 id（message.user_saved，F2）：补 dbId 作后续编辑重发的截断点 */
  onUserMessageId?: (id: number) => void;
  /** 运行中引导已生效（input.applied）：用户在执行过程中提交的引导被
   *  当前轮就地吸收（不中断、不另起一轮）。scope：turn=轮级注入 / graph=图级重规划。 */
  onInputApplied?: (payload: { inputId: string; content: string; scope: string }) => void;
  onInputRejected?: (payload: { inputId: string; content: string; reason: string }) => void;
  onContextUsage?: (usage: ContextUsage) => void;
  onInteractive?: (payload: InteractivePayload) => void;
  onCitations?: (sources: CitationSource[]) => void;
  onPlanUpdate?: (event: PlanUpdateEvent) => void;
  onTaskPlan?: (event: TaskPlanEvent) => void;
  onCapabilityLoaded?: (names: string[]) => void;
  onArtifactSaved?: (payload: { source?: string; files: GeneratedFile[] }) => void;
  onResearchProgress?: (payload: ResearchProgressPayload) => void;
  onToolEvent?: (ev: ToolStepEvent) => void;
  onSubagentStep?: (ev: SubagentStepEvent) => void;
  onRouteSelected?: (route: RouteSelected) => void;
  onClarification?: (payload: Clarification) => void;
  onApproval?: (payload: ApprovalRequest) => void;
  onRecommendation?: (items: Recommendation[]) => void;
  /** 结构化智能体推荐卡：前端仅使用服务端已校验的 id 和命中理由，
   *  不依赖模型 [[RECOMMEND]] 标记，也不在本地编造能力说明。 */
  onRecommendAgents?: (payload: RecommendAgentsPayload) => void;
  /** 用户输入 CAS 消费成功（input.accepted 帧）：「本次提交不可回滚」的
   *  权威信号——2xx 响应头只是传输层接收，令牌实际在后台生成器里才 CAS 消费；
   *  断连回滚判定必须以本回调（而非 onStreamStart）为准。 */
  onInputAccepted?: () => void;
  /** 附件读取置信度（attachments.status，P0）：本轮未完整读取的附件清单，UI 渲染降级提示条。 */
  onAttachmentsStatus?: (items: AttachmentIssue[]) => void;
  /** Codex ContextCompaction 过程（context.compaction）：started 出 Compacting context。 */
  onCompaction?: (payload: { status: string; seconds?: number }) => void;
  /** 自动压缩已发生（context.compacted，§13）：系统已整理较早对话以继续。 */
  onCompacted?: (note: string) => void;
  /** 已更新记忆（memory.updated，§14 Phase 2）：上一轮抽取新记的记忆摘要。
   *  ⚠️ 2026-07-28 起**主对话不再订阅它**——用户拍板「更新记忆这里不用显示」，那张 chip
   *  已从 MessageList 移除（记忆功能本身照常工作，只是不在对话里逐轮报账）。
   *  这里刻意保留回调作为扩展点：没人传就什么都不发生，想恢复 chip 加一个 handler 即可。
   *  **不是漏接线**。 */
  onMemoryUpdated?: (items: string[]) => void;
  /** run.failed / error 事件：区别于正常回复，供 UI 标红并避免误判为成功。 */
  onError?: (message: string) => void;
  /** 每个被接受的 v1 事件的 sequence（严格递增）。订阅方持有 lastSequence，
   *  断线重连用 ?after=lastSequence 游标续传——避免全量回放把 tool/subagent
   *  事件重复灌进 reducer（时间线/工作卡重复）。 */
  onSequence?: (sequence: number) => void;
  /** 事件游标出现断层；调用方必须重拉权威 Run/Plan 快照。 */
  onSequenceGap?: (gap: { expected: number; received: number }) => void;
  /** 传输结束（TCP EOF/[DONE] 后流关闭）回调：报告本次连接是否见过**业务终态/挂起信号**
   *  （run.completed / run.failed / error / input.required / approval.required）。
   *  [DONE] 与 EOF 只是传输结束——sawTerminal=false 的正常 EOF＝断流而非完成，
   *  调用方据此移交订阅通道续接，绝不能当成功收尾（整合路线图 #2 补强）。 */
  onStreamEnd?: (info: StreamEndInfo) => void;
};

/** readChatStream 传输结束时的业务终态标记：见 StreamCallbacks.onStreamEnd。 */
export type StreamEndInfo = { sawTerminal: boolean };

/**
 * 从正文累积中剔除过程说明（或其流式残段）。
 * 与后端 turn_finalizer.peel_commentary_from_answer 对齐：
 * stream 可能已剥「我来…。」，正文只剩 commentary 后缀「先建立研究计划…」——
 * 仅 endsWith(全文) 会剥失败，残留假终答 +「正在整理研究结论…」。
 */
export function peelCommentaryFromContent(content: string, commentary: string): string {
  const ans = String(content || '');
  const text = String(commentary || '');
  if (!ans) return ans;

  const dropSuffix = (hay: string, suf: string): string | null => {
    if (suf && hay.endsWith(suf)) return hay.slice(0, hay.length - suf.length);
    return null;
  };

  // 轻量镜像后端 strip_leading_mechanical_ack：剥开场 + 纯过程规划句
  const stripMech = (raw: string): string => {
    let s = String(raw || '').replace(/^\s+/, '');
    if (!s) return raw;
    for (let i = 0; i < 4; i += 1) {
      const before = s;
      const ack = s.match(
        /^(?:收到[啦了]?|好的|好嘞|没问题|当然可以|当然|马上|这就|稍等[一下]?|明白[了]?|了解|已知悉|已收到)[，,。.!！]?\s*/,
      );
      if (ack) {
        const rest = s.slice(ack[0].length).replace(/^\s+/, '');
        if (rest) s = rest;
        else break;
      }
      const proc = s.match(
        /^(?:好的[，,]?)?(?:(?:我来|让我来|我先来|我先去|我先|我同时|我马上|我这就|稍等我|等我)(?:帮你)?(?:去)?(?:查|搜|看|找|处理|生成|写|动手|检索|核对|问|了解|调研|研究)?[^。！？\n]{0,80}|我先从现有材料入手[^。！？\n]{0,64}|我先动手[：:][^。！？\n]{0,64}|接下来(?:会)?(?:先)?(?:帮你)?(?:查|搜|看|核对|检索|查找|建立|制定)[^。！？\n]{0,64}|先查找可核验资料[^。！？\n]{0,48}|先(?:建立|制定|梳理|列出|做好|准备)(?:一个|一份|好)?[^。！？\n]{0,40}(?:研究|调研)?计划[^。！？\n]{0,64}|先(?:做|写|列)(?:一个|一份|好)?[^。！？\n]{0,24}(?:大纲|提纲|步骤|计划)[^。！？\n]{0,48}|(?:然后)?(?:再)?(?:多角度|全面|系统地?)?(?:检索|搜集|查找|搜索)(?:相关)?(?:资料|信息|数据)[^。！？\n]{0,32})[。.!！]\s*/,
      );
      if (proc) {
        const rest2 = s.slice(proc[0].length).replace(/^\s+/, '');
        s = rest2;
        if (!rest2) return '';
      }
      if (s === before) break;
    }
    return s;
  };

  if (text) {
    const exact = dropSuffix(ans, text);
    if (exact != null) return exact;
    let cur = text;
    for (let i = 0; i < 4; i += 1) {
      const nxt = stripMech(cur);
      if (nxt === cur) break;
      const dropped = dropSuffix(ans, nxt);
      if (dropped != null) return dropped;
      cur = nxt;
    }
    const ast = ans.trim();
    if (ast && text.replace(/\s+$/, '').endsWith(ast)) {
      const idx = ans.lastIndexOf(ast);
      if (idx >= 0) return ans.slice(0, idx);
    }
  }

  if (ans.trim() && !stripMech(ans).trim()) return '';
  return ans;
}

/** readChatStream 的续传选项：游标重连时事件从 afterSequence 之后开始，
 *  正文累积器以 initialContent 播种（message.delta 只发增量，不播种会让全文覆写只剩尾巴）。 */
type StreamResumeOptions = {
  afterSequence?: number;
  initialContent?: string;
};

async function readChatStream(
  response: Response,
  cb: StreamCallbacks = {},
  resume: StreamResumeOptions = {},
) {
  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error('聊天接口未返回可读取的流式内容');
  }

  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let content = resume.initialContent || '';
  let reasoning = '';
  // 业务终态跟踪（断流≠完成）：[DONE]/TCP EOF 只是传输结束，业务终态只有 run.completed /
  // run.failed / error / 合法挂起（input.required、approval.required）。流读完后经
  // onStreamEnd 报告给调用方——没见过任何业务信号的正常 EOF 是断流，不能当成功收尾。
  let sawTerminal = false;
  // 去重/防乱序闸：断线重连、回放重发的旧 sequence 一律丢弃，不重复渲染（§15.4）；
  // 游标续传时以 afterSequence 为起点（服务端 ?after= 已过滤，这里是双保险）
  const acceptSequence = createSequenceGate(resume.afterSequence || 0, cb.onSequenceGap);

  const appendPayload = async (payload: string) => {
    if (!payload || payload === '[DONE]') return;
    try {
      const data = JSON.parse(payload);

      // ---- Harness Protocol 1 结构化信封 ----
      if (data?.schema_version === 1 && typeof data.type === 'string') {
        if (!acceptSequence(data.sequence)) return;
        if (typeof data.sequence === 'number') cb.onSequence?.(data.sequence);
        const d = data.data || {};
        if (['message.delta', 'message.reasoning.delta', 'tool.started'].includes(data.type)) cb.onRunPhase?.('running');
        switch (data.type) {
          case 'run.recovery.scheduled':
            cb.onRunPhase?.('waiting_system');
            return;
          case 'run.phase.changed':
            if (d.phase === 'waiting_system' || d.phase === 'running') cb.onRunPhase?.(d.phase);
            return;
          case 'run.accepted':
            return;
          case 'run.started':
            if (d.thread_id) cb.onThreadId?.(String(d.thread_id));
            if (data.run_id) cb.onRunStarted?.({
              run_id: String(data.run_id),
              thread_id: String(d.thread_id || ''),
              status: 'running',
              timestamp: Number(data.timestamp) || undefined,
              agent_mode: String(d.agent_mode || '') || undefined,
              model: String(d.model || '') || undefined,
              // v3.0：续做轮附带「已从上次进度继续」元信息（旧后端/旧轨迹无此字段）
              resume_meta: d.resume_meta && typeof d.resume_meta === 'object'
                ? (d.resume_meta as Record<string, unknown>)
                : undefined,
            });
            return;
          case 'message.delta': {
            const text = String(d.text || '');
            if (text) {
              if (reasoning) {
                reasoning = '';
                await cb.onReasoningCompleted?.();
              }
              content += text;
              cb.onDelta?.(text, content);
            }
            return;
          }
          case 'message.reasoning.delta': {
            const text = String(d.text || '');
            if (text) {
              reasoning += text;
              cb.onReasoningDelta?.(text, reasoning);
            }
            return;
          }
          case 'message.reasoning.completed':
            reasoning = '';
            await cb.onReasoningCompleted?.({
              text: d.text ? String(d.text) : undefined,
              seconds: typeof d.seconds === 'number' ? Number(d.seconds) : undefined,
            });
            return;
          case 'message.commentary': {
            // 事后声明：刚流出的这段是过程说明（开场白/衔接语）——从正文累积中剔除，
            // 保证后续 onDelta 的全文与后端最终落库正文口径一致。
            // v3.01：stream 可能已剥机械开场，正文只剩过程残段，不能只 endsWith 全文。
            const text = String(d.text || '');
            if (text) {
              if (reasoning) {
                reasoning = '';
                await cb.onReasoningCompleted?.();
              }
              content = peelCommentaryFromContent(content, text);
              // kind='plan'：计划报告卡；其他 kind 是可公开的进度说明。
              await cb.onCommentary?.(text, content, String(d.kind || ''), {
                evidenceItemIds: Array.isArray(d.evidence_item_ids)
                  ? d.evidence_item_ids.map(String).filter(Boolean)
                  : [],
                nextAction: d.next_action ? String(d.next_action) : undefined,
              });
            }
            return;
          }
          case 'model.connection': {
            const status = String(d.status || '');
            if (status === 'recovering' || status === 'recovered' || status === 'failed') {
              cb.onModelConnection?.({
                status,
                transport: d.transport ? String(d.transport) : undefined,
                attempt: typeof d.attempt === 'number' ? Number(d.attempt) : undefined,
                maxRetries: typeof d.max_retries === 'number' ? Number(d.max_retries) : undefined,
                delaySeconds: typeof d.delay_seconds === 'number' ? Number(d.delay_seconds) : undefined,
              });
            }
            return;
          }
          case 'message.completed': {
            reasoning = '';
            await cb.onReasoningCompleted?.();
            // 终态帧不是第二次正文 delta。正文已逐 token 流完且与落库文本一致时，
            // 这里只补 message_id；否则把整篇再送进打字机，会在结束瞬间突然重放/跳变。
            // 仅当后端权威文本确有差异时才同步，delta 只传真正新增的后缀。
            const text = String(d.text || '');
            if (text && text !== content) {
              const suffix = text.startsWith(content) ? text.slice(content.length) : '';
              content = text;
              cb.onDelta?.(suffix, content);
            }
            cb.onMessageCompleted?.(content);
            if (d.message_id != null) cb.onMessageId?.(Number(d.message_id));
            return;
          }
          case 'input.applied':
            // 运行中引导已被当前轮吸收（不中断）：前端据此确认这条引导真的生效了
            cb.onInputApplied?.({
              inputId: String(d.input_id || ''),
              content: String(d.content || ''),
              scope: String(d.scope || 'turn'),
            });
            return;
          case 'input.rejected':
            cb.onInputRejected?.({
              inputId: String(d.input_id || ''),
              content: String(d.content || ''),
              reason: String(d.reason || '无法安全合并到当前任务'),
            });
            return;
          case 'message.user_saved':
            if (d.message_id != null) cb.onUserMessageId?.(Number(d.message_id));
            return;
          case 'input.accepted':
            // resume 令牌 CAS 消费成功（第五批项 1）：后台生成器在 thread 帧之后下发的
            // 「不可回滚」权威信号——从此刻起断连走裸 EOF 交接，卡片乐观隐藏不得回滚。
            // 段边界重置（切回乱序修复）：任务模式一个 Run 跨需求/计划/执行多段（resume
            // 续同一 run_id、sequence 续号），?after=0 全量回放会把已挂起段与当前段的
            // delta 串进同一累积器——活流语义本是一段一个气泡（resumeChatTurn 不播种、
            // 写续接消息），回放在此对齐：越过段边界即清零，正文/思考只呈现当前段。
            content = '';
            reasoning = '';
            await cb.onReasoningCompleted?.();
            sawTerminal = false;
            cb.onInputAccepted?.();
            return;
          case 'progress.updated':
            if (d.kind !== 'next_actions') return;
            cb.onPlanUpdate?.({
              round: Math.max(1, Number(d.round) || 1),
              items: Array.isArray(d.items)
                ? d.items
                    .filter((item: any) => item && item.name)
                    .map((item: any, index: number) => ({
                      key: String(item.key || `round-${d.round || 1}-${index}`),
                      name: String(item.name || ''),
                      detail: item.detail ? String(item.detail) : undefined,
                    }))
                : [],
            });
            return;
          case 'plan.updated':
            cb.onTaskPlan?.({
              goal_revision: Math.max(0, Number(d.goal_revision) || 0),
              plan_version: Math.max(0, Number(d.plan_version) || 0),
              approved_version: d.approved_version == null || d.approved_version === ''
                ? undefined
                : Math.max(0, Number(d.approved_version) || 0),
              diverged: Boolean(d.diverged),
              goal_contract: d.goal_contract && typeof d.goal_contract === 'object'
                ? {
                    goal: d.goal_contract.goal ? String(d.goal_contract.goal) : undefined,
                    deliverable: d.goal_contract.deliverable
                      ? String(d.goal_contract.deliverable)
                      : undefined,
                    success_criteria: Array.isArray(d.goal_contract.success_criteria)
                      ? d.goal_contract.success_criteria.map(String)
                      : undefined,
                    forbidden: Array.isArray(d.goal_contract.forbidden)
                      ? d.goal_contract.forbidden.map(String)
                      : undefined,
                    budget_hint: d.goal_contract.budget_hint
                      ? String(d.goal_contract.budget_hint)
                      : undefined,
                  }
                : undefined,
              steps: Array.isArray(d.steps)
                ? d.steps
                    .filter((s: any) => s && s.title)
                    .map((s: any, index: number) => ({
                      key: String(s.key || `plan-${index}`),
                      title: String(s.title || ''),
                      status: (String(s.status) === 'in_progress'
                        ? 'running'
                        : ['pending', 'running', 'completed', 'skipped', 'invalidated'].includes(String(s.status))
                          ? String(s.status)
                          : 'pending') as TaskPlanStatus,
                      detail: s.detail ? String(s.detail) : undefined,
                      acceptance: s.acceptance ? String(s.acceptance) : undefined,
                      blocked: Boolean(s.blocked) || String(s.detail || '').includes('等待上一步'),
                    }))
                : [],
            });
            return;
          case 'capability.loaded':
            cb.onCapabilityLoaded?.(
              Array.isArray(d.names) ? d.names.map(String).filter(Boolean) : [],
            );
            return;
          case 'artifact.saved':
            cb.onArtifactSaved?.({
              source: d.source ? String(d.source) : undefined,
              files: mapSavedArtifactFiles(d.files, d.source ? String(d.source) : undefined),
            });
            return;
          case 'research.team': {
            const team = parseResearchTeam(d.team);
            if (team) cb.onResearchProgress?.({ team });
            return;
          }
          case 'research.progress':
            cb.onResearchProgress?.({
              stage: d.stage ? String(d.stage) : undefined,
              topic: d.topic ? String(d.topic) : undefined,
              topicIndex: Number(d.topicIndex) || 0,
              topicTotal: Number(d.topicTotal) || 0,
              sourcesFound: Number(d.sourcesFound || d.citationCount) || 0,
              searchCalls: Number(d.searchCalls) || 0,
              citationCount: Number(d.citationCount || d.sourcesFound) || 0,
              label: d.label ? String(d.label) : undefined,
              team: parseResearchTeam(d.team),
            });
            return;
          case 'tool.started':
            {
              const args = d.args && typeof d.args === 'object' ? d.args : {};
              // pattern/url 一起取（2026-07-27）：glob 的匹配式、download_url/browser_fetch
              // 的地址原先都取不到，行内 detail 是空的
              // element 补在末尾（2026-07-29）：这是 browser_act 唯一声明它的工具，后端 schema
              // 里写的就是「一句话说明这是什么元素，会展示给用户」。而 target 只在**成功**时才
              // 由 meta.action 落下来（sink 在 ToolSoftError 之后），失败行于是一个字都没有——
              // 「页面操作失败」而不说失败在哪个元素上，等于让用户自己猜点的是哪个按钮。
              const detailValue = args.query ?? args.filename ?? args.path ?? args.pattern
                ?? args.url ?? args.file_id ?? args.description ?? args.element;
              // 实际执行的命令只进可展开面板；不再取首行当 detail——首行几乎总是切目录或
              // 环境准备，行内展示是纯噪音（2026-07-24 拍板）。
              // 唯一执行器 bash 的实际命令只进可展开面板。
              const rawCommand = args.command;
              const command = rawCommand != null && String(rawCommand).trim()
                ? String(rawCommand).slice(0, 4000)
                : undefined;
            cb.onToolEvent?.({
              phase: 'started',
              name: String(d.name || ''),
              callId: d.call_id ? String(d.call_id) : undefined,
              intent: args.intent != null && String(args.intent).trim()
                ? String(args.intent).trim().slice(0, 80)
                : undefined,
              detail: detailValue != null && String(detailValue).trim()
                ? String(detailValue).trim().slice(0, 200)
                : undefined,
              command,
              timestamp: Number(data.timestamp) || undefined,
            });
            return;
            }
          case 'tool.progress':
            cb.onToolEvent?.({
              phase: 'progress',
              name: String(d.name || ''),
              callId: d.call_id ? String(d.call_id) : undefined,
              stage: String(d.stage || ''),
              label: String(d.label || ''),
              elapsedMs: Math.max(0, Number(d.elapsed_ms) || 0),
              heartbeat: Boolean(d.heartbeat),
              timestamp: Number(data.timestamp) || undefined,
              progressDetail: d.detail && typeof d.detail === 'object' ? d.detail : undefined,
            });
            return;
          case 'tool.completed':
            cb.onToolEvent?.({
              phase: 'completed',
              name: String(d.name || ''),
              callId: d.call_id ? String(d.call_id) : undefined,
              preview: d.result_preview,
              meta: d.meta && typeof d.meta === 'object' ? d.meta : undefined,
              timestamp: Number(data.timestamp) || undefined,
            });
            return;
          case 'tool.failed':
            cb.onToolEvent?.({
              phase: 'failed', name: String(d.name || ''), error: d.error,
              callId: d.call_id ? String(d.call_id) : undefined,
              meta: d.meta && typeof d.meta === 'object' ? d.meta : undefined,
              timestamp: Number(data.timestamp) || undefined,
            });
            return;
          case 'subagent.started':
            cb.onSubagentStep?.({
              phase: 'started',
              id: String(d.subagent_id || ''),
              name: String(d.name || ''),
              icon: d.icon ? String(d.icon) : undefined,
              task: d.task ? String(d.task) : undefined,
              // 执行团队一期：岗位名/子任务清单/验收标准（缺省不带）
              roleName: d.role_name ? String(d.role_name) : undefined,
              managerRole: d.manager_role ? String(d.manager_role) : undefined,
              subtasks: Array.isArray(d.subtasks) ? d.subtasks.map(String) : undefined,
              acceptanceCriteria: Array.isArray(d.acceptance_criteria)
                ? d.acceptance_criteria.map(String)
                : undefined,
            });
            return;
          case 'subagent.preparing':
            cb.onSubagentStep?.({
              phase: 'preparing',
              id: String(d.subagent_id || ''),
              name: String(d.name || ''),
              task: d.task ? String(d.task) : undefined,
            });
            return;
          case 'subagent.review':
            // 执行团队一期：验收单逐条裁定（先于 completed 到达）
            cb.onSubagentStep?.({
              phase: 'review', id: String(d.subagent_id || ''), name: String(d.name || ''),
              review: {
                verdicts: Array.isArray(d.verdicts)
                  ? d.verdicts.map((v: any) => ({
                      criterion: String(v?.criterion || ''),
                      passed: typeof v?.passed === 'boolean' ? v.passed : null,
                      evidence: v?.evidence ? String(v.evidence) : undefined,
                    }))
                  : [],
                passedCount: Number(d.passed_count || 0),
                total: Number(d.total || 0),
              },
            });
            return;
          case 'subagent.completed':
            cb.onSubagentStep?.({
              phase: 'completed', id: String(d.subagent_id || ''), name: String(d.name || ''),
              preview: d.result_preview,
              roleName: d.role_name ? String(d.role_name) : undefined,
              acceptance: d.acceptance && typeof d.acceptance === 'object'
                ? {
                    passedCount: Number(d.acceptance.passed_count || 0),
                    total: Number(d.acceptance.total || 0),
                  }
                : undefined,
              files: mapSavedArtifactFiles(d.files, 'generated'),
            });
            return;
          case 'subagent.failed':
            cb.onSubagentStep?.({
              phase: 'failed', id: String(d.subagent_id || ''), name: String(d.name || ''),
              error: d.error,
            });
            return;
          case 'subagent.node':
            cb.onSubagentStep?.({
              phase: 'node', id: String(d.subagent_id || ''), name: '',
              label: String(d.label || ''), status: String(d.status || ''),
            });
            return;
          case 'subagent.delta':
            cb.onSubagentStep?.({
              phase: 'delta', id: String(d.subagent_id || ''), name: '', text: String(d.text || ''),
            });
            return;
          case 'subagent.reasoning':
            cb.onSubagentStep?.({
              phase: 'reasoning', id: String(d.subagent_id || ''), name: '', text: String(d.text || ''),
            });
            return;
          case 'subagent.reasoning.completed':
            cb.onSubagentStep?.({
              phase: 'reasoning', id: String(d.subagent_id || ''), name: '', text: '',
            });
            return;
          case 'input.required':
            sawTerminal = true; // 合法挂起：本轮以卡片收尾，也算「见过业务信号」
            cb.onInteractive?.({ ...d, kind: 'clarification' });
            return;
          case 'plan.confirmation.required':
            sawTerminal = true;
            cb.onInteractive?.({ ...d, kind: 'plan_confirmation' });
            return;
          case 'approval.required':
            sawTerminal = true; // 合法挂起（同 input.required）
            cb.onApproval?.({ call_id: String(d.call_id || ''), tool_name: d.tool_name, prompt: d.prompt });
            return;
          case 'recommendation':
            cb.onRecommendation?.(Array.isArray(d.items) ? d.items : []);
            return;
          case 'recommend_agents':
            cb.onRecommendAgents?.({
              ids: Array.isArray(d.ids) ? d.ids.map(String) : [],
              intent: d.intent === 'explicit_request' ? 'explicit_request' : 'capability_gap',
              confidence: d.confidence ? String(d.confidence) : undefined,
              reasons: d.reasons && typeof d.reasons === 'object'
                ? Object.fromEntries(
                    Object.entries(d.reasons).map(([id, reason]) => [String(id), String(reason)]),
                  )
                : undefined,
            });
            return;
          case 'attachments.status':
            cb.onAttachmentsStatus?.(
              Array.isArray(d.items)
                ? d.items
                    .filter((item: any) => item && item.filename && (item.status === 'partial' || item.status === 'failed'))
                    .map((item: any) => ({
                      filename: String(item.filename),
                      kind: item.kind ? String(item.kind) : undefined,
                      status: item.status as 'partial' | 'failed',
                      note: item.note ? String(item.note) : undefined,
                      file_id: item.file_id ? String(item.file_id) : undefined,
                    }))
                : [],
            );
            return;
          case 'context.compaction':
            cb.onCompaction?.({
              status: String(d.status || 'started'),
              seconds: typeof d.seconds === 'number' ? d.seconds : undefined,
            });
            return;
          case 'context.compacted':
            cb.onCompacted?.(String(d.note || ''));
            return;
          case 'memory.updated':
            cb.onMemoryUpdated?.(Array.isArray(d.items) ? d.items.map(String) : []);
            return;
          case 'context.usage':
            cb.onContextUsage?.(d);
            return;
          case 'citations':
            cb.onCitations?.(Array.isArray(d.sources) ? d.sources : []);
            return;
          case 'route.selected':
            cb.onRouteSelected?.({ subagent_id: String(d.subagent_id || ''), name: String(d.name || '') });
            return;
          case 'clarification.required':
            cb.onClarification?.({
              prompt: String(d.prompt || ''),
              options: Array.isArray(d.options) ? d.options : [],
              skill_ids: Array.isArray(d.skill_ids) ? d.skill_ids.map(String) : undefined,
              attachments: Array.isArray(d.attachments) ? d.attachments : undefined,
            });
            return;
          case 'run.completed':
            reasoning = '';
            await cb.onReasoningCompleted?.();
            sawTerminal = true;
            if (d.message_id != null) cb.onMessageId?.(Number(d.message_id));
            cb.onRunCompleted?.({
              message_id: d.message_id != null ? Number(d.message_id) : undefined,
              timestamp: Number(data.timestamp) || undefined,
            });
            return;
          case 'run.partial':
            reasoning = '';
            await cb.onReasoningCompleted?.();
            sawTerminal = true;
            if (d.message_id != null) cb.onMessageId?.(Number(d.message_id));
            cb.onRunPartial?.({
              message_id: d.message_id != null ? Number(d.message_id) : undefined,
              timestamp: Number(data.timestamp) || undefined,
              reason_codes: Array.isArray(d.reason_codes) ? d.reason_codes.map(String) : undefined,
            });
            return;
          case 'run.cancelled':
            reasoning = '';
            await cb.onReasoningCompleted?.();
            sawTerminal = true;
            cb.onRunCancelled?.({
              timestamp: Number(data.timestamp) || undefined,
              reason: d.reason ? String(d.reason) : undefined,
            });
            return;
          case 'run.failed': {
            reasoning = '';
            await cb.onReasoningCompleted?.();
            sawTerminal = true;
            const msg = String(d.message || '出错了');
            // 作为独立错误信号上报（不拼进正文），供 UI 单独标红并避免误判为成功完成。
            cb.onError?.(msg);
            return;
          }
          default:
            return;
        }
      }
    } catch {
      // Ignore malformed keep-alive chunks.
    }
  };

  // 传输 watchdog（N-06）：服务端约每 15s 发一次 `: ping` 注释帧，只防代理空闲掐断。
  // 注释帧不算业务进度。跨进程订阅若只靠 ping 保活、data 帧被空缓存吞掉，页面会
  // 完全不动、刷新才看到步骤。必须以 data: 帧重置空闲钟，超时按裸 EOF 续订。
  const STREAM_IDLE_TIMEOUT_MS = 45_000;
  let lastDataAt = Date.now();
  const readWithWatchdog = (waitMs: number): Promise<ReadableStreamReadResult<Uint8Array>> => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const readPromise = reader.read();
    // race 输掉的 read 在 cancel 后可能 reject（如取消与网络错误竞态）：挂空 catch 防
    // unhandled rejection；race 本身的胜负路径不受影响
    readPromise.catch(() => {});
    return Promise.race([
      readPromise,
      new Promise<ReadableStreamReadResult<Uint8Array>>((resolve) => {
        timer = setTimeout(() => {
          reader.cancel().catch(() => {});
          resolve({ done: true, value: undefined } as ReadableStreamReadResult<Uint8Array>);
        }, waitMs);
      }),
    ]).finally(() => {
      if (timer != null) clearTimeout(timer);
    });
  };

  try {
    while (true) {
      const remain = STREAM_IDLE_TIMEOUT_MS - (Date.now() - lastDataAt);
      if (remain <= 0) {
        reader.cancel().catch(() => {});
        break;
      }
      const { done, value } = await readWithWatchdog(remain);
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() || '';

      let sawData = false;
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith('data:')) continue;
        sawData = true;
        await appendPayload(trimmed.replace(/^data:\s*/, ''));
      }
      if (sawData) lastDataAt = Date.now();
    }

    const trailing = buffer.trim();
    if (trailing.startsWith('data:')) {
      await appendPayload(trailing.replace(/^data:\s*/, ''));
    }

    // 传输正常结束（含残尾处理后）才报告；中途抛错走调用方 catch，不经此出口。
    cb.onStreamEnd?.({ sawTerminal });
    return content;
  } finally {
    reasoning = '';
    await cb.onReasoningConnectionEnd?.();
  }
}

// Chat API
export async function createAgentChatCompletion(
  params: {
    message: string;
    thread_id?: string;
    workspace_folder_id?: string;
    model?: string;
    skill_ids?: string[];
    selected_skills?: SkillItem[];
    knowledge_ids?: string[];
    selected_knowledge?: KnowledgeSelection[];
    stream?: boolean;
    /** 右侧轻量旁路会话：新建线程不进入主对话历史。 */
    side_chat?: boolean;
    regenerate?: boolean;
    subagent_id?: string;
    web_search?: boolean;
    attachments?: Array<{
      filename: string;
      text: string;
      file_id?: string;
      sha256?: string;
      kind?: string;
      image_url?: string;
      /** 解析置信度（/chat/upload 返回）：后端据此判定降级并持久化附件元数据 */
      status?: string;
      note?: string;
    }>;
    /** composer 选中的「我的文件」id（ADR-047 §6.6）：后端按归属解析为附件文本 */
    file_ids?: string[];
    /** composer 「最近的对话」选中的会话 id（2026-07-28）：后端按归属渲染成转录附件注入本轮 */
    thread_ids?: string[];
    /** 编辑重发（F2）：后端删除本会话 id >= 此值的消息后再走新一轮（线性覆盖） */
    truncate_from_message_id?: number;
    /** 仅选择 Profile；Standard/Plan/Research 共用同一 Harness Kernel。 */
    agent_mode?: 'standard' | 'plan' | 'research';
    /** 平台内置助手预设；只改变会话身份与能力边界，不切换 Harness Kernel。 */
    assistant_preset?: AssistantPreset;
    interview_input?: InterviewInput;
    /** 队列派发轮（§10.6 P0）：pop 认领的队列项 id + 一次性租约凭证。服务端建 Run 前
     *  原子校验绑定、Run 持久化后确认删除；前端不再提前 confirm，端到端不丢不重。 */
    queue_item_id?: string;
    queue_lease_token?: string;
    /** 队列/续做：继承该 Run 的 GoalContract、计划、工作区与 Skill。 */
    resume_source_run_id?: string;
    /** 可靠握手幂等键（N-02）：每次发送生成一次。同键重复 POST 服务端不建第二个 Run；
     *  首帧丢失后凭它 getRunByClientRequest 反查已建 Run，订阅续接而不是重发。 */
    client_request_id?: string;
    signal?: AbortSignal;
    onDelta?: (delta: string, content: string) => void;
    onReasoningDelta?: (delta: string, reasoning: string) => void;
    onReasoningCompleted?: (payload?: { text?: string; seconds?: number }) => void | Promise<void>;
    onReasoningConnectionEnd?: () => void | Promise<void>;
    onMessageCompleted?: (content: string) => void;
    onCommentary?: StreamCallbacks['onCommentary'];
    onModelConnection?: StreamCallbacks['onModelConnection'];
    onRunPhase?: StreamCallbacks['onRunPhase'];
    onRunStarted?: (payload: RunStartedPayload) => void;
    onRunCompleted?: (payload: { message_id?: number; timestamp?: number }) => void;
    onRunPartial?: (payload: { message_id?: number; timestamp?: number; reason_codes?: string[] }) => void;
    onRunCancelled?: (payload: { timestamp?: number; reason?: string }) => void;
    onThreadId?: (threadId: string) => void;
    onMessageId?: (id: number) => void;
    onUserMessageId?: (id: number) => void;
    /** 运行中引导已被当前轮吸收（input.applied）；见 StreamCallbacks 同名项 */
    onInputApplied?: (payload: { inputId: string; content: string; scope: string }) => void;
    onInputRejected?: (payload: { inputId: string; content: string; reason: string }) => void;
    onContextUsage?: (usage: ContextUsage) => void;
    onInteractive?: (payload: InteractivePayload) => void;
    onCitations?: (sources: CitationSource[]) => void;
    onPlanUpdate?: (event: PlanUpdateEvent) => void;
    onTaskPlan?: (event: TaskPlanEvent) => void;
    onCapabilityLoaded?: (names: string[]) => void;
    onArtifactSaved?: (payload: { source?: string; files: GeneratedFile[] }) => void;
    onResearchProgress?: (payload: ResearchProgressPayload) => void;
    onToolEvent?: (ev: ToolStepEvent) => void;
    onSubagentStep?: (ev: SubagentStepEvent) => void;
    onRouteSelected?: (route: RouteSelected) => void;
    onClarification?: (payload: Clarification) => void;
    onApproval?: (payload: ApprovalRequest) => void;
    onRecommendation?: (items: Recommendation[]) => void;
    onRecommendAgents?: (payload: RecommendAgentsPayload) => void;
    onInputAccepted?: () => void;
    onAttachmentsStatus?: (items: AttachmentIssue[]) => void;
    onCompaction?: (payload: { status: string; seconds?: number }) => void;
    onCompacted?: (note: string) => void;
    onMemoryUpdated?: (items: string[]) => void;
    onError?: (message: string) => void;
    /** 已接受事件的 sequence 游标：断流交接订阅通道时按 after= 续传（防事件重复） */
    onSequence?: (sequence: number) => void;
    onSequenceGap?: (gap: { expected: number; received: number }) => void;
    /** 传输结束回调：sawTerminal=false 的正常 EOF＝断流而非完成（见 StreamEndInfo） */
    onStreamEnd?: (info: StreamEndInfo) => void;
  }
) {
  const requestBody: any = {
    message: params.message,
    thread_id: params.thread_id || undefined,
    workspace_folder_id: params.workspace_folder_id || undefined,
    model: params.model || undefined,
    skill_ids: params.skill_ids || undefined,
    knowledge_ids: params.knowledge_ids?.length ? params.knowledge_ids : undefined,
    selected_knowledge: params.selected_knowledge?.map((knowledge) => ({
      id: knowledge.id,
      name: knowledge.name,
      permission: knowledge.permission || '',
    })) || undefined,
    selected_skills: params.selected_skills?.map((skill) => ({
      id: skill.id,
      name: skill.name,
      description: skill.description || '',
      version: skill.version || '',
      author: skill.author || '',
      source: skill.source || '',
    })) || undefined,
    regenerate: params.regenerate || undefined,
    subagent_id: params.subagent_id || undefined,
    web_search: params.web_search || undefined,
    attachments: params.attachments?.length ? params.attachments : undefined,
    file_ids: params.file_ids?.length ? params.file_ids : undefined,
    thread_ids: params.thread_ids?.length ? params.thread_ids : undefined,
    truncate_from_message_id: params.truncate_from_message_id || undefined,
    agent_mode: params.agent_mode || 'standard',
    assistant_preset: params.assistant_preset || undefined,
    interview_input: params.interview_input,
    queue_item_id: params.queue_item_id || undefined,
    queue_lease_token: params.queue_lease_token || undefined,
    resume_source_run_id: params.resume_source_run_id || undefined,
    client_request_id: params.client_request_id || undefined,
    // 主对话只有 Run + 事件流一条执行路径。
    stream: true,
    side_chat: Boolean(params.side_chat),
  };

  if (requestBody.stream) {
    const started: any = await requestAgentApi('/chat/runs', {
      method: 'POST',
      body: JSON.stringify(requestBody),
      signal: params.signal,
      headers: { 'X-Harness-Protocol-Version': '1' },
    });
    if (!started?.run_id) throw new Error('运行任务未被服务端接受');
    if (started.thread_id) params.onThreadId?.(String(started.thread_id));
    // 202 受理边界已有 durable run_id。立刻绑定 runId/threadId（复用 onRunStarted 回调），
    // 避免 worker 尚未发出 run.started 时失败/断流被误判为首帧前失败、错误被盖成默认 503。
    // 语义上这是 accepted 绑定，不是 worker 已开跑；markRunStarted 幂等，SSE run.started 可再校准。
    params.onRunStarted?.({
      run_id: String(started.run_id),
      thread_id: String(started.thread_id || params.thread_id || ''),
      status: 'created',
      agent_mode: started.agent_mode ? String(started.agent_mode) : undefined,
      model: started.model ? String(started.model) : params.model,
    });
    const answer = await subscribeChatRun(String(started.run_id), {
      signal: params.signal,
      onDelta: params.onDelta,
      onReasoningDelta: params.onReasoningDelta,
      onReasoningCompleted: params.onReasoningCompleted,
      onReasoningConnectionEnd: params.onReasoningConnectionEnd,
      onMessageCompleted: params.onMessageCompleted,
      onCommentary: params.onCommentary,
      onModelConnection: params.onModelConnection,
      onRunPhase: params.onRunPhase,
      onRunStarted: params.onRunStarted,
      onRunCompleted: params.onRunCompleted,
      onRunPartial: params.onRunPartial,
      onRunCancelled: params.onRunCancelled,
      onThreadId: params.onThreadId,
      onMessageId: params.onMessageId,
      onUserMessageId: params.onUserMessageId,
      onInputApplied: params.onInputApplied,
      onInputRejected: params.onInputRejected,
      onContextUsage: params.onContextUsage,
      onInteractive: params.onInteractive,
      onCitations: params.onCitations,
      onPlanUpdate: params.onPlanUpdate,
      onTaskPlan: params.onTaskPlan,
      onCapabilityLoaded: params.onCapabilityLoaded,
      onArtifactSaved: params.onArtifactSaved,
      onResearchProgress: params.onResearchProgress,
      onToolEvent: params.onToolEvent,
      onSubagentStep: params.onSubagentStep,
      onRouteSelected: params.onRouteSelected,
      onClarification: params.onClarification,
      onApproval: params.onApproval,
      onRecommendation: params.onRecommendation,
      onRecommendAgents: params.onRecommendAgents,
      onAttachmentsStatus: params.onAttachmentsStatus,
      onCompaction: params.onCompaction,
      onCompacted: params.onCompacted,
      onMemoryUpdated: params.onMemoryUpdated,
      onInputAccepted: params.onInputAccepted,
      onError: params.onError,
      onSequence: params.onSequence,
      onSequenceGap: params.onSequenceGap,
      onStreamEnd: params.onStreamEnd,
    });
    // 原样返回（可能为空串）：HITL 挂起（消歧卡/表单）时正文本就为空，兜底文案由调用方
    // 按「是否挂起/出错」决定，这里不能一刀切塞「模型未返回内容」
    return answer;
  }

  throw new Error('Harness Run 未能启动');
}

// HITL：恢复挂起的子智能体运行（提交表单/选项后续接）
export async function resumeChatTurn(params: {
  run_id: string;
  resume_value: any;
  kind?: 'clarification' | 'plan_confirmation';
  resume_id?: string; // 一次性恢复令牌（挂起事件下发；服务端校验匹配，防旧页面/重复提交）
  signal?: AbortSignal;
  onDelta?: (delta: string, content: string) => void;
  onReasoningDelta?: (delta: string, reasoning: string) => void;
  onReasoningCompleted?: (payload?: { text?: string; seconds?: number }) => void | Promise<void>;
  onReasoningConnectionEnd?: () => void | Promise<void>;
  onMessageCompleted?: (content: string) => void;
  onCommentary?: StreamCallbacks['onCommentary'];
  onModelConnection?: StreamCallbacks['onModelConnection'];
  onRunPhase?: StreamCallbacks['onRunPhase'];
  onRunStarted?: (payload: RunStartedPayload) => void;
  onRunCompleted?: (payload: { message_id?: number; timestamp?: number }) => void;
  onRunPartial?: (payload: { message_id?: number; timestamp?: number; reason_codes?: string[] }) => void;
  onRunCancelled?: (payload: { timestamp?: number; reason?: string }) => void;
  onThreadId?: (threadId: string) => void;
  onMessageId?: (id: number) => void;
  onInputApplied?: (payload: { inputId: string; content: string; scope: string }) => void;
  onInputRejected?: (payload: { inputId: string; content: string; reason: string }) => void;
  onInteractive?: (payload: InteractivePayload) => void;
  onCitations?: (sources: CitationSource[]) => void;
  onPlanUpdate?: (event: PlanUpdateEvent) => void;
  onTaskPlan?: (event: TaskPlanEvent) => void;
  onCapabilityLoaded?: (names: string[]) => void;
  onArtifactSaved?: (payload: { source?: string; files: GeneratedFile[] }) => void;
  onResearchProgress?: (payload: ResearchProgressPayload) => void;
  onToolEvent?: (ev: ToolStepEvent) => void;
  onSubagentStep?: (ev: SubagentStepEvent) => void;
  onAttachmentsStatus?: (items: AttachmentIssue[]) => void;
  /** 敏感工具审批（P0-2 补齐）：resume 续接段同样可能挂起审批卡 */
  onApproval?: (payload: ApprovalRequest) => void;
  /** 传输层接收（2xx 响应头到达）。注意：响应头到达≠令牌已消费（令牌在后台生成器里才
   *  CAS 消费，第五批修正）——回滚判定必须以 onInputAccepted 为准，本回调仅作传输层观测。 */
  onStreamStart?: () => void;
  /** 用户输入 CAS 消费成功（input.accepted 帧）：「本次提交不可回滚」的权威信号 */
  onInputAccepted?: () => void;
  onError?: (message: string) => void;
  /** 已接受事件的 sequence 游标（P0-2）：resume 流断流交接订阅通道时按 after= 续传 */
  onSequence?: (sequence: number) => void;
  onSequenceGap?: (gap: { expected: number; received: number }) => void;
  /** 传输结束回调（P0-2）：sawTerminal=false 的正常 EOF＝断流而非完成（见 StreamEndInfo） */
  onStreamEnd?: (info: StreamEndInfo) => void;
}) {
  // 交互补全与新消息共用 Run inputs 边界。
  const runId = String(params.run_id || '').trim();
  if (!runId) throw new Error('缺少 run_id');
  const started: any = await requestAgentApi(`/chat/runs/${encodeURIComponent(runId)}/inputs`, {
    method: 'POST',
    body: JSON.stringify({
      kind: params.kind || 'clarification',
      value: params.resume_value,
      resume_id: params.resume_id || undefined,
    }),
    signal: params.signal,
    headers: { 'X-Harness-Protocol-Version': '1' },
  });
  // 2xx 受理＝服务器已接收本次输入；令牌消费仍以 input.accepted 帧为准
  params.onStreamStart?.();
  const after = Number(started?.after ?? 0) || 0;
  const answer = await subscribeChatRun(runId, {
    after: after > 0 ? after : undefined,
    signal: params.signal,
    onDelta: params.onDelta,
    onReasoningDelta: params.onReasoningDelta,
    onReasoningCompleted: params.onReasoningCompleted,
    onReasoningConnectionEnd: params.onReasoningConnectionEnd,
    onMessageCompleted: params.onMessageCompleted,
    onCommentary: params.onCommentary,
    onModelConnection: params.onModelConnection,
    onRunPhase: params.onRunPhase,
    onRunStarted: params.onRunStarted,
    onRunCompleted: params.onRunCompleted,
    onRunPartial: params.onRunPartial,
    onRunCancelled: params.onRunCancelled,
    onThreadId: params.onThreadId,
    onMessageId: params.onMessageId,
    onInputApplied: params.onInputApplied,
    onInputRejected: params.onInputRejected,
    onInteractive: params.onInteractive,
    onCitations: params.onCitations,
    onPlanUpdate: params.onPlanUpdate,
    onTaskPlan: params.onTaskPlan,
    onCapabilityLoaded: params.onCapabilityLoaded,
    onArtifactSaved: params.onArtifactSaved,
    onResearchProgress: params.onResearchProgress,
    onToolEvent: params.onToolEvent,
    onSubagentStep: params.onSubagentStep,
    onAttachmentsStatus: params.onAttachmentsStatus,
    onApproval: params.onApproval,
    onInputAccepted: params.onInputAccepted,
    onError: params.onError,
    onSequence: params.onSequence,
    onSequenceGap: params.onSequenceGap,
    onStreamEnd: params.onStreamEnd,
  });
  return answer || '';
}

// Models API
export async function getAgentModels(): Promise<AgentModelItem[]> {
  const data: any = await requestAgentApi('/models', { method: 'GET' });
  const list = Array.isArray(data) ? data : (Array.isArray(data?.data) ? data.data : []);
  return list.map((item: any) => ({
    id: String(item?.id || item?.model_name || '').trim(),
    name: String(item?.name || item?.model_name || item?.id || ''),
    is_default: Boolean(item?.is_default),
  })).filter((item: AgentModelItem) => item.id);
}

/** 当前用户可运行工作流的模型列表。 */
export async function getWorkflowModelOptions(): Promise<WorkflowModelOption[]> {
  const data: any = await defHttp.get(
    { url: '/agent-api/workflow/model/options' },
    { isTransformResponse: false, apiUrl: '', errorMessageMode: 'none' },
  );
  const list = Array.isArray(data) ? data : (Array.isArray(data?.data) ? data.data : []);
  return list.map((item: any) => ({
    label: String(item?.label || item?.value || '').trim(),
    value: String(item?.value || '').trim(),
    available: item?.available !== false,
  })).filter((item: WorkflowModelOption) => item.value);
}

/** 智能体广场预检用模型列表：包含用户可用聊天模型和平台可用向量模型。 */
export async function getMarketplaceModelOptions(): Promise<WorkflowModelOption[]> {
  const data: any = await defHttp.get(
    { url: '/agent-api/workflow/marketplace/model/options' },
    { isTransformResponse: false, apiUrl: '', errorMessageMode: 'none' },
  );
  const list = Array.isArray(data) ? data : (Array.isArray(data?.data) ? data.data : []);
  return list.map((item: any) => ({
    label: String(item?.label || item?.value || '').trim(),
    value: String(item?.value || '').trim(),
    available: item?.available !== false,
  })).filter((item: WorkflowModelOption) => item.value);
}

// Agents API
export async function getAgents(params?: { recommend?: boolean; search?: string }): Promise<AgentItem[]> {
  const queryParams = new URLSearchParams();
  if (params?.recommend) queryParams.append('recommend', 'true');
  if (params?.search) queryParams.append('search', params.search);
  const query = queryParams.toString();
  const path = `/agents${query ? `?${query}` : ''}`;
  const data: any = await requestAgentApi(path, { method: 'GET' });
  const list = Array.isArray(data) ? data : (Array.isArray(data?.data) ? data.data : []);
  return list.map((item: any) => ({
    id: String(item?.id || '').trim(),
    name: String(item?.name || item?.appName || ''),
    description: String(item?.description || item?.appRemark || ''),
    icon: String(item?.icon || item?.appIcon || ''),
    is_recommend: Boolean(item?.is_recommend || item?.isRecommend),
    status: Number(item?.status || 0),
  }));
}

// Skills API
export async function getSkills(): Promise<SkillItem[]> {
  const data: any = await defHttp.get(
    {
      url: '/ai/skill/list',
      params: {
        pageNo: 1,
        pageSize: 20,
        enabled: 1,
        column: 'updateTime',
        order: 'desc',
      },
    },
    { errorMessageMode: 'none' }
  );
  const list = Array.isArray(data)
    ? data
    : Array.isArray(data?.records)
      ? data.records
      : Array.isArray(data?.data)
        ? data.data
        : [];

  return list
    .filter((item: any) => item?.enabled === 1 || item?.enabled === true || item?.enabled === '1')
    .map((item: any) => {
      const skillId = String(item?.skillId || item?.id || '').trim();
      return {
        id: skillId,
        recordId: String(item?.id || '').trim(),
        skillId,
        name: String(item?.name || skillId || '未命名 Skill'),
        description: String(item?.description || ''),
        icon: String(item?.icon || ''),
        enabled: true,
        version: String(item?.version || ''),
        author: String(item?.author || ''),
        source: String(item?.source || ''),
        installStatus: String(item?.installStatus || ''),
      };
    })
    .filter((item: SkillItem) => item.id);
}

/** 取某个 Skill 的 SKILL.md 正文（供广场详情弹窗展示）。recordId = ai_skill.id。 */
export async function getSkillReadme(recordId: string): Promise<string> {
  if (!recordId) return '';
  const r: any = await defHttp.get(
    { url: '/ai/skill/readme', params: { id: recordId } },
    { errorMessageMode: 'none' }
  );
  if (typeof r === 'string') return r;
  return String(r?.readme ?? r?.content ?? r?.result ?? '');
}

// Threads API
export async function getThreads(
  search?: string,
  limit?: number,
  offset?: number,
  scope: ThreadScope = 'ordinary',
): Promise<ThreadItem[]> {
  const qs = new URLSearchParams();
  if (search && search.trim()) qs.set('search', search.trim());
  if (limit != null) qs.set('limit', String(limit));
  if (offset) qs.set('offset', String(offset));
  qs.set('scope', scope);
  const query = qs.toString() ? `?${qs.toString()}` : '';
  const data: any = await requestAgentApi(`/chat/threads${query}`, { method: 'GET' });
  const list = Array.isArray(data) ? data : (Array.isArray(data?.data) ? data.data : []);
  return list.map((item: any) => ({
    id: String(item?.id || item?.thread_id || '').trim(),
    title: String(item?.title || '未命名对话'),
    model: item?.model ? String(item.model) : undefined,
    assistant_preset: isAssistantPreset(item?.assistant_preset) ? item.assistant_preset : undefined,
    pinned: Boolean(item?.pinned),
    created_at: item?.created_at,
    updated_at: item?.updated_at,
    active_run: item?.active_run?.id ? {
      id: String(item.active_run.id),
      status: String(item.active_run.status || ''),
      model: item.active_run.model ? String(item.active_run.model) : undefined,
      kind: item.active_run.kind ? String(item.active_run.kind) : undefined,
      agent_mode: item.active_run.agent_mode
        ? String(item.active_run.agent_mode)
        : undefined,
      phase: item.active_run.phase
        ? String(item.active_run.phase)
        : undefined,
      interactive_type: item.active_run.interactive_type
        ? String(item.active_run.interactive_type)
        : undefined,
      subagent_id: item.active_run.subagent_id ? String(item.active_run.subagent_id) : undefined,
      resume_token: item.active_run.resume_token ? String(item.active_run.resume_token) : undefined,
    } : null,
  }));
}

export async function deleteThread(threadId: string, scope?: ThreadScope): Promise<void> {
  await requestAgentApi(threadPath(`/chat/threads/${encodeURIComponent(threadId)}`, scope), { method: 'DELETE' });
}

export async function pinThread(threadId: string, pinned: boolean, scope?: ThreadScope): Promise<void> {
  await requestAgentApi(threadPath(`/chat/threads/${encodeURIComponent(threadId)}/pin`, scope), {
    method: 'POST',
    body: JSON.stringify({ pinned }),
  });
}

export async function renameThread(threadId: string, title: string, scope?: ThreadScope): Promise<string> {
  const data: any = await requestAgentApi(threadPath(`/chat/threads/${encodeURIComponent(threadId)}/rename`, scope), {
    method: 'POST',
    body: JSON.stringify({ title }),
  });
  return String(data?.title || title);
}

export async function getThreadModel(threadId: string, scope?: ThreadScope): Promise<string> {
  const data: any = await requestAgentApi(
    threadPath(`/chat/threads/${encodeURIComponent(threadId)}/model`, scope),
    { method: 'GET' },
  );
  return String(data?.model || '');
}

export async function getThreadSettings(
  threadId: string,
  scope?: ThreadScope,
): Promise<{ model: string; assistant_preset?: AssistantPreset; workspace_folder?: import('./myfiles.api').WorkFolderSelection }> {
  const data: any = await requestAgentApi(
    threadPath(`/chat/threads/${encodeURIComponent(threadId)}/model`, scope),
    { method: 'GET' },
  );
  return {
    model: String(data?.model || ''),
    assistant_preset: isAssistantPreset(data?.assistant_preset) ? data.assistant_preset : undefined,
    workspace_folder: data?.workspace_folder?.id ? {
      id: String(data.workspace_folder.id),
      name: String(data.workspace_folder.name || '工作文件夹'),
      unavailable: Boolean(data.workspace_folder.unavailable),
    } : undefined,
  };
}

export async function updateThreadModel(threadId: string, model: string, scope?: ThreadScope): Promise<string> {
  const data: any = await requestAgentApi(
    threadPath(`/chat/threads/${encodeURIComponent(threadId)}/model`, scope),
    { method: 'PUT', body: JSON.stringify({ model }) },
  );
  return String(data?.model || model);
}

// ---- 用户长期记忆管理（§14 Phase 2）----
export interface UserMemoryItem {
  id: string;
  type: string;
  content: string;
  updated_at?: string;
}

/** 列出本人 active 长期记忆。enabled=false 表示后端记忆未启用（Runtime 未配置或用户已关）。 */
export async function listMemories(type?: string): Promise<{ enabled: boolean; items: UserMemoryItem[] }> {
  const qs = type ? `?type=${encodeURIComponent(type)}` : '';
  const data: any = await requestAgentApi(`/memories${qs}`, { method: 'GET' });
  const items = (Array.isArray(data?.items) ? data.items : []).map((it: any) => ({
    id: String(it?.id || ''),
    type: String(it?.type || ''),
    content: String(it?.content || ''),
    updated_at: it?.updated_at,
  }));
  return { enabled: Boolean(data?.enabled), items };
}

/** 手动添加一条记忆（走后端治理管线；类型不合法/敏感/为空会 400）。 */
export async function addMemory(type: string, content: string): Promise<{ id: string; isNew: boolean }> {
  const data: any = await requestAgentApi('/memories', {
    method: 'POST',
    body: JSON.stringify({ type, content }),
  });
  return { id: String(data?.id || ''), isNew: Boolean(data?.is_new) };
}

export async function deleteMemory(id: string): Promise<void> {
  await requestAgentApi(`/memories/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

/** 编辑一条记忆（真 PUT 原地改）。不要用「新增+删除」模拟——编辑文本与原文相似时
    新增会因语义去重命中旧条返回旧 id，随后删除同一条 → 整条记忆消失。 */
export async function updateMemory(id: string, type: string, content: string): Promise<void> {
  await requestAgentApi(`/memories/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify({ type, content }),
  });
}

/** 记忆总开关状态。available=false 表示 Runtime 库未配置（功能不可用）。 */
export async function getMemorySettings(): Promise<{ available: boolean; enabled: boolean }> {
  const data: any = await requestAgentApi('/memory-settings', { method: 'GET' });
  return { available: Boolean(data?.available), enabled: Boolean(data?.enabled) };
}

export async function setMemorySettings(enabled: boolean): Promise<boolean> {
  const data: any = await requestAgentApi('/memory-settings', {
    method: 'PUT',
    body: JSON.stringify({ enabled }),
  });
  return Boolean(data?.enabled);
}

/** 删除本人全部记忆（复刻 ChatGPT「删除全部记忆」）。返回删除条数。 */
export async function deleteAllMemories(): Promise<number> {
  const data: any = await requestAgentApi('/memories', { method: 'DELETE' });
  return Number(data?.deleted || 0);
}

/** 按内容搜索本人记忆（服务端 LIKE）。 */
export async function searchMemories(q: string): Promise<UserMemoryItem[]> {
  const data: any = await requestAgentApi(`/memories?q=${encodeURIComponent(q)}`, { method: 'GET' });
  return (Array.isArray(data?.items) ? data.items : []).map((it: any) => ({
    id: String(it?.id || ''),
    type: String(it?.type || ''),
    content: String(it?.content || ''),
    updated_at: it?.updated_at,
  }));
}

// ---- 个性化设置（复刻 ChatGPT Personalization）----
export interface PersonalizationConfig {
  autoManage: boolean;
  nickname: string;
  occupation: string;
  about: string;
  customInstructions: string;
  memorySummary: string;
  memorySummaryAt: string;
}

export async function getPersonalization(): Promise<PersonalizationConfig> {
  const data: any = await requestAgentApi('/personalization', { method: 'GET' });
  return {
    autoManage: data?.autoManage !== false,
    nickname: String(data?.nickname || ''),
    occupation: String(data?.occupation || ''),
    about: String(data?.about || ''),
    customInstructions: String(data?.customInstructions || ''),
    memorySummary: String(data?.memorySummary || ''),
    memorySummaryAt: String(data?.memorySummaryAt || ''),
  };
}

export async function savePersonalization(
  patch: Partial<PersonalizationConfig>,
): Promise<void> {
  await requestAgentApi('/personalization', { method: 'PUT', body: JSON.stringify(patch) });
}

/** 生成/刷新记忆摘要（模型分区总结全部记忆）。 */
export async function summarizeMemories(): Promise<{ summary: string; updatedAt: string }> {
  const data: any = await requestAgentApi('/memories/summarize', { method: 'POST' });
  return { summary: String(data?.summary || ''), updatedAt: String(data?.updatedAt || '') };
}

/** 自然语言「添加或更新」记忆。返回入库内容列表。 */
export async function nlUpdateMemories(text: string): Promise<string[]> {
  const data: any = await requestAgentApi('/memories/nl-update', {
    method: 'POST',
    body: JSON.stringify({ text }),
  });
  return Array.isArray(data?.stored) ? data.stored.map((s: any) => String(s)) : [];
}

/** 手动压缩会话上下文（composer「+」菜单）：较早对话压进滚动摘要，释放窗口。
 *  对话不够长无可压段时 compacted=false；before/usage 为压缩前后占用。 */
export async function compactThreadContext(
  threadId: string,
  model?: string,
  scope?: ThreadScope,
): Promise<{ compacted: boolean; before: ContextUsage; usage: ContextUsage }> {
  const data: any = await requestAgentApi(
    threadPath(`/chat/threads/${encodeURIComponent(threadId)}/compact`, scope),
    { method: 'POST', body: JSON.stringify({ model: model || null }) },
  );
  const norm = (u: any): ContextUsage => ({
    tokens: Number(u?.tokens || 0),
    window: Number(u?.window || 0),
    ratio: Number(u?.ratio || 0),
  });
  return { compacted: Boolean(data?.compacted), before: norm(data?.before), usage: norm(data?.usage) };
}

/** 打开旧会话时估算上下文占用，恢复占用环（口径同流式 context.usage）。失败返回 null。
 *  model=该会话下一次发送将使用的模型：占用环分母须用它的真实窗口，缺省则后端退回 32K 兜底。 */
export async function getThreadContextUsage(
  threadId: string,
  model?: string,
  scope?: ThreadScope,
): Promise<ContextUsage | null> {
  try {
    const params = new URLSearchParams();
    if (model) params.set('model', model);
    if (scope) params.set('scope', scope);
    const query = params.toString() ? `?${params.toString()}` : '';
    const data: any = await requestAgentApi(
      `/chat/threads/${encodeURIComponent(threadId)}/context-usage${query}`,
      { method: 'GET' },
    );
    if (!data || typeof data.ratio !== 'number') return null;
    return {
      tokens: Number(data.tokens || 0),
      window: Number(data.window || 0),
      ratio: Number(data.ratio || 0),
    };
  } catch {
    return null;
  }
}

export type UploadedFile = {
  filename: string;
  kind: string;
  text: string;
  /** 上传即建立的唯一文件实体；分析、后续修改和版本历史始终使用该 ID。 */
  file_id?: string;
  /** 原始字节哈希，不以解析文本或文件名代替文件身份。 */
  sha256?: string;
  versionNo?: number;
  chars: number;
  truncated: boolean;
  /** 解析置信度（P0 附件生命周期）：ok=完整 / partial=截断或部分失败 / failed=未提取出内容 */
  status?: 'ok' | 'partial' | 'failed';
  /** 未完整读取的简短原因（status 非 ok 时） */
  note?: string;
  // 客户端补充：图片的本地预览地址（data URL），仅用于缩略图/大图预览，不回传后端
  previewUrl?: string;
  // 客户端补充：canvas 压缩缩略图（data URL，≤640px JPEG）：随消息作为 preview_url 落库，
  // 历史回放时图片卡据此显示（原图 previewUrl 只在内存，刷新即失）
  thumbUrl?: string;
  // 客户端补充：本地单调 id，用于上传完成后就地回填/移除对应占位卡
  uid?: number;
  // 客户端补充：true=本地预览已出、后端上传解析仍在进行（缩略图上盖转圈）
  uploading?: boolean;
  // 客户端补充：原始 File 句柄（仅内存，供解析失败时「重试」重新上传；不序列化、不回传）
  rawFile?: File;
  /** 草稿跨刷新后只剩展示元数据、没有原始字节/解析正文；重新选择文件前禁止发送。 */
  needsReupload?: boolean;
};

// 会话文件上传（ADR-040/041）：上传即解析为文本返回
export async function uploadChatFile(file: File, model?: string): Promise<UploadedFile> {
  const config = getAgentApiConfig();
  const baseUrl = normalizeBaseUrl(config.baseUrl);
  const form = new FormData();
  form.append('file', file);
  if (model) form.append('model', model);
  const headers = getHeaders(config);
  delete headers['Content-Type']; // 让浏览器自动设置 multipart 边界
  const response = await fetch(`${baseUrl}/chat/upload`, {
    method: 'POST',
    headers,
    body: form,
  });
  if (!response.ok) {
    let message = `上传失败：${response.status}`;
    try {
      const data = await response.json();
      message = apiErrorMessage(data, message);
    } catch {
      // keep status message
    }
    throw new Error(message);
  }
  return response.json();
}

// @ 是委派入口，候选必须来自 Agent API 的可执行集合，而不是 Java 的「当前用户可见应用」。
// /chat/subagents 与实际委派共用 published / 工作流版本 / 租户 / 角色部门 ACL 判据；
// 不可执行的系统工具、外部应用和无权限应用不得只因“可见”就出现在这里。
export function normalizeSubagentItems(data: any): SubagentItem[] {
  const list = Array.isArray(data)
    ? data
    : Array.isArray(data?.records)
      ? data.records
      : Array.isArray(data?.list)
        ? data.list
        : Array.isArray(data?.data)
          ? data.data
          : [];

  return list
    .map((item: any) => ({
      id: String(item?.id || '').trim(),
      name: String(item?.name || '未命名智能体'),
      description: String(item?.description || ''),
      icon: String(item?.icon || ''),
      type: String(item?.type || ''),
      scope: item?.scope === 'owned' || item?.scope === 'shared' ? item.scope : undefined,
    }))
    .filter((item: SubagentItem) => item.id);
}

export async function getSubagents(keyword?: string): Promise<SubagentItem[]> {
  const params = new URLSearchParams({ limit: '50' });
  const query = keyword?.trim();
  if (query) params.set('keyword', query);
  const data: any = await requestAgentApi(`/chat/subagents?${params.toString()}`, { method: 'GET' });
  const normalized = normalizeSubagentItems(data);
  const normalizedQuery = query?.toLowerCase();
  return normalizedQuery
    ? normalized.filter((item: SubagentItem) =>
        item.name.toLowerCase().includes(normalizedQuery)
        || (item.description || '').toLowerCase().includes(normalizedQuery),
      )
    : normalized;
}

// 子智能体独立对话窗的会话模型已统一到 /workflow/run/*（useAgentRun 运行栈）；
// 曾并存的 /chat/subthread* 封装从未接线，2026-07-14 随后端端点一并移除。

export async function getThreadMessages(
  threadId: string,
  scope?: ThreadScope,
): Promise<
  Array<{
    id?: number;
    /** 产生该助手消息的 Run；执行分段回放需要用它保持同一 Run 身份。 */
    run_id?: string;
    role: string;
    content: string;
    /** 消息状态（第四批项 3 契约）：'superseded'=被重新生成取代的旧回答（紧邻新回答之前，
     *  带自己的 execution_trace/citations，前端折叠为「查看上一版」入口）；编辑重发截断的
     *  死分支是 'archived'，后端不会返回。缺省/其它值=当前消息。 */
    status?: string;
    agent_mode?: string | null;
    feedback?: 'up' | 'down' | null;
    citations?: CitationSource[] | null;
    /** 子智能体 chip 回放（agent_steps，§16.5）：刷新后仍可见「这轮谁办的事」 */
    subagent_calls?: Array<{ name: string; status: string }> | null;
    /** 用户消息附件元数据快照（attachments_json）：刷新/历史回放后附件卡仍可见 */
    attachments?: Array<{
      filename: string;
      kind?: string;
      status?: string;
      note?: string;
      file_id?: string;
      preview_url?: string;
      reference_id?: string;
    }> | null;
    execution_trace?: {
      event_cursor?: number | null;
      startedAt?: number | null;
      completedAt?: number | null;
      durationMs?: number | null;
      status?: string;
      plan?: any[];
      task_plan?: any[] | null;
      steps?: any[];
      subagents?: any[];
      files?: GeneratedFile[];
      agent_mode?: string | null;
      research_progress?: ResearchProgressPayload | null;
      /** 附件读取降级（attachments.status 事件回放） */
      attachments_status?: AttachmentIssue[] | null;
      preamble?: string | null;
      plan_report?: string | null;
      reasoning_summary?: string | null;
      reasoning_seconds?: number | null;
      /** 用户运行中输入前封存的执行段；按 inputMessageId 插回历史原位。 */
      segments?: Array<{
        inputMessageId: number;
        inputId?: string | null;
        startSequence?: number | null;
        endSequence?: number | null;
        startedAt?: number | null;
        completedAt?: number | null;
        durationMs?: number | null;
        steps?: any[];
        preamble?: string | null;
        plan_report?: string | null;
        reasoning_summary?: string | null;
        reasoning_seconds?: number | null;
        status?: string;
      }> | null;
    } | null;
  }>
> {
  const data: any = await requestAgentApi(
    threadPath(`/chat/threads/${encodeURIComponent(threadId)}/messages`, scope),
    { method: 'GET' },
  );
  return Array.isArray(data) ? data : [];
}

export async function getThreadActiveRun(threadId: string, scope?: ThreadScope): Promise<ActiveRun | null> {
  const data: any = await requestAgentApi(
    threadPath(`/chat/threads/${encodeURIComponent(threadId)}/active-run`, scope),
    { method: 'GET' },
  );
  if (!data?.id) return null;
  return {
    id: String(data.id),
    status: String(data.status || ''),
    model: data.model ? String(data.model) : undefined,
    kind: data.kind ? String(data.kind) : undefined,
    agent_mode: ['standard', 'plan', 'research'].includes(String(data.agent_mode))
      ? data.agent_mode
      : undefined,
    phase: data.phase ? String(data.phase) : undefined,
    interactive_type: data.interactive_type ? String(data.interactive_type) : undefined,
    plan: data.plan && typeof data.plan === 'object' ? data.plan : undefined,
    goal_contract: data.goal_contract && typeof data.goal_contract === 'object'
      ? data.goal_contract
      : undefined,
    approved_plan_version: data.approved_plan_version == null
      ? undefined
      : Number(data.approved_plan_version),
    subagent_id: data.subagent_id ? String(data.subagent_id) : undefined,
    resume_token: data.resume_token ? String(data.resume_token) : undefined,
    started_at: data.started_at ? String(data.started_at) : undefined,
    completed_at: data.completed_at ? String(data.completed_at) : undefined,
  };
}

export async function subscribeChatRun(
  runId: string,
  params: StreamCallbacks & { signal?: AbortSignal; after?: number; initialContent?: string } = {},
) {
  const query = params.after ? `?after=${encodeURIComponent(String(params.after))}` : '';
  const response = await requestAgentApiStream(`/chat/runs/${encodeURIComponent(runId)}/events${query}`, {
    method: 'GET',
    signal: params.signal,
  });
  return readChatStream(response, params, {
    afterSequence: params.after,
    initialContent: params.initialContent,
  });
}

/** 请求取消 Run。返回服务端收敛结果（第三批契约）：status=cancelled＝已确认收敛；
 *  pending＝停止已发出但 5s 内执行未收敛——旧任务可能仍在跑，调用方**不得**按已取消收尾，
 *  应保持/恢复订阅等 pump 真正收敛后的 run.failed(已停止生成) 帧；
 *  completed/failed＝终态先到（Run 在停止请求前已自行结束）——按真实终态收尾，不得冒充
 *  「已停止」（审计项 22：此前一律映射成 cancelled，completed 的回答被标成被打断）。
 *  旧后端响应无 status 字段（{"success":true}）→ 兼容按 cancelled 处理。 */
export async function cancelChatRun(
  runId: string,
): Promise<{ success: boolean; status: 'cancelled' | 'pending' | 'completed' | 'failed' }> {
  const data: any = await requestAgentApi(`/chat/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' });
  const raw = String(data?.status || '');
  const status = raw === 'pending' ? 'pending'
    : raw === 'completed' ? 'completed'
      : raw === 'failed' ? 'failed'
        : 'cancelled';
  return {
    success: data?.success !== false,
    status,
  };
}

export type RunStateInfo = {
  run_id: string;
  thread_id?: string;
  status: string;
  agent_mode?: 'standard' | 'plan' | 'research';
  phase?: string;
  state_version?: number;
  goal_revision?: number;
  plan_version?: number;
  approved_plan_version?: number;
  event_cursor?: number;
  terminal_reason?: string;
  error?: string;
  plan?: HarnessPlanSnapshot | null;
  goal_contract?: {
    goal?: string;
    deliverable?: string;
    success_criteria?: string[];
    forbidden?: string[];
    budget_hint?: string;
  } | null;
};

export type HarnessPlanSnapshot = {
  run_id: string;
  goal_revision: number;
  plan_version: number;
  goal: string;
  updated_at: string;
  steps: Array<{
    step_id: string;
    order: number;
    title: string;
    detail?: string;
    status: 'pending' | 'in_progress' | 'completed' | 'skipped' | 'invalidated';
    required: boolean;
    acceptance_criteria: string[];
    evidence_refs: Array<Record<string, unknown>>;
    reason?: string;
  }>;
};

/** 可靠握手（N-02）：按客户端幂等键反查已建 Run。首帧（run.started）丢失时凭此发现
 *  「服务端其实已受理」，按 run_id 订阅续接而不是恢复草稿重发第二轮。
 *  null=确实没建成（重发安全）或查询失败（调用方自带重试）。 */
export type ClientRequestRunLookup =
  | { kind: 'found'; run_id: string; thread_id: string; status?: string }
  | { kind: 'not_found' };

export async function getRunByClientRequest(
  clientRequestId: string,
): Promise<ClientRequestRunLookup> {
  try {
    const data: any = await requestAgentApi(
      `/chat/requests/${encodeURIComponent(clientRequestId)}/run`,
      { method: 'GET' },
    );
    if (!data?.id) return { kind: 'not_found' };
    return {
      kind: 'found',
      run_id: String(data.id),
      thread_id: String(data.thread_id || ''),
      status: data.status ? String(data.status) : undefined,
    };
  } catch (error) {
    // 只有权威 404 才等于“服务端确认没有这次 Run”。断网/超时/5xx 必须向上抛，
    // 否则调用方会把未知误判为可安全重发，换新幂等键制造第二个 Run。
    if ((error as { status?: number })?.status === 404) return { kind: 'not_found' };
    throw error;
  }
}

export async function getRunState(runId: string): Promise<RunStateInfo> {
  const data: any = await requestAgentApi(`/chat/runs/${encodeURIComponent(runId)}`, { method: 'GET' });
  const phase = String(data?.phase || 'failed');
  const status = ['completed', 'partial', 'failed', 'cancelled'].includes(phase)
    ? phase
    : phase.startsWith('waiting_') || phase === 'plan_ready'
      ? 'waiting_user'
      : 'running';
  return {
    run_id: String(data?.run_id || runId),
    thread_id: data?.thread_id ? String(data.thread_id) : undefined,
    status,
    agent_mode: ['standard', 'plan', 'research'].includes(String(data?.agent_mode))
      ? data.agent_mode
      : undefined,
    phase,
    state_version: Number(data?.state_version) || 0,
    goal_revision: Number(data?.goal_revision) || 0,
    plan_version: Number(data?.plan_version) || 0,
    approved_plan_version: data?.approved_plan_version == null
      ? undefined
      : Number(data.approved_plan_version),
    event_cursor: Number(data?.event_cursor) || 0,
    terminal_reason: data?.terminal_reason ? String(data.terminal_reason) : undefined,
    error: data?.terminal_reason ? String(data.terminal_reason) : undefined,
    plan: data?.plan && typeof data.plan === 'object' ? data.plan as HarnessPlanSnapshot : null,
    goal_contract: data?.goal_contract && typeof data.goal_contract === 'object'
      ? data.goal_contract
      : null,
  };
}

// ---- 运行中消息队列（任务模式 B：Codex 式，运行中默认排队而非打断）----
export type ChatQueueAttachment = {
  filename: string;
  text?: string;
  file_id?: string;
  sha256?: string;
  kind?: string;
  image_url?: string;
  preview_url?: string;
  status?: string;
  note?: string;
};

/** 入队时刻的 TurnContext 快照（审计项 1）：派发时按它执行，不读「当前全局选择」——
 *  排队期间用户改选 Skill/知识库/文件不得漂移到已排队的消息上。 */
export type QueueTurnContext = {
  skills?: SkillItem[];
  /** @ 选中的委托目标；排队消息派发时仍须携带同一个 subagent_id。 */
  subagent?: SubagentItem;
  knowledge?: KnowledgeSelection[];
  files?: Array<{ id: string; filename: string }>;
  /** 「最近的对话」引用（2026-07-28）：排队时的选择必须随快照走，否则派发轮悄悄丢掉引用 */
  threads?: ThreadReference[];
  webSearch?: boolean;
  model?: string;
  planMode?: boolean;
  researchProfile?: boolean;
};

export type ChatQueueItem = {
  id: string;
  content: string;
  attachments?: ChatQueueAttachment[];
  /** TurnContext 快照：队列卡可核对绑定的上下文；派发按它恢复 */
  context?: QueueTurnContext;
  position: number;
  /** queued / dispatching（服务端租约状态） */
  status?: string;
  /** pop 认领时下发的一次性租约凭证：派发请求随 queue_item_id 一起带给 /chat，
   *  由服务端在 Run 持久化后原子确认删除（§10.6 P0，前端不再提前 confirm） */
  leaseToken?: string;
  createdAt?: string;
};

function normalizeQueueItem(raw: any): ChatQueueItem {
  return {
    id: String(raw?.id || ''),
    content: String(raw?.content || ''),
    attachments:
      Array.isArray(raw?.attachments) && raw.attachments.length
        ? raw.attachments.map((a: any) => ({
            filename: String(a?.filename || '附件'),
            text: a?.text != null ? String(a.text) : undefined,
            file_id: a?.file_id ? String(a.file_id) : undefined,
            sha256: a?.sha256 ? String(a.sha256) : undefined,
            kind: a?.kind ? String(a.kind) : undefined,
            image_url: a?.image_url ? String(a.image_url) : undefined,
            preview_url: a?.preview_url ? String(a.preview_url) : undefined,
            status: a?.status ? String(a.status) : undefined,
            note: a?.note ? String(a.note) : undefined,
          }))
        : undefined,
    context: raw?.context && typeof raw.context === 'object' ? (raw.context as QueueTurnContext) : undefined,
    position: Number(raw?.position ?? 0),
    status: raw?.status ? String(raw.status) : undefined,
    leaseToken: raw?.leaseToken ? String(raw.leaseToken) : undefined,
    createdAt: raw?.createdAt ? String(raw.createdAt) : undefined,
  };
}

/** 拉取本会话运行中消息队列（服务端持久化）：进入会话时调用一次回填，刷新后仍在。 */
export async function getChatQueue(threadId: string, scope?: ThreadScope): Promise<ChatQueueItem[]> {
  const data: any = await requestAgentApi(
    threadPath(`/chat/threads/${encodeURIComponent(threadId)}/queue`, scope),
    { method: 'GET' },
  );
  const list = Array.isArray(data?.items) ? data.items : [];
  return list.map(normalizeQueueItem);
}

/** 追加一条待发送消息到队尾（运行中再发送的默认去向）。队列已满时后端返回 409，
 *  错误信息随 apiErrorMessage 透出，调用方直接展示即可。 */
export async function addChatQueueItem(
  threadId: string,
  content: string,
  attachments?: ChatQueueAttachment[],
  context?: QueueTurnContext,
  scope?: ThreadScope,
): Promise<ChatQueueItem> {
  const data: any = await requestAgentApi(threadPath(`/chat/threads/${encodeURIComponent(threadId)}/queue`, scope), {
    method: 'POST',
    body: JSON.stringify({
      content,
      attachments: attachments?.length ? attachments : undefined,
      context: context || undefined,
    }),
  });
  return normalizeQueueItem(data?.item || data);
}

/** 持久化队列拖动后的完整顺序。 */
export async function reorderChatQueue(threadId: string, orderedIds: string[], scope?: ThreadScope): Promise<void> {
  await requestAgentApi(threadPath(`/chat/threads/${encodeURIComponent(threadId)}/queue`, scope), {
    method: 'PATCH',
    body: JSON.stringify({ orderedIds }),
  });
}

/** 编辑队列中某条待发送消息的正文。 */
export async function updateChatQueueItem(itemId: string, content: string): Promise<void> {
  await requestAgentApi(`/chat/queue/${encodeURIComponent(itemId)}`, {
    method: 'PUT',
    body: JSON.stringify({ content }),
  });
}

/** 删除队列中某条；「移回输入框」由调用方在删除成功后把 content 塞回输入框实现。 */
export async function deleteChatQueueItem(itemId: string): Promise<void> {
  await requestAgentApi(`/chat/queue/${encodeURIComponent(itemId)}`, { method: 'DELETE' });
}

/** 认领队首（租约制，服务端置 dispatching 不删除）：当前 Run 到达终态后派发下一条时调用；
 *  无可派/已有派发中/仍有活动 Run 时返回 null。返回项带一次性 leaseToken——派发请求把
 *  queue_item_id + queue_lease_token 交给 /chat，由服务端在 Run 持久化后原子确认删除
 *  （§10.6 P0）；发送失败/关标签则租约超时后按「Run 是否已建」回收或删除，不丢不重。 */
export async function popChatQueue(threadId: string, scope?: ThreadScope): Promise<ChatQueueItem | null> {
  const data: any = await requestAgentApi(
    threadPath(`/chat/threads/${encodeURIComponent(threadId)}/queue/pop`, scope),
    { method: 'POST' },
  );
  return data?.item ? normalizeQueueItem(data.item) : null;
}

/** （兼容入口，主链路已不用）删除 dispatching 项；§10.6 P0 起必须携带本次租约凭证，
 *  旧租约/未认领项一律被服务端拒绝。仅在后端未下发 leaseToken 的过渡期作为回退调用。 */
export async function confirmChatQueue(itemId: string, leaseToken?: string): Promise<void> {
  await requestAgentApi(`/chat/queue/${encodeURIComponent(itemId)}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ lease_token: leaseToken || '' }),
  });
}

/** 立即引导（C）：运行中把一条消息作为引导发给当前 Run——不取消 Run、不进队列，
 *  与「停止」（取消整个 Run）严格分开。 */
export async function submitChatRunInput(
  runId: string,
  params: {
    content: string;
    threadId: string;
    clientInputId: string;
    expectedRunId?: string;
    attachments?: ChatQueueAttachment[];
    context?: QueueTurnContext;
  },
): Promise<{ inputId?: string; messageId?: number }> {
  void params.threadId;
  void params.context;
  const submit = () => requestAgentApi<any>(`/chat/runs/${encodeURIComponent(runId)}/inputs`, {
    method: 'POST',
    body: JSON.stringify({
      kind: 'message',
      content: params.content,
      client_input_id: params.clientInputId,
      expected_run_id: params.expectedRunId || runId,
      attachments: params.attachments?.length ? params.attachments : undefined,
    }),
  });
  const normalize = (data: any) => ({
    inputId: data?.input_id ? String(data.input_id) : undefined,
    messageId: data?.message_id != null && Number.isFinite(Number(data.message_id))
      ? Number(data.message_id)
      : undefined,
  });
  try {
    return normalize(await submit());
  } catch (error) {
    if ((error as { status?: number })?.status) throw error;
    // 未知网络结果使用同一 input id 重试；后端幂等返回首次受理事实。
    return normalize(await submit());
  }
}

export async function submitMessageFeedback(
  messageId: number,
  feedback: 'up' | 'down' | null,
): Promise<void> {
  await requestAgentApi(`/chat/messages/${messageId}/feedback`, {
    method: 'POST',
    body: JSON.stringify({ feedback }),
  });
}

/** Tool Gateway 审批（§11）：用户对敏感工具调用放行/拒绝；放行后同幂等键重试即执行 */
export async function decideGatewayApproval(
  callId: string,
  approved: boolean,
): Promise<{ status: string }> {
  return requestAgentApi(`/workflow/gateway/${approved ? 'approve' : 'reject'}`, {
    method: 'POST',
    body: JSON.stringify({ callId }),
  });
}

// Master Config API
export interface MasterConfig {
  baseUrl: string;
  apiKey: string;
  model: string;
  systemPrompt: string;
  welcomeMessage: string;
}

export async function getMasterConfig(): Promise<MasterConfig> {
  const data: any = await requestAgentApi('/master-config', { method: 'GET' });
  return {
    baseUrl: data?.baseUrl || data?.base_url || '',
    apiKey: data?.apiKey || data?.api_key || '',
    model: data?.model || '',
    systemPrompt: data?.systemPrompt || data?.system_prompt || '',
    welcomeMessage: data?.welcomeMessage || data?.welcome_message || '',
  };
}

export async function saveMasterConfig(config: MasterConfig): Promise<void> {
  await requestAgentApi('/master-config', {
    method: 'POST',
    body: JSON.stringify(config),
  });
}

export type WorkspaceFileItem = {
  id: string;
  name: string;
  path?: string;
  kind: 'asset' | 'tree_file';
  mime?: string;
  size_bytes: number;
  updated_at?: string;
  uploaded?: boolean;
  added: number;
  removed: number;
};

export type WorkspaceList = {
  files: WorkspaceFileItem[];
  change: { added: number; removed: number };
  empty: boolean;
  current_tree_id?: string;
};

export async function listWorkspace(
  threadId: string,
  params?: { q?: string; kind?: string },
): Promise<WorkspaceList> {
  if (!threadId) {
    return { files: [], change: { added: 0, removed: 0 }, empty: true };
  }
  const qs = new URLSearchParams();
  if (params?.q) qs.set('q', params.q);
  if (params?.kind) qs.set('kind', params.kind);
  const query = qs.toString() ? `?${qs.toString()}` : '';
  const data: any = await requestAgentApi(`/workspace/${encodeURIComponent(threadId)}${query}`, {
    method: 'GET',
  });
  const files = (Array.isArray(data?.files) ? data.files : []).map((item: any) => {
    const uploaded = Boolean(item?.uploaded);
    return {
      id: String(item?.id || ''),
      name: String(item?.name || '未命名'),
      path: String(item?.path || item?.name || ''),
      kind: item?.kind === 'asset' ? 'asset' : 'tree_file',
      mime: String(item?.mime || ''),
      size_bytes: Number(item?.size_bytes || 0),
      updated_at: item?.updated_at ? String(item.updated_at) : '',
      uploaded,
      added: Number(item?.added ?? (uploaded ? 1 : 0)),
      removed: Number(item?.removed || 0),
    };
  });
  return {
    files,
    change: {
      added: Number(data?.change?.added || 0),
      removed: Number(data?.change?.removed || 0),
    },
    empty: Boolean(data?.empty ?? files.length === 0),
    current_tree_id: data?.current_tree_id ? String(data.current_tree_id) : '',
  };
}

export async function uploadWorkspaceFile(threadId: string, file: File): Promise<void> {
  const config = getAgentApiConfig();
  const baseUrl = normalizeBaseUrl(config.baseUrl);
  const form = new FormData();
  form.append('file', file);
  const headers = getHeaders(config);
  delete headers['Content-Type'];
  const response = await fetch(`${baseUrl}/workspace/${encodeURIComponent(threadId)}/files`, {
    method: 'POST',
    headers,
    body: form,
  });
  if (!response.ok) {
    let message = `上传失败：${response.status}`;
    try {
      const data = await response.json();
      message = apiErrorMessage(data, message);
    } catch {
      // keep status message
    }
    throw new Error(message);
  }
}

export async function createWorkspaceFile(threadId: string, name: string): Promise<void> {
  await requestAgentApi(`/workspace/${encodeURIComponent(threadId)}/files/new`, {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}

export async function deleteWorkspaceFile(threadId: string, fileId: string): Promise<void> {
  await requestAgentApi(
    `/workspace/${encodeURIComponent(threadId)}/file?file_id=${encodeURIComponent(fileId)}`,
    { method: 'DELETE' },
  );
}

export async function clearWorkspace(threadId: string): Promise<void> {
  await requestAgentApi(
    `/workspace/${encodeURIComponent(threadId)}?confirm=true`,
    { method: 'DELETE' },
  );
}

export function workspaceDownloadUrl(threadId: string, fileId: string): string {
  return `/agent-api/workspace/${encodeURIComponent(threadId)}/download?file_id=${encodeURIComponent(fileId)}`;
}

export async function downloadWorkspaceFile(
  threadId: string,
  fileId: string,
  filename: string,
): Promise<void> {
  const config = getAgentApiConfig();
  const baseUrl = normalizeBaseUrl(config.baseUrl);
  const headers = {
    ...getHeaders(config),
    'X-Harness-Protocol-Version': '1',
  };
  delete headers['Content-Type'];
  const response = await fetch(
    `${baseUrl}/workspace/${encodeURIComponent(threadId)}/download?file_id=${encodeURIComponent(fileId)}`,
    { method: 'GET', headers },
  );
  if (!response.ok) {
    throw new Error(`下载失败：${response.status}`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename || 'file';
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
