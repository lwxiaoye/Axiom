import type {
  AttachmentIssue,
  CitationSource,
  GeneratedFile,
  PlanUpdateEvent,
  ResearchProgressPayload,
  ModelConnectionPayload,
  SubagentStepEvent,
  TaskPlanEvent,
  ToolStepEvent,
} from '../agentApi';
import { isCompanionFile, isDeliverableFile } from './deliverable';
import { mergeResearchTeam, parseResearchTeam } from '../utils/researchTeam';

/**
 * 统一执行时间线（纯 TS，无 Vue/组件依赖）：公开进度、工具调用、沙箱任务、联网搜索、
 * 子智能体共用一条按到达时序交错的步骤流水。useCenterChat 的三个流入口
 * （发送 / HITL 续接 / 后台 Run 重订阅）与历史消息还原共用这里的 reducer，
 * 事件乱序、重复、计时与轨迹重建因此可以在 node 单测里直接验证。
 */

export type StepStatus = 'running' | 'completed' | 'failed';
export type PlanItemStatus = 'pending' | StepStatus | 'skipped' | 'invalidated';

export interface ExecutionPlanItem {
  key: string;
  name: string;
  label: string;
  status: PlanItemStatus;
  detail?: string;
}

export interface ExecutionPlan {
  items: ExecutionPlanItem[];
}

export type AgentStep = AgentStepBase & {
  /** 该步骤发生时正在进行的语义任务步骤（taskPlan item.key；历史消息里可能是后端遗留的
   *  title，见 buildExecutionRows 的兼容匹配）：执行卡按它把详细步骤交错嵌到对应计划步骤下
   *  （Codex 式）。计划出现前的步骤没有该值，平铺在最上；计划已出现过但当前步骤没落在
   *  任何一项上（比如全部收尾后又发生的收尾动作）打尾部哨兵，平铺在最下，不与「计划出现前」混淆。 */
  planKey?: string;
  /**
   * 实时 commentary 的短命流式定位键。只存在于当前页面的几百毫秒绘制窗口，
   * 不进 SSE 协议、不持久化；历史回放仍直接使用已落库的完整 text。
   */
  liveStreamKey?: string;
};

type AgentStepBase =
  | { kind: 'thinking'; text: string; status?: StepStatus; seconds?: number; startedAt?: number }
  /** Codex ContextCompaction：进行中 shimmer Compacting context，完成后 Context compacted */
  | { kind: 'compaction'; status?: StepStatus; seconds?: number; startedAt?: number }
  /** 轮间衔接语（message.commentary 第 2 段起）：模型在两次工具调用之间对用户说的话 */
  | {
      kind: 'note';
      text: string;
      /** Ephemeral platform-owned provider recovery notice. */
      connectionStatus?: ModelConnectionPayload['status'];
    }
  | {
      kind: 'tool';
      name: string;
      /** Stable Responses call identity; absent only on historical protocol-v1 rows. */
      callId?: string;
      label: string;
      status: StepStatus;
      /** 模型现写的本次调用意图（任务语言一句话）：行标题优先用它，缺省回退 label */
      intent?: string;
      detail?: string;
      urls?: string[];
      /** search_web 命中数：保留给来源面板和内部统计，不进入执行行标题。 */
      count?: number;
      /**
       * search_web 管线里真正抓到正文的结果页。
       * 挂在搜索步骤自身上，不再另起一行「浏览 N 个页面」——否则放大镜搜天气、
       * 地球打开无关标题，图标与步骤名看起来像两件平级的事。
       */
      pages?: Array<{ title: string; url: string }>;
      /** 页面快照 data URI（browser_fetch 抓完就给用户看）。只用于展示，不进模型上下文。 */
      shot?: string;
      /** 长任务（bash）当前真实阶段；不伪造百分比 */
      stage?: string;
      /** 服务端 tool.started 时间（ms epoch），完成时与终态事件时间差出 durationMs */
      startedAt?: number;
      /** 运行中由 tool.progress/heartbeat 持续刷新的已用时 */
      elapsedMs?: number;
      /** 终态定格用时（服务端起止时间差） */
      durationMs?: number;
      operation?: string;
      /** 子智能体工作流当前节点行所属的运行档；同一运行档只保留一个当前节点行。 */
      runKey?: string;
      target?: string;
      fileId?: string;
      added?: number;
      removed?: number;
      /** 工具返回的可展示摘要；只显示截断后的事实结果，不展示隐藏思维。 */
      preview?: string;
      /** 技能不可用告警（v3.0）：bash/use_skill 回执命中「没有挂进沙箱/取包失败/暂时
       *  不可用」等关键字时的琥珀色警示行。实时与历史回放同源（都从 preview 提取）。 */
      warning?: string;
      /** bash 实际执行的命令全文（cap 4000），仅在可展开面板展示。 */
      command?: string;
      error?: string;
    }
  | { kind: 'read'; pages: Array<{ title: string; url: string }>; showAll?: boolean }
  | {
      kind: 'subagent';
      name: string;
      label: string;
      status: StepStatus;
      task?: string;
      preview?: string;
      runKey?: string;
    }
  /** 仅用于 MessageList 的多子智能体聚合展示，不会由后端事件或历史轨迹直接生成。 */
  | {
      kind: 'subagentGroup';
      count: number;
      running: boolean;
      expanded: boolean;
    }
  /** 仅用于 MessageList 的同类步骤归拢展示（2026-07-24 拍板）：连续、同族、已完成、
   *  无失败的步骤折成一组；同 subagentGroup，不会由事件/轨迹直接生成。
   *  2026-07-27 从「只收沙箱执行」泛化到读取/下载/浏览/搜索等族——真机一次仓库
   *  分析吐出连续 14 行「读取 xxx」+ 12 行「获取 xxx」，不折就是一屏流水账。 */
  | {
      kind: 'runGroup';
      count: number;
      expanded: boolean;
      groupKey: string;
      /** 组头文案/计数单位/图标。旧数据无这三个字段时按「沙箱探查 · N 步」渲染，行为不变。 */
      label?: string;
      unit?: string;
      icon?: string;
      /** 派生分组族标识，只用于组头的精确文案和交互样式。 */
      familyId?: string;
      /** 折叠组是否允许展开成员动作；工具输出仍由 MessageList 单独控制。 */
      expandable?: boolean;
      /** 组内失败成员数（2026-07-29）。「查阅网页」族**失败也会被折进来**（见 hit 判定），
       *  而组头此前把状态硬写成 completed、文案硬写「· 已完成」——3 次抓取里挂了 1 次，
       *  收起态显示「查阅网页 · 3 次 · 已完成」，与单行失败被伪装成成功是同一个母题。
       *  有了这个计数，组头才能如实说「2 成功 / 1 失败」。 */
      failedCount?: number;
      /** 组内成员的网页快照（最多 3 张，2026-07-28）。
       *  「查阅网页」族连抓 3 次以上就会折成一行，而那恰恰是最该让用户感到「它在干活」的
       *  时候——成员行连同缩略图一起被藏起来，折叠就把这份实感也折没了。组头带几张小图，
       *  收起状态下也看得见它在翻哪些页面。 */
      shots?: string[];
    }
  | {
      kind: 'artifact';
      label: string;
      status: StepStatus;
      files: GeneratedFile[];
    }
  | {
      kind: 'verification';
      label: string;
      status: StepStatus;
      reviewStatus?: string;
    };

/** 把 Markdown 正文压成一行纯文本摘要（执行团队行/卡的副标题用）。
 *  子智能体的交付常是整篇带标题、强调与列表语法的报告，原样塞进一行会露出满屏符号。
 *  只做展示层降噪：去标题井号、强调星号、列表符号、链接语法与多余空白，取首个有内容的段落。 */
export function plainSummary(text: string | undefined | null, limit = 120): string {
  const raw = String(text || '').trim();
  if (!raw) return '';
  const flat = raw
    .replace(/```[\s\S]*?```/g, ' ') // 代码块整段丢弃
    .replace(/^\s{0,3}#{1,6}\s*/gm, '') // 标题井号
    .replace(/^\s{0,3}[-*+]\s+/gm, '') // 无序列表符
    .replace(/^\s{0,3}\d+[.)]\s+/gm, '') // 有序列表序号
    .replace(/^\s{0,3}>\s?/gm, '') // 引用
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '') // 图片
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1') // 链接留文字
    .replace(/(\*\*|__|\*|_|`|~~)/g, '') // 强调/行内代码
    .replace(/\s+/g, ' ')
    .trim();
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat;
}

/** 交付验收单（执行团队一期，subagent.review 帧）：逐条裁定 + 计数。
    页面口径：成员卡只显示 passedCount/total 正向计数，逐条明细供 @ 窗与总结 */
export interface SubagentReview {
  verdicts: Array<{ criterion: string; passed: boolean | null; evidence?: string }>;
  passedCount: number;
  total: number;
}

/** 子智能体一次调用的公开运行档：委派任务 + 逐节点执行 + 输出 */
export interface SubagentRun {
  id: string; // subagent id
  runKey: string; // 每次调用唯一（id + 序号）
  name: string;
  /** 委派帧携带的真实工作台头像；随运行档持久化，避免依赖当前 @ 候选目录。 */
  icon?: string;
  task?: string; // 主对话委派给它的自包含任务
  status: StepStatus;
  nodes: Array<{ label: string; status: string }>; // 工作流逐节点执行
  output: string; // 节点输出累积
  /** 子智能体当前连接的思考流；不进历史回放 */
  reasoning?: string;
  preview?: string; // 最终结果摘要
  error?: string;
  /** 历史回放归一出的「已中断」：status 仍归 failed（复用失败态视觉/收尾逻辑），
      但消费方文案不该写「失败」——中断≠任务失败 */
  interrupted?: boolean;
  // ---- 执行团队一期（2026-07-27）：委派扩展与验收，全部可缺省 ----
  /** 模型按场景生成的岗位名（委派时冻结，如「数据分析师」）；缺省回退 name */
  roleName?: string;
  /** AXIOM Agent 在本次任务里的场景化身份（如「审阅协调人」）；缺省回退 AXIOM Agent。
      跟着委派帧走：同一轮的多次委派携带同一个值 */
  managerRole?: string;
  /** 委派任务书里的子任务清单（该成员的工作拆解，展示用，不造假进度） */
  subtasks?: string[];
  /** 委派时的验收标准（3–5 条） */
  acceptanceCriteria?: string[];
  /** 验收单（逐条裁定；completed 后才有） */
  review?: SubagentReview;
  /** 验收摘要（completed 帧带的 passed_count/total，与 review 同源冗余） */
  acceptance?: { passedCount: number; total: number };
  /** 子智能体本次委派产生的持久化文件，用于实时/历史工作窗口下载卡 */
  files?: GeneratedFile[];
  /** 同一身份被委派了几次（mergeTeamMembers 合并出来的展示字段，>1 才有意义）。
      注意它不是「团队规模」——团队规模是**去重后的成员数**。 */
  callCount?: number;
}

/** reducer 触碰到的消息字段最小集：MessageList 的 ChatMessage 结构性满足它 */
export interface ExecutionMessage {
  content: string;
  /** @deprecated 2026-08-09 不再展示续做注记；字段保留仅兼容旧本地消息对象 */
  resumeNote?: string;
  /** 历史回放出来的内部推荐智能体 id（2026-07-29）：只带 id，由调用方用当前应用列表
   *  现场匹配成卡片——存整卡快照的话，智能体改名/下架后卡面与真实应用对不上。 */
  recommendedAgentIds?: string[];
  /** 开场白（首段过程说明，ChatGPT 式「先答一句」）：渲染在执行时间线上方，不属于正文 */
  preamble?: string;
  /** 当前页面上首段 commentary 正在渐进绘制时的短命定位键。 */
  preambleStreamKey?: string;
  /** 流式准备阶段的临时确认；首个模型开场白到达前可见，随后应被它替换。 */
  preambleIsInitialProgress?: boolean;
  /**
   * 对话层硬类型（P0 架构）：这一段用户可见文案属于哪一层。
   * - ack：插话/系统确认（「明白…」）——轻量，不当完整终答
   * - commentary：过程旁白（工具间衔接语）
   * - final：终答正文
   * - system_note：平台注记（续做提示、空段、错误脚注）
   * 缺省时按历史行为渲染（兼容旧消息）。
   */
  narrativeKind?: 'ack' | 'commentary' | 'final' | 'system_note';
  /** 计划报告（计划轮那份计划）：渲染成一张独立卡片，不是开场白也不是正文（2026-07-28） */
  planReport?: string;
  /** 用户已同意执行：执行轮正文不再按计划卡藏起来。 */
  planExecutionUnlocked?: boolean;
  /** 上下文压缩是已发生的公开运行事实，仅展示简短注记。 */
  compactedNote?: string;
  /** 当前活跃连接的 reasoning_content 尾窗；有公开正文后撤下，不落原始 token。 */
  reasoningSummary?: string;
  /** 当前 burst 已封口；下一 burst 首 token 到达时原位替换旧尾窗。 */
  reasoningPendingReset?: boolean;
  executionCollapsed?: boolean;
  /** 自动折叠一次性闸：true 后 revealAssistantOutput 不再动折叠态（用户手动操作也置 true） */
  executionAutoCollapsed?: boolean;
  /** 模型已实际发出的工具调用清单；不是根据文案猜测的计划。 */
  executionPlan?: ExecutionPlan;
  /** 模型拆解的语义任务步骤（update_plan，面向用户的高层阶段）：整表替换，供「任务协作」渲染 */
  taskPlan?: Array<{
    key: string;
    title: string;
    status: PlanItemStatus;
    detail?: string;
    acceptance?: string;
    blocked?: boolean;
  }>;
  taskPlanPrevious?: Array<{
    key: string;
    title: string;
    status: PlanItemStatus;
    detail?: string;
    acceptance?: string;
  }>;
  taskPlanVersion?: number;
  taskPlanApprovedVersion?: number;
  taskPlanDiverged?: boolean;
  /** 本轮目标契约（GoalContract）：任务协作面板展示，可纠正方向 */
  taskGoalContract?: {
    goal?: string;
    deliverable?: string;
    success_criteria?: string[];
    forbidden?: string[];
    budget_hint?: string;
  };
  /** 当前正在进行的任务步骤 key（applyTaskPlan 维护，不是 title——同批步骤标题可能重复，
   *  key 才是稳定分组依据）：新追加的时间线步骤据此打 planKey */
  taskPlanActiveKey?: string;
  /** 一次性闸：任务计划是否出现过至少一次（applyTaskPlan 首次收到非空 steps 时置 true，
   *  之后不再复位）。全部步骤收尾后 taskPlanActiveKey 会变空，但计划仍算"出现过"——据此把
   *  收尾后新发生的步骤送进时间线尾部而非误判成"计划出现前" */
  taskPlanEverStarted?: boolean;
  agentSteps?: AgentStep[];
  toolSteps?: Array<{ name: string; callId?: string; status: StepStatus }>;
  /** 活跃流中的逐页产物预览；不持久化，终稿卡使用真实产物。 */
  artifactPages?: ArtifactPageView[];
  subagentCalls?: Array<{ name: string; status: StepStatus }>;
  subagentRuns?: SubagentRun[];
  /** 任务级服务端计时（run.started/run.completed 的信封时间）；断线重连不重置 */
  runStartedAt?: number;
  runCompletedAt?: number;
  runDurationMs?: number;
  /** 用户主动停止：标题显示「已停止」，未完步骤统一收尾 */
  runCancelled?: boolean;
  /** Completion Verifier 裁定为部分完成；不得投影成 completed 或 failed。 */
  runPartial?: boolean;
  /** 整轮终态（任务模式设计稿 §14）：只在 run 真正以失败收尾时置位——由 markRunFailed
   *  （run.failed/error 终态事件）或历史回放 execution_trace.status==='failed' 写入，
   *  与期间某次工具/子智能体重试失败无关。同一逻辑步骤先 failed 后 succeeded、且整轮最终
   *  以 markRunCompleted 收尾时，本字段保持 false——不被历史失败 attempt 污染整轮判定。 */
  runFailed?: boolean;
  /** Shared public failure reason, preserved when reopening the transcript. */
  error?: string | null;
  /** 本轮 Profile：plan / research / standard。计划报告分流依赖它。 */
  agentMode?: string;
  generatedFiles?: GeneratedFile[];
  /** 深度研究阶段机进度（research.progress），只驱动状态行，不另开过程页。 */
  researchProgress?: ResearchProgressPayload;
  /** 已持久化的 capability.loaded 事实，用于实时/回放去重。 */
  loadedCapabilities?: string[];
  citations?: CitationSource[];
  /** 附件读取降级（attachments.status，P0）：未完整读取的附件清单，回答上方渲染提示条 */
  attachmentIssues?: AttachmentIssue[];
  /** HITL 恢复桥（复用 submitResume 通道）：任务卡事件写入 run_id/resume_id/type */
  interactive?: { run_id?: string; resume_id?: string; type?: string; params?: any; ask_user?: boolean } | null;
  routedAgent?: string;
  externalRecs?: Array<{ id: string; name: string; description?: string; url?: string }>;
}

/** 工具或模型请求用户补充信息时的通用问题卡。 */
export interface QuestionCardView {
  runId: string;
  resumeId?: string;
  goal?: string;
  questions: Array<{
    key: string;
    title: string;
    why?: string;
    kind: 'single' | 'multiple' | 'text' | 'tags';
    options?: Array<{ value: string; label?: string; description?: string; recommended?: boolean }>;
    required?: boolean;
    allow_custom?: boolean;
    allow_uncertain?: boolean;
    prefill?: unknown;
    placeholder?: string;
  }>;
  error?: string;
  submitted?: boolean;
}

/** 历史消息接口随带的执行轨迹（后端由已持久化的 Run/Event 聚合，旧数据缺失时为 null） */
export type ExecutionTracePayload = {
  /** 与这份快照同批投影到的最后持久事件序号。 */
  event_cursor?: number | null;
  startedAt?: number | null;
  completedAt?: number | null;
  durationMs?: number | null;
  status?: string;
  error?: string | null;
  plan?: unknown[];
  /** 模型 update_plan 拆解的语义任务步骤（历史回放）：整表快照，最新一版 */
  task_plan?: unknown[] | null;
  /** 本轮目标契约回放 */
  goal_contract?: Record<string, unknown> | null;
  approved_version?: number | null;
  plan_version?: number | null;
  diverged?: boolean;
  steps?: unknown[];
  /** 子智能体协作档：委派任务、节点状态与终态摘要（不含 token 级思考原文） */
  subagents?: unknown[];
  files?: GeneratedFile[];
  /** 附件读取降级回放（attachments.status 事件） */
  attachments_status?: AttachmentIssue[] | null;
  /** 开场白（首段过程说明）回放：message.commentary 事件重建 */
  preamble?: string | null;
  /** 计划报告回放：带 kind='plan' 的 message.commentary 事件重建 */
  plan_report?: string | null;
  /** 每轮思考的可展开正文回放；原始 delta 不进入该字段。 */
  reasoning_summary?: string | null;
  reasoning_seconds?: number | null;
  /** 常驻标记回放（2026-07-29）：上下文压缩提示 / 路由到的智能体 / 外部推荐 / 内部推荐 id。
   *  这四样在实时侧一旦出现就常驻在那条助手消息上，刷新后没了就是真缺口。 */
  compacted_note?: string | null;
  routed_agent?: { id?: string; name?: string } | null;
  recommendations?: any[] | null;
  recommended_agent_ids?: string[] | null;
  recommendation_meta?: {
    intent?: 'explicit_request' | 'capability_gap' | string;
    confidence?: string;
    reasons?: Record<string, string>;
  } | null;
  loaded_capabilities?: string[] | null;
  agent_mode?: string | null;
  research_progress?: ResearchProgressPayload | null;
  /** 同一 Run 在用户插话处封存的历史执行段；最终段仍保留在顶层字段。 */
  segments?: Array<{
    inputMessageId: number;
    inputId?: string | null;
    startSequence?: number | null;
    endSequence?: number | null;
    startedAt?: number | null;
    completedAt?: number | null;
    durationMs?: number | null;
    steps?: unknown[];
    preamble?: string | null;
    plan_report?: string | null;
    reasoning_summary?: string | null;
    reasoning_seconds?: number | null;
    status?: string;
  }> | null;
} | null;

/** ToolSpec 名称的纯展示文案，不参与权限和调度。 */
export const TIMELINE_TOOL_LABELS: Record<string, { running: string; completed: string; failed: string }> = {
  search_web: { running: '正在搜索网页…', completed: '已搜索网页', failed: '网页搜索失败' },
  deep_read: { running: '正在深读网页…', completed: '已深读网页', failed: '网页深读失败' },
  search_knowledge: { running: '正在检索知识库…', completed: '已检索知识库', failed: '知识库检索失败' },
  edit_file: { running: '正在修改文件…', completed: '已修改文件', failed: '修改文件失败' },
  read_file: { running: '正在读取文件…', completed: '已读取文件', failed: '读取文件失败' },
  // 2026-07-27 新工具集。缺了就落到兜底文案「正在调用 bash…」——把内部工具名直接
  // 摊给用户看，正是这张表存在的理由（模型省略可选 intent 时就会走到兜底）。
  bash: { running: '正在沙箱中执行…', completed: '已在沙箱中完成处理', failed: '沙箱执行失败' },
  glob: { running: '正在查看我的文件…', completed: '已查看我的文件', failed: '查看文件失败' },
  write_file: { running: '正在写入文件…', completed: '已写入文件', failed: '写入文件失败' },
  download_url: { running: '正在下载文件…', completed: '已下载文件', failed: '下载文件失败' },
  browser_fetch: { running: '正在打开网页…', completed: '已读取网页内容', failed: '网页读取失败' },
  // 有状态浏览（2026-07-27）：文案要让用户看出「它在页面上动手」，与只读抓取区分开
  browser_open: { running: '正在打开网页…', completed: '已打开网页', failed: '网页打开失败' },
  browser_act: { running: '正在操作页面…', completed: '已操作页面', failed: '页面操作失败' },
  browser_close: { running: '正在关闭网页…', completed: '已关闭网页', failed: '网页关闭失败' },
  call_subagent: { running: '正在委派子智能体…', completed: '已委派子智能体', failed: '子智能体委派失败' },
  ask_user_choice: { running: '正在请求补充信息…', completed: '已请求补充信息', failed: '请求补充信息失败' },
  fetch_tool_result: { running: '正在读取完整结果…', completed: '已读取完整结果', failed: '读取完整结果失败' },
  use_skill: { running: '正在读取技能…', completed: '已读取技能', failed: '读取技能失败' },
  get_interview_session: { running: '正在查看面试材料…', completed: '已查看面试材料', failed: '查看面试材料失败' },
  commit_interview_turn: { running: '正在准备下一问…', completed: '已准备下一问', failed: '准备下一问失败' },
};

/** 会产出交付文件、因而在时间线上生成产物行的当前工具。 */
const ARTIFACT_PRODUCERS = new Set([
  'bash', 'write_file', 'download_url', 'edit_file', 'research',
]);

// 连接器工具（2026-07-29 补）：名字是 `{provider}_{action}` **动态拼**出来的
// （connectors.py 的 _safe_name），静态表永远枚举不完 —— 而兜底文案会把内部
// snake_case 标识符直接摊给用户看：「正在调用 qqmail_list_recent…」夹在一排
// 「已读取文件」「已搜索网页」中间，像半成品。这里按 provider + action 两段拆开翻译。
const CONNECTOR_PROVIDER_NAMES: Record<string, string> = {
  qqmail: 'QQ 邮箱', gmail: 'Gmail', outlook: 'Outlook', imapmail: '邮箱',
  github: 'GitHub', gitlab: 'GitLab', notion: 'Notion', slack: 'Slack',
  feishu: '飞书', dingtalk: '钉钉', wecom: '企业微信', jira: 'Jira',
};
const CONNECTOR_ACTION_VERBS: Array<{ test: RegExp; running: string; done: string }> = [
  { test: /list_recent|recent|list$/, running: '查看最近内容', done: '查看最近内容' },
  { test: /read_message|read$|get$|detail/, running: '读取内容', done: '读取内容' },
  { test: /search|query|find/, running: '搜索', done: '搜索' },
  { test: /send|create|post|reply/, running: '提交内容', done: '提交内容' },
];

function connectorToolLabels(name: string) {
  const idx = name.indexOf('_');
  if (idx <= 0) return null;
  const provider = CONNECTOR_PROVIDER_NAMES[name.slice(0, idx).toLowerCase()];
  if (!provider) return null;
  const action = name.slice(idx + 1).toLowerCase();
  const verb = CONNECTOR_ACTION_VERBS.find((v) => v.test.test(action));
  const doing = verb ? verb.running : '处理';
  const done = verb ? verb.done : '处理';
  return {
    running: `正在${doing}（${provider}）…`,
    completed: `已${done}（${provider}）`,
    failed: `${provider}${done}失败`,
  };
}

function toolLabels(name: string) {
  return (
    TIMELINE_TOOL_LABELS[name]
    || connectorToolLabels(name)
    || {
      running: `正在调用 ${name}…`,
      completed: `已调用 ${name}`,
      failed: `${name} 调用失败`,
    }
  );
}

function planLabel(name: string): string {
  const labels = toolLabels(name);
  return labels.completed.replace(/^已/, '') || name || '执行操作';
}

const COMPLETED_ACTION_LABELS: Record<string, string> = {
  read: '已读取',
  list: '已查看',
  search: '已搜索',
  edit: '已编辑',
  create: '已创建',
  execute: '已执行',
  // 2026-07-27 新工具的 action.operation（见后端 _file_action 的第一个参数）
  write: '已写入',
  glob: '已查看',
  download: '已下载',
  bash: '已执行',
  fetch: '已读取',
};

/** action.operation 不可信、必须让位给按工具名文案的工具。
 *
 * browser_fetch / browser_open / browser_act 在后端统统写死 `operation:"fetch"`
 * （chat/tools/browser.py 的三处 _sink），通用文案「已读取」会把**有副作用**的动作
 * （点按钮、键入文字、提交表单）说成只读抓取——这是在骗用户，比文案难看严重得多。 */
const ACTION_LABEL_UNTRUSTED = new Set([
  'browser_fetch', 'browser_open', 'browser_act', 'browser_close',
  // bash 的 operation 是 execute/bash，套上「已执行」等于没说干了什么。
  'bash',
]);

function looksLikeUserTaskPhrase(text?: string): boolean {
  const t = String(text || '').trim();
  if (!t || t.length > 80) return false;
  if (/^[a-z][a-z0-9_]{2,}$/.test(t)) return false;
  if (/[/\\$`|&;<>]/.test(t)) return false;
  if (/^(cd|ls|cat|python|node|npm|npx|pip|mkdir|rm|cp|mv|bash|sh|git)\b/i.test(t)) return false;
  return /[\u4e00-\u9fff]/.test(t) || /\s/.test(t);
}

function bashTaskPhrase(input: { intent?: string; target?: string; detail?: string }): string {
  for (const candidate of [input.intent, input.detail, input.target]) {
    if (looksLikeUserTaskPhrase(candidate)) return String(candidate).trim();
  }
  return '';
}

/** 沙箱行标题：只说这次干了什么，不把「已执行」和任务短语拆成父子级。 */
export function bashRowTitle(step: {
  intent?: string;
  target?: string;
  detail?: string;
  label?: string;
  status?: StepStatus;
}): string {
  const phrase = bashTaskPhrase(step);
  if (phrase) return step.status === 'failed' ? `${phrase}失败` : phrase;
  if (step.label && step.label !== '已执行') return step.label;
  return step.status === 'failed' ? '沙箱执行失败' : '已在沙箱中完成处理';
}

export interface ToolStepDisplay {
  label: string;
  target?: string;
  detail?: string;
}

/**
 * 工具行的显示归一（label / target / detail）——**实时 reducer 与历史回放共用这一个函数**。
 *
 * 为什么必须共用而不是两边各写一份：2026-07-27 的事故就是实时侧给 bash/glob 加了降噪、
 * 回放侧（restoreStep）没跟上，刷新一次同一条消息就变了样——bash 行铺出 240 字 shell 命令、
 * glob 的匹配式 `*` 还被 isFileActionStep 渲染成可点的「跳我的文件」按钮。只要两条路径各留
 * 一份副本，下一次加降噪规则就会再漏一次。
 */
export function toolStepDisplay(input: {
  name: string;
  status: StepStatus;
  operation?: string;
  target?: string;
  detail?: string;
  intent?: string;
  /** meta.count：glob 的文件数、search_web 的结果数（轨迹里持久化，搜索行不直接展示） */
  count?: number;
  /** P2.4：后端 ToolObservation.ui.summary 产品文案（优先于静态工具名表） */
  summary?: string;
}): ToolStepDisplay {
  const { name, status, operation = '' } = input;
  const labels = toolLabels(name);
  let label = status === 'running' ? labels.running : status === 'failed' ? labels.failed : labels.completed;
  let target = input.target || undefined;
  let detail = input.detail || undefined;
  // 通用动作文案（「已读取 报告.md」比「已读取文件 报告.md」少复读一次宾语）只在 operation
  // 可信时接管；且只接管完成态——失败态套上「已写入」等于把没做成的事说成做成了
  if (status === 'completed' && operation && !ACTION_LABEL_UNTRUSTED.has(name)) {
    label = COMPLETED_ACTION_LABELS[operation] || label;
  }
  // glob：文件数量是执行器的内部统计，用户从「已查看文件区现有素材」已经
  // 能理解这一步做了什么；把「42 个文件」附在行尾只会让过程像日志。清单仍可在 preview
  // 展开查看。
  if (name === 'glob') {
    target = undefined;
    detail = undefined;
  }
  // bash：行内只说这次干了什么（intent），不要「已执行 > 子标题」。
  // 命令、产物计数进可展开面板，不跟在箭头后面当子级。
  if (name === 'bash') {
    const phrase = bashTaskPhrase(input);
    if (phrase) {
      label = status === 'failed' ? `${phrase}失败` : phrase;
    } else if (status === 'completed') {
      label = labels.completed;
    }
    target = undefined;
    detail = undefined;
  }
  if (name === 'use_skill') {
    const matched = /《(.+?)》/u.exec(String(target || ''));
    const skillName = matched?.[1] || '';
    if (skillName) {
      label = status === 'running'
        ? `正在读取 ${skillName} 技能…`
        : status === 'failed'
          ? `读取 ${skillName} 技能失败`
          : `已读取 ${skillName} 技能`;
      target = undefined;
    }
  }
  // P2.4：后端 ToolObservation.ui.summary 最后覆盖
  if (status === 'completed' && input.summary && String(input.summary).trim()) {
    label = String(input.summary).trim().slice(0, 80);
  }
  return { label, target, detail };
}

type SearchWebDisplayStep = {
  status?: StepStatus;
  /** 工具入参，仅保留以证明完成态展示不依赖它们。 */
  detail?: string;
  intent?: string;
  pages?: Array<{ title: string; url: string }>;
  urls?: string[];
  error?: string;
};

function searchWebHost(url: string): string {
  const value = String(url || '').trim();
  if (!value) return '';
  try {
    return new URL(value).hostname.replace(/^www\./i, '');
  } catch {
    return '';
  }
}

function searchWebPageSummary(step: SearchWebDisplayStep): string {
  const firstPage = step.pages?.find((page) => page && (page.title || page.url));
  const pageUrl = String(firstPage?.url || step.urls?.[0] || '');
  const host = searchWebHost(pageUrl);
  const rawTitle = String(firstPage?.title || '').replace(/\s+/g, ' ').trim();
  const title = rawTitle && rawTitle !== pageUrl ? rawTitle : '';
  if (title && host) return `${title} · ${host}`;
  return title || host;
}

/** 网页搜索的执行行只陈述真实结果页，不复述用户输入或搜索 query。
 * 查询词是工具入参，命中数量属于来源面板；二者都不进完成态时间线。 */
export function searchWebStepTitle(step: SearchWebDisplayStep, live = step.status === 'running'): string {
  if (live) return '正在搜索网页…';
  if (step.status === 'failed') return '网页搜索失败';
  const page = searchWebPageSummary(step);
  return page ? `已搜索网页：${page}` : '已搜索网页';
}

/**
 * 时间线流光只给「当前还在做」的动作。
 *
 * 模型在 tool.completed 到达前就会开始下一段思考/叙述；若只看 step.status，
 * 已做完的「正在检索网页」会一直 shimmer。后面出现思考、叙述或已收尾的动作，
 * 就不再把这一步当进行中。并行仍在 running 的工具互不取消。
 */
export function isLiveRunningAction(steps: AgentStep[] | undefined, stepIndex: number): boolean {
  const list = steps || [];
  const step = list[stepIndex];
  if (!step) return false;
  if (!('status' in step) || step.status !== 'running') return false;
  if (step.kind === 'thinking' || step.kind === 'note' || step.kind === 'compaction') return false;
  for (let i = stepIndex + 1; i < list.length; i += 1) {
    const later = list[i];
    if (
      later.kind === 'thinking'
      || later.kind === 'note'
      || later.kind === 'artifact'
      || later.kind === 'verification'
      || later.kind === 'read'
    ) {
      return false;
    }
    if ((later.kind === 'tool' || later.kind === 'subagent') && later.status !== 'running') {
      return false;
    }
  }
  return true;
}

/** 可展开面板里 `command` 段的表头。
 *
 *  `command` 装的是**实际执行的那一段**（agentApi.ts 取 `args.command`），bash 若落到兜底
 *  的「入参」，就把一条 shell 命令叫成了入参——既不准确，也让「它到底跑了什么」这个最该
 *  一眼看懂的东西显得像调试信息。其余工具的 command 字段本就是空的，兜底极少出现。
 */
export function shellInputTitle(name: string): string {
  if (name === 'bash') return '执行命令';
  return '入参';
}

function updatePlanItem(target: ExecutionMessage, name: string, from: PlanItemStatus[], to: PlanItemStatus) {
  const item = target.executionPlan?.items.find((entry) => entry.name === name && from.includes(entry.status));
  if (item) item.status = to;
}

/** 模型已生成的真实工具调用清单 → 可观察计划。后续轮次增量追加，重放按 key 幂等。 */
export function applyPlanUpdate(target: ExecutionMessage, event: PlanUpdateEvent) {
  if (!target.executionPlan) target.executionPlan = { items: [] };
  const known = new Set(target.executionPlan.items.map((item) => item.key));
  for (const [index, raw] of (event.items || []).entries()) {
    const name = String(raw.name || '');
    if (!name) continue;
    const key = String(raw.key || `round-${event.round || 1}-${index}`);
    if (known.has(key)) continue;
    known.add(key);
    target.executionPlan.items.push({
      key,
      name,
      label: planLabel(name),
      status: 'pending',
      detail: raw.detail ? String(raw.detail) : undefined,
    });
  }
}


/** 后端 update_plan 用 in_progress；SSE 实时路径必须与历史回放同口径。 */
function normalizeTaskPlanStatus(raw: unknown): PlanItemStatus {
  const s = String(raw || '').toLowerCase().trim();
  if (s === 'running' || s === 'in_progress' || s === 'active' || s === 'doing' || s === 'ongoing') {
    return 'running';
  }
  if (s === 'failed' || s === 'error' || s === 'cancelled' || s === 'canceled') {
    return 'failed';
  }
  if (s === 'skipped') return 'skipped';
  if (s === 'invalidated') return 'invalidated';
  if (s === 'completed' || s === 'done' || s === 'finished' || s === 'complete' || s === 'ok' || s === 'succeeded') {
    return 'completed';
  }
  return 'pending';
}

/** 语义任务计划（update_plan / task.plan）：模型每次整表回传，直接替换——不做增量合并，
    因为步骤集合与顺序都可能被模型重排。空标题剔除，状态收敛到 PlanItemStatus。 */
export function applyTaskPlan(target: ExecutionMessage, event: TaskPlanEvent) {
  const steps = (event.steps || [])
    .map((s) => ({
      key: String(s.key || ''),
      title: String(s.title || '').trim(),
      // 后端 provisional/update_plan 常用 in_progress；实时 SSE 必须映射为 running。
      status: normalizeTaskPlanStatus(s.status),
      detail: s.detail ? String(s.detail) : undefined,
      acceptance: s.acceptance ? String(s.acceptance) : undefined,
      blocked: Boolean((s as { blocked?: boolean }).blocked),
    }))
    .filter((s) => s.title);
  // 后端 TaskPlanEvent.steps[].key 实测恒为空串（未生成/校验）：按「标题+同名序号」兜底唯一化。
  // 不能用位置下标：模型整表重排是本函数 JSDoc 自认的合法场景，重排后下标 key 会指向别的
  // 步骤，让早前记在时间线步骤上的 planKey 漂移进错误计划项的桶；标题 key 在重排下稳定，
  // 重复标题靠 #序号区分（同名项之间的重排本就无从分辨），同一快照内仍保证唯一
  const dupSeq = new Map<string, number>();
  for (const s of steps) {
    if (!s.key) {
      const n = dupSeq.get(s.title) || 0;
      dupSeq.set(s.title, n + 1);
      s.key = `plan-${s.title}#${n}`;
    }
  }
  const nextVersion = Math.max(0, Number(event.plan_version) || 0);
  const prevVersion = Math.max(0, Number(target.taskPlanVersion) || 0);
  if (target.taskPlan?.length && nextVersion > prevVersion) {
    target.taskPlanPrevious = target.taskPlan;
  }
  target.taskPlan = steps.length ? steps : undefined;
  if (steps.length) target.taskPlanEverStarted = true;
  if (nextVersion) target.taskPlanVersion = nextVersion;
  if (event.approved_version != null) {
    target.taskPlanApprovedVersion = Math.max(0, Number(event.approved_version) || 0);
  }
  if (event.diverged) target.taskPlanDiverged = true;
  // plan_version 每次 upsert 都递增（含纯 status 推进）；事件不带版本时 nextVersion=0，
  // 不能拿 0 去和 approved 比较，否则会把已置真的 diverged 清掉。
  const currentVersion = nextVersion || Number(target.taskPlanVersion) || 0;
  if (
    target.taskPlanApprovedVersion != null
    && currentVersion > 0
    && currentVersion <= Number(target.taskPlanApprovedVersion)
  ) {
    target.taskPlanDiverged = undefined;
  }
  if (event.goal_contract && typeof event.goal_contract === 'object') {
    target.taskGoalContract = {
      goal: event.goal_contract.goal ? String(event.goal_contract.goal) : undefined,
      deliverable: event.goal_contract.deliverable
        ? String(event.goal_contract.deliverable)
        : undefined,
      success_criteria: Array.isArray(event.goal_contract.success_criteria)
        ? event.goal_contract.success_criteria.map(String)
        : undefined,
      forbidden: Array.isArray(event.goal_contract.forbidden)
        ? event.goal_contract.forbidden.map(String)
        : undefined,
      budget_hint: event.goal_contract.budget_hint
        ? String(event.goal_contract.budget_hint)
        : undefined,
    };
  }
  // 「当前步骤」判定（与后端历史回放同一规则）：在现行步骤（不含已替换的 invalidated）里
  // 取首个 running，否则首个 pending。全完成则无归属。
  const live = visibleTaskPlan(steps);
  const active = live.find((s) => s.status === 'running') || live.find((s) => s.status === 'pending');
  target.taskPlanActiveKey = active?.key;
}

/** 任务协作 / 时间线组头只展示现行步骤。invalidated 是被新版计划替换的残骸，
 *  仍留在权威快照里，但不能插在后面冒充还没做的步骤。 */
export function visibleTaskPlan<T extends { status?: string }>(
  plan: T[] | undefined | null,
): T[] {
  return (plan || []).filter((item) => item.status !== 'invalidated');
}

// 全部任务计划步骤都已收尾（taskPlanActiveKey 因此变回 undefined）后，模型若还触发新动作，
// 这些步骤不该混进「计划出现前」的 pre 桶——用哨兵 planKey 把它们送进 buildExecutionRows 的
// tail 桶；真实的 plan key/title 都不可能等于这个字面量。
const TAIL_PLAN_KEY = '__fallback_tail__';

/** 新建时间线步骤该打的 planKey：有进行中/待办步骤则挂它对应 key；计划已出现过但当前
    全收尾（无归属）则挂尾部哨兵；计划从未出现过则维持 undefined（真正的「计划出现前」）。 */
function currentPlanKey(target: ExecutionMessage): string | undefined {
  return target.taskPlanActiveKey ?? (target.taskPlanEverStarted ? TAIL_PLAN_KEY : undefined);
}

/** call_subagent 编排 chip（ADR-046）：started 入列、completed/failed 收尾——三个流入口共用。
    同时把协作过程插入 agentSteps 时间线（委派任务→结果摘要，与思考片段按时序交错）。 */
export function applySubagentStep(target: ExecutionMessage, ev: SubagentStepEvent) {
  if (!target.subagentCalls) target.subagentCalls = [];
  if (!target.agentSteps) target.agentSteps = [];
  if (!target.subagentRuns) target.subagentRuns = [];
  const name = ev.name || '子智能体';
  // 当前运行档：同 subagent id 的最后一个 running——node/delta/reasoning/收尾都归到它
  const currentRun = () =>
    [...target.subagentRuns!].reverse().find((r) => r.status === 'running' && (!ev.id || r.id === ev.id));

  if (ev.phase === 'preparing') {
    target.executionCollapsed = false;
    target.agentSteps.push({
      kind: 'tool', name: 'call_subagent', operation: 'subagent_prepare',
      label: `正在打开「${name}」并准备委派`, status: 'running',
      planKey: currentPlanKey(target),
    });
    return;
  }

  if (ev.phase === 'started') {
    parkTransientReasoning(target);
    target.executionCollapsed = false;
    updatePlanItem(target, 'call_subagent', ['pending'], 'running');
    const preparingStep = [...target.agentSteps].reverse().find(
      (step) => step.kind === 'tool' && step.name === 'call_subagent'
        && step.operation === 'subagent_prepare' && step.status === 'running',
    );
    if (preparingStep && preparingStep.kind === 'tool') {
      preparingStep.status = 'completed';
      preparingStep.label = `已打开「${name}」，任务已交给它处理`;
    }
    const runKey = `${ev.id || 'sub'}-${target.subagentRuns.length}`;
    target.subagentRuns.push({
      id: ev.id || '', runKey, name, task: ev.task, status: 'running',
      nodes: [], output: '', reasoning: '',
      ...(ev.icon ? { icon: ev.icon } : {}),
      // 执行团队一期：岗位名/子任务清单/验收标准随派发帧入档（缺省不占位）
      ...(ev.roleName ? { roleName: ev.roleName } : {}),
      ...(ev.managerRole ? { managerRole: ev.managerRole } : {}),
      ...(ev.subtasks?.length ? { subtasks: ev.subtasks } : {}),
      ...(ev.acceptanceCriteria?.length ? { acceptanceCriteria: ev.acceptanceCriteria } : {}),
    });
    target.subagentCalls.push({ name, status: 'running' });
    target.agentSteps.push({
      kind: 'subagent', name, status: 'running',
      label: name, task: ev.task, runKey,
      planKey: currentPlanKey(target),
    });
    return;
  }
  // 内部干活流程（供「子智能体工作窗口」实时展示）
  if (ev.phase === 'node') {
    const run = currentRun();
    if (run && ev.label) {
      run.nodes.push({ label: ev.label, status: ev.status || '' });
      const status: StepStatus = ev.status === 'failed'
        ? 'failed'
        : /^(success|succeeded|completed|done)$/i.test(ev.status || '')
          ? 'completed'
          : 'running';
      const currentNodeStep = [...target.agentSteps].reverse().find(
        (step) => step.kind === 'tool' && step.name === 'call_subagent'
          && step.operation === 'subagent_node' && step.runKey === run.runKey,
      );
      const nodeLabel = status === 'failed'
        ? `「${run.name || '子智能体'}」执行“${ev.label}”失败`
        : status === 'completed'
          ? `「${run.name || '子智能体'}」已完成${ev.label}`
          : `「${run.name || '子智能体'}」正在${ev.label}`;
      if (currentNodeStep && currentNodeStep.kind === 'tool') {
        // Codex 的协作项以事件更新原位状态；子智能体内部完整节点留在 run.nodes，
        // 主时间线只显示当前节点，避免多个瞬时 node 在同一帧一起堆出来。
        currentNodeStep.label = nodeLabel;
        currentNodeStep.status = status;
      } else {
        target.agentSteps.push({
          kind: 'tool', name: 'call_subagent', operation: 'subagent_node', runKey: run.runKey,
          label: nodeLabel,
          status, planKey: currentPlanKey(target),
        });
      }
    }
    return;
  }
  if (ev.phase === 'delta') {
    const run = currentRun();
    if (run) run.output += ev.text || '';
    return;
  }
  if (ev.phase === 'reasoning') {
    const run = currentRun();
    if (run && ev.text) run.reasoning = `${run.reasoning || ''}${ev.text}`.slice(-6000);
    return;
  }
  if (ev.phase === 'review') {
    // 执行团队一期：验收单帧（先于 completed 到达）——挂到当前运行档
    const run = currentRun();
    if (run && ev.review) run.review = ev.review;
    return;
  }
  // completed / failed 收尾
  const status = ev.phase === 'failed' ? 'failed' : 'completed';
  updatePlanItem(target, 'call_subagent', ['running', 'pending'], status);
  const run = currentRun();
  if (run) {
    run.status = status;
    // 6000 上限与后端帧一致：结果报告卡（max-height+滚动）可查看完整内容，不再 300 字截断
    run.preview = String(ev.preview || '').slice(0, 6000) || undefined;
    run.error = ev.error ? String(ev.error).slice(0, 300) : undefined;
    // 执行团队一期：收尾帧附带的验收摘要/岗位名
    if (ev.acceptance) run.acceptance = ev.acceptance;
    if (ev.roleName && !run.roleName) run.roleName = ev.roleName;
    if (ev.files?.length) run.files = ev.files;
  }
  const item = [...target.subagentCalls]
    .reverse()
    .find((s) => s.status === 'running' && (!ev.name || s.name === name));
  if (item) item.status = status;
  else target.subagentCalls.push({ name, status });
  const step = [...target.agentSteps]
    .reverse()
    .find((s) => s.kind === 'subagent' && s.status === 'running' && s.name === name);
  if (step && step.kind === 'subagent') {
    step.status = status;
    if (status === 'failed') {
      step.preview = String(ev.preview || ev.error || '').slice(0, 6000) || undefined;
    }
  }
  const nodeStep = [...target.agentSteps].reverse().find(
    (candidate) => candidate.kind === 'tool' && candidate.name === 'call_subagent'
      && candidate.operation === 'subagent_node' && (!run || candidate.runKey === run.runKey),
  );
  if (nodeStep && nodeStep.kind === 'tool' && nodeStep.status === 'running') {
    nodeStep.status = status;
    nodeStep.label = status === 'failed'
      ? `${nodeStep.label.replace('正在', '执行')}失败`
      : nodeStep.label.replace('正在', '已完成');
  }
}

/** 供应商 reasoning_content 的活流：时间线 thinking 行始终累积；顶部尾窗仅在无公开正文时驻留。 */
const REASONING_REPLAY_MAX = 20000;

export function compactReasoningSummary(text: string): string {
  const collapsed = String(text || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
  if (!collapsed) return '';
  if (collapsed.length <= REASONING_REPLAY_MAX) return collapsed;
  let window = collapsed.slice(-REASONING_REPLAY_MAX);
  let boundary = -1;
  for (const sep of ['\n', '。', '！', '？', '. ', '? ', '! ']) {
    const idx = window.indexOf(sep);
    if (idx >= 0 && idx < 240) boundary = Math.max(boundary, idx + sep.length - 1);
  }
  if (boundary >= 0) window = window.slice(boundary + 1).trimStart();
  return window.trim();
}

export function applyCompaction(
  target: ExecutionMessage,
  payload: { status?: string; seconds?: number },
) {
  if (!target.agentSteps) target.agentSteps = [];
  const status = String(payload?.status || 'started');
  const last = [...target.agentSteps].reverse().find((step) => step.kind === 'compaction');
  if (status === 'started') {
    if (last && last.status === 'running') return;
    parkTransientReasoning(target);
    target.agentSteps.push({
      kind: 'compaction',
      status: 'running',
      startedAt: Date.now(),
      planKey: currentPlanKey(target),
    });
    target.executionCollapsed = false;
    target.executionAutoCollapsed = false;
    return;
  }
  const step = (last && last.status === 'running')
    ? last
    : { kind: 'compaction' as const, startedAt: Date.now(), planKey: currentPlanKey(target) };
  if (!last || last.status !== 'running') target.agentSteps.push(step);
  step.status = status === 'failed' ? 'failed' : 'completed';
  if (typeof payload?.seconds === 'number' && payload.seconds > 0) {
    step.seconds = payload.seconds;
  } else if (!step.seconds && step.startedAt) {
    step.seconds = Math.max(1, Math.round((Date.now() - step.startedAt) / 1000));
  }
}

function lastThinkingStep(target: ExecutionMessage) {
  const steps = target.agentSteps || [];
  const last = steps[steps.length - 1];
  return last?.kind === 'thinking' ? last : undefined;
}

/** 完成后标题必须是 Thoughts for Ns：服务端没给 seconds 时用 startedAt，再不行记 1s。 */
function stampThoughtSeconds(
  step: Extract<AgentStep, { kind: 'thinking' }>,
  payloadSeconds?: number,
) {
  if (typeof payloadSeconds === 'number' && Number.isFinite(payloadSeconds) && payloadSeconds > 0) {
    step.seconds = payloadSeconds;
    return;
  }
  if (typeof step.seconds === 'number' && step.seconds > 0) return;
  if (step.startedAt) {
    step.seconds = Math.max(1, Math.round((Date.now() - step.startedAt) / 1000));
    return;
  }
  step.seconds = 1;
}

/** 思考步骤只在收到 reasoning 时插入，发送后不预先占位。 */
export function ensureRunningThought(_target: ExecutionMessage) {
  return;
}

function dropEmptyThought(target: ExecutionMessage, step: Extract<AgentStep, { kind: 'thinking' }>) {
  const steps = target.agentSteps || [];
  const index = steps.lastIndexOf(step);
  if (index >= 0) steps.splice(index, 1);
}

export function applyReasoningDelta(
  target: ExecutionMessage,
  delta: string,
  _fullReasoning: string,
) {
  const text = String(delta || '');
  if (!text) return;
  clearInitialProgressPreamble(target);
  if (!target.agentSteps) target.agentSteps = [];
  const hasBody = Boolean(String(target.content || '').trim());
  if (target.reasoningPendingReset) {
    target.reasoningSummary = '';
    target.reasoningPendingReset = false;
  }
  let step = lastThinkingStep(target);
  if (!step || step.status === 'completed') {
    step = { kind: 'thinking', text: '', status: 'running', startedAt: Date.now(), planKey: currentPlanKey(target) };
    target.agentSteps.push(step);
  }
  if (!step.startedAt) step.startedAt = Date.now();
  step.text = `${step.text || ''}${text}`.slice(-20000);
  step.status = 'running';
  if (hasBody) {
    target.reasoningSummary = '';
  } else {
    target.reasoningSummary = `${target.reasoningSummary || ''}${text}`.slice(-20000);
  }
  target.executionCollapsed = false;
  target.executionAutoCollapsed = false;
}

/** reasoning burst 已结束：时间线收束为可展开正文；下一 burst 首 token 再换新。 */
export function parkTransientReasoning(
  target: ExecutionMessage,
  payload?: { text?: string; seconds?: number },
) {
  const step = lastThinkingStep(target);
  if (step && step.status !== 'completed') {
    const source = payload?.text || step.text || '';
    const body = compactReasoningSummary(source);
    if (!body) {
      dropEmptyThought(target, step);
    } else {
      step.text = body;
      step.status = 'completed';
      stampThoughtSeconds(step, payload?.seconds);
    }
  } else if (!step) {
    const source = payload?.text || target.reasoningSummary || '';
    const body = compactReasoningSummary(source);
    if (body) {
      if (!target.agentSteps) target.agentSteps = [];
      const created: Extract<AgentStep, { kind: 'thinking' }> = {
        kind: 'thinking',
        text: body,
        status: 'completed',
        planKey: currentPlanKey(target),
      };
      stampThoughtSeconds(created, payload?.seconds);
      target.agentSteps.push(created);
    }
  } else if (step.status === 'completed') {
    stampThoughtSeconds(step, payload?.seconds);
    if (payload?.text) step.text = compactReasoningSummary(payload.text) || step.text;
  }
  if (target.reasoningSummary) target.reasoningPendingReset = true;
}

/** 只撤顶部活窗口；时间线 thinking 行保留。 */
export function clearTransientReasoning(target: ExecutionMessage) {
  target.reasoningSummary = '';
  target.reasoningPendingReset = false;
}

/** 过程说明（message.commentary，ChatGPT 式）：任何工具/子智能体动作之前的首段=开场白
    （渲染在执行时间线上方），其余=轮间衔接语（时间线 note 步骤，与动作按时序交错）。
    正文累积已在 agentApi 层剔除本段；流式期间执行卡因这段「假正文」被 revealAssistantOutput
    折叠过，这里重新展开——工具阶段过程应保持可见，真正的答案开始时再折叠。 */
/** P2.5：技术 notice 默认不出时间线——仅保留用户能看懂的过程叙述。 */
function isTechnicalNotice(text: string): boolean {
  const clean = (text || '').trim();
  if (!clean) return true;
  if (/^（⚠️/.test(clean) || /^\(⚠️/.test(clean)) return true;
  if (/\[artifact_validity_gate=/.test(clean)) return true;
  if (/\/workspace\//.test(clean) && /(沙箱|落库|exit_code|stderr|timeout|SIGXFSZ|技能包)/.test(clean)) return true;
  if (/(技能包解析失败|本轮剩余执行时间不足|超过单文件大小上限|不会交付给用户|outputs\/)/.test(clean)) return true;
  return false;
}

/** 后端 _initial_progress_text / 前端乐观占位语：只服务“首帧别空白”，不是模型叙述。 */
export function isSystemInitialProgressPreamble(text: string | null | undefined): boolean {
  const clean = String(text || '').trim();
  if (!clean) return false;
  return (
    clean === '正在处理…' ||
    clean === '正在处理...' ||
    clean === '仍在处理，请稍候…' ||
    clean === '正在检索可核验的资料…' ||
    clean === '正在查看你选中的知识库资料…' ||
    clean === '正在阅读你提供的材料…' ||
    clean === '正在梳理目标和现有条件…' ||
    /^正在(处理|检索|查看|阅读|梳理).{0,24}$/.test(clean)
  );
}

function clearInitialProgressPreamble(target: ExecutionMessage) {
  // 首帧占位语（正在处理…）只在真正开场白/工具/正文到来前短暂存在；
  // 工具已开始或回合已收尾时再挂着会像假进度残留。
  // 兼容两条路径：
  // 1) 实时流：带 preambleIsInitialProgress 标记；
  // 2) 历史回放：后端曾把 kind=initial_progress 误投影进 preamble，刷新后没有标记，
  //    但文案仍是系统占位语——工具/终态到达后同样必须清掉。
  const text = String(target.preamble || '').trim();
  if (!target.preambleIsInitialProgress && !isSystemInitialProgressPreamble(text)) return;
  target.preamble = '';
  target.preambleIsInitialProgress = false;
}

export function isPlanProcessDump(text: string): boolean {
  const clean = String(text || '').trim();
  if (!clean) return false;
  if (/\*\*(?:Context|指导原则|验收|Phase\s*[ABC]|待你拍板)/i.test(clean)) return false;
  if (/(?:^|\n)Phase\s*[ABC]/i.test(clean)) return false;
  if (/待你拍板|验收标准/.test(clean)) return false;
  if (/(?:^|\n)步骤\s*\d+/.test(clean)) return false;
  return /当前搜索服务不可用|没法拉取最新|写正式文档前会再尝试搜索/.test(clean);
}

export function looksLikePlanReport(text: string): boolean {
  const clean = String(text || '').trim();
  if (clean.length < 80) return false;
  // 过程独白（搜不到、先拆步骤）即使很长也不能当成计划卡。
  if (isPlanProcessDump(clean)) return false;
  const hasLabeled = /\*\*(?:Context|指导原则|验收|Phase\s*[ABC]|待你拍板)/i.test(clean);
  const hasPhase = /(?:^|\n)Phase\s*[ABC]/i.test(clean);
  const hasSignoff = /待你拍板|验收标准/.test(clean);
  const hasNumberedStep = /(?:^|\n)步骤\s*\d+/.test(clean);
  if (hasLabeled || hasPhase || hasSignoff || hasNumberedStep) return true;
  // 模型常写自然 Markdown（标题 + 编号步骤），不一定套 Context/Phase 标签。
  if (/(?:^|\n)#{1,3}\s+\S/.test(clean)) return true;
  const numbered = clean.match(/(?:^|\n)\d+\s*(?:[.、．]|\)|）)\s+\S/g) || [];
  return numbered.length >= 2;
}

/** 兼容 v1.104 已落库但未打 kind=plan 的 Responses 计划块。 */
export function extractProposedPlanText(text: string): string | null {
  const raw = String(text || '');
  const complete = [...raw.matchAll(/<proposed_plan>\s*\n([\s\S]*?)\n\s*<\/proposed_plan>/gi)];
  if (complete.length === 1) return String(complete[0][1] || '').trim() || null;
  if (complete.length > 1) return null;
  const opened = /<proposed_plan>\s*(?:\n|$)/i.exec(raw);
  if (!opened || opened.index == null) return null;
  const body = raw
    .slice(opened.index + opened[0].length)
    .replace(/\s*<\/proposed_plan>\s*$/i, '')
    .trim();
  return body || null;
}

export function mergePlanReport(previous: string | undefined, incoming: string): string {
  const prev = String(previous || '').trim();
  const next = String(incoming || '').trim();
  if (!next) return prev;
  if (!prev || prev === next) return next;
  if (next.includes(prev) && next.length >= prev.length) return next;
  if (prev.includes(next)) return prev;
  // 后到的短 intro（确认卡那句）不得盖掉已经画出来的完整计划报告。
  if (next.length + 80 < prev.length) return prev;
  return `${prev}\n\n${next}`;
}

function compactPlanText(text: string): string {
  return String(text || '').replace(/\s+/g, ' ').trim();
}

export function planReportOwnsBody(report: string | undefined, body: string): boolean {
  const cleanReport = String(report || '').trim();
  const cleanBody = String(body || '').trim();
  if (!cleanReport || !cleanBody) return false;
  if (looksLikePlanReport(cleanBody)) return true;
  if (cleanReport.includes(cleanBody)) return true;
  const reportCompact = compactPlanText(cleanReport);
  const bodyCompact = compactPlanText(cleanBody);
  if (reportCompact.includes(bodyCompact)) return true;
  const head = reportCompact.slice(0, Math.min(80, reportCompact.length));
  return Boolean(head) && bodyCompact.includes(head);
}

function clearDuplicatePlanBody(target: ExecutionMessage) {
  const body = String(target.content || '').trim();
  if (body && planReportOwnsBody(target.planReport, body)) target.content = '';
}

export function planExecutionStarted(target: Pick<ExecutionMessage, 'taskPlan'>): boolean {
  return (target.taskPlan || []).some(
    (step) => step.status === 'running' || step.status === 'completed',
  );
}

/** 计划尚未交给用户看：正文不得流式进气泡，卡片等整份报告齐了再整体出现。
 *  规划阶段 `update_plan` 把步骤标成 running，不等于用户已同意执行。 */
export function shouldHoldPlanStreamOffBody(target: ExecutionMessage): boolean {
  if (target.planExecutionUnlocked) return false;
  return String(target.agentMode || '') === 'plan';
}

export function ingestPlanReportText(target: ExecutionMessage, incoming: string): boolean {
  const text = String(incoming || '').trim();
  if (!text) return false;
  if (target.planExecutionUnlocked) return false;
  const alreadyHasCard = looksLikePlanReport(target.planReport)
    || String(target.planReport || '').trim().length >= 80;
  if (String(target.agentMode || '') !== 'plan' && !alreadyHasCard) return false;
  if (isPlanProcessDump(text)) return false;
  if (!looksLikePlanReport(text) && !alreadyHasCard) return false;
  target.planReport = mergePlanReport(target.planReport, text);
  clearDuplicatePlanBody(target);
  return true;
}

type PlanCardStep = { title?: string; detail?: string };
type PlanCardContract = { goal?: string; deliverable?: string };

function isPlanDocumentTurn(message: {
  agentMode?: string;
  planReport?: string;
  interactive?: {
    kind?: string;
    revision_gate?: unknown;
    plan_steps?: PlanCardStep[];
  } | null;
}): boolean {
  if (String(message.agentMode || '') === 'plan') return true;
  const durableReport = String(message.planReport || '').trim();
  if (
    durableReport
    && (looksLikePlanReport(durableReport)
      || (durableReport.length >= 40 && !isPlanProcessDump(durableReport)))
  ) return true;
  const interactive = message.interactive;
  if (!interactive) return false;
  return interactive.kind === 'plan_confirmation'
    || Boolean(interactive.revision_gate)
    || Boolean(interactive.plan_steps?.length);
}

/** 计划白卡正文：优先用模型写出的报告；确认卡若报告没对上启发式，用步骤标题/说明合成。
 *  标准模式的种子 taskPlan（「完成「…」→ 整理结果并回复」）不得合成白卡。 */
export function buildPlanCardMarkdown(message: {
  agentMode?: string;
  planReport?: string;
  content?: string;
  planExecutionUnlocked?: boolean;
  taskPlan?: PlanCardStep[];
  taskGoalContract?: PlanCardContract;
  interactive?: {
    kind?: string;
    revision_gate?: unknown;
    plan_steps?: PlanCardStep[];
    goal_contract?: PlanCardContract;
  } | null;
}): string {
  if (!isPlanDocumentTurn(message)) return '';
  const report = String(message.planReport || '').trim();
  if (report && (looksLikePlanReport(report) || (report.length >= 80 && !isPlanProcessDump(report)))) {
    return report;
  }
  const body = String(message.content || '').trim();
  if (looksLikePlanReport(body)) return body;
  if (message.planExecutionUnlocked) return report;
  const confirm = Boolean(
    message.interactive?.kind === 'plan_confirmation'
    || message.interactive?.revision_gate
    || message.interactive?.plan_steps?.length,
  );
  // 仅确认卡才用步骤合成；规划中的 update_plan / 标准模式种子计划都不是给用户看的文稿。
  if (!confirm) return '';
  const steps = (message.interactive?.plan_steps?.length
    ? message.interactive.plan_steps
    : message.taskPlan) || [];
  const titled = steps.filter((step) => String(step?.title || '').trim());
  if (!titled.length) return report;
  const contract = message.interactive?.goal_contract || message.taskGoalContract;
  const title = String(contract?.goal || contract?.deliverable || '').trim() || '执行计划';
  const lines = [`## ${title}`, ''];
  titled.forEach((step, index) => {
    lines.push(`### ${index + 1}. ${String(step.title || '').trim()}`);
    const detail = String(step.detail || '').trim();
    if (detail) {
      lines.push('');
      lines.push(detail);
    }
    lines.push('');
  });
  return lines.join('\n').trim();
}

export function applyCommentary(target: ExecutionMessage, text: string, kind = '') {
  const clean = (text || '').trim();
  if (!clean) return;
  parkTransientReasoning(target);
  clearTransientReasoning(target);
  // 计划报告（kind='plan'）：渲染成一张独立卡片，不进开场白气泡、更不进时间线 note
  // （2026-07-28 用户拍板「计划收进一张卡片」）。后端在计划轮显式打标，前端不猜——
  // 靠「有没有工具动过手」猜的话，模型先读了文件再写计划就会把整份计划塞进一个 note 行。
  if (kind === 'plan' && !target.planExecutionUnlocked) {
    const trustKind = looksLikePlanReport(clean)
      || looksLikePlanReport(target.planReport)
      || (clean.length >= 80 && !isPlanProcessDump(clean));
    if (trustKind) {
      target.planReport = mergePlanReport(target.planReport, clean);
      clearDuplicatePlanBody(target);
      return;
    }
  }
  // 模型常先勘查再写计划：完整报告可能没打 kind='plan'，而确认卡那句短 intro 打了。
  // 长文看起来就是计划报告时，并进同一张卡，避免「卡片只剩半句、正文散在时间线」。
  if (ingestPlanReportText(target, clean)) return;
  // 兼容切换期间已经入库的旧事件：公开阶段判断仍可保留，但只作为普通 commentary，
  // 不再伪装成会常驻的“思考尾窗”。
  if (kind === 'reasoning_summary') kind = '';
  if (kind === 'initial_progress') {
    if (!target.preamble) {
      target.preamble = clean;
      target.preambleIsInitialProgress = true;
      target.narrativeKind = 'system_note';
    }
    if (!target.content) target.executionCollapsed = false;
    return;
  }
  target.executionCollapsed = false;
  const acted =
    (target.agentSteps || []).some((s) => s.kind === 'tool' || s.kind === 'subagent') ||
    (target.agentSteps || []).some(
      (s) => s.kind === 'note' && /^已加载能力[：:]/u.test(String(s.text || '').trim()),
    ) ||
    Boolean(target.toolSteps?.length) ||
    Boolean(target.subagentCalls?.length);
  // ：覆盖前端乐观开场/系统占位；相同文案直接去重，避免「我先按…」双行
  const prevPreamble = String(target.preamble || '').trim();
  if (prevPreamble && prevPreamble === clean) return;
  const optimisticOrPlaceholder =
    target.preambleIsInitialProgress ||
    !target.preamble ||
    /^(接下来会|我先按|我先去|我接着|我先查)/.test(prevPreamble);
  if (!acted && optimisticOrPlaceholder) {
    target.preamble = clean;
    target.preambleIsInitialProgress = false;
    target.narrativeKind = 'commentary';
  } else {
    // P2.5：技术括号/沙箱 notice 只给模型，不进用户时间线
    if (isTechnicalNotice(clean)) return;
    // 已有相同 note 不重复塞
    if ((target.agentSteps || []).some((s) => s.kind === 'note' && String(s.text || '').trim() === clean)) {
      return;
    }
    if (!target.agentSteps) target.agentSteps = [];
    target.agentSteps.push({ kind: 'note', text: clean, planKey: currentPlanKey(target) });
    if (!target.narrativeKind || target.narrativeKind === 'ack') {
      target.narrativeKind = 'commentary';
    }
  }
  if (!target.content) {
    target.executionCollapsed = false;
    target.executionAutoCollapsed = false;
  }
}

/** Keep one visible row per automatic provider-recovery cycle.

The event is structured and platform-owned: it must not become the assistant preamble, final
answer, or persisted model commentary. A recovered event closes only the latest active notice,
so later model rounds retain their true place in the execution timeline.
*/
export function applyModelConnection(
  target: ExecutionMessage,
  payload: ModelConnectionPayload,
) {
  if (!target.agentSteps) target.agentSteps = [];
  const active = [...target.agentSteps].reverse().find(
    (step): step is Extract<AgentStep, { kind: 'note' }> => (
      step.kind === 'note' && step.connectionStatus === 'recovering'
    ),
  );
  if (payload.status === 'recovering') {
    const attempt = Math.max(0, Number(payload.attempt || 0));
    const maxRetries = Math.max(0, Number(payload.maxRetries || 0));
    const text = attempt > 0 && maxRetries > 0
      ? `模型连接暂时中断，正在自动重连（${attempt}/${maxRetries}）…`
      : '模型连接暂时中断，正在自动恢复…';
    if (active) {
      active.text = text;
    } else {
      target.agentSteps.push({
        kind: 'note',
        text,
        connectionStatus: 'recovering',
        planKey: currentPlanKey(target),
      });
    }
  } else if (payload.status === 'recovered') {
    if (!active) return;
    active.text = '模型连接已恢复，继续执行。';
    active.connectionStatus = 'recovered';
  } else {
    if (!active) return;
    const maxRetries = Math.max(0, Number(payload.maxRetries || 0));
    active.text = maxRetries > 0
      ? `模型连接连续重试 ${maxRetries} 次仍未恢复，已停止本轮。`
      : '模型连接未能恢复，已停止本轮。';
    active.connectionStatus = 'failed';
  }
  target.executionCollapsed = false;
  target.executionAutoCollapsed = false;
}

/** 附件读取降级（attachments.status）：整表快照直接替换（后端每轮至多下发一次）。 */
export function applyAttachmentsStatus(target: ExecutionMessage, items: AttachmentIssue[]) {
  if (!items?.length) return;
  target.attachmentIssues = items;
}

/** 从工具回执中提取简短的 Skill 不可用事实，实时与回放共用。 */
export function extractSkillUnavailableWarning(preview: string): string {
  const text = String(preview || '');
  if (!text) return '';
  const keyword = /没有挂进沙箱|取包失败|暂时不可用|超出单次挂载上限|二进制资源没有挂进沙箱/.exec(text);
  if (!keyword) return '';
  const at = keyword.index;
  const start = Math.max(0, text.lastIndexOf('\n', at - 1) + 1);
  const end = text.indexOf('\n', at);
  const line = text.slice(start, end >= 0 ? end : undefined).trim();
  const flat = line.replace(/\*\*/g, '').replace(/\s+/g, ' ').trim();
  return flat.length > 60 ? `${flat.slice(0, 60)}…` : flat;
}

const HITL_TOOL_NAMES = new Set(['ask_user_choice']);

/** 用户已继续或后续工具已开始时，收口仍在 running 的提问工具行。 */
export function settleHitlToolSteps(target: ExecutionMessage) {
  for (const step of target.agentSteps || []) {
    if (step.kind !== 'tool' || step.status !== 'running' || !HITL_TOOL_NAMES.has(step.name)) continue;
    step.status = 'completed';
    step.label = toolLabels(step.name).completed;
    if (step.startedAt && !step.durationMs) step.durationMs = Math.max(0, Date.now() - step.startedAt);
  }
  for (const chip of target.toolSteps || []) {
    if (chip.status === 'running' && HITL_TOOL_NAMES.has(chip.name)) chip.status = 'completed';
  }
}

/** 将已提交的工具事件投影为用户可见的事实时间线。 */
export function applyTimelineTool(target: ExecutionMessage, ev: ToolStepEvent) {
  if (ev.name === 'call_subagent') return;
  if (!target.agentSteps) target.agentSteps = [];
  if (ev.name && !HITL_TOOL_NAMES.has(ev.name)) settleHitlToolSteps(target);
  const labels = toolLabels(ev.name);
  if (ev.phase === 'started') {
    updatePlanItem(target, ev.name, ['pending'], 'running');
    const started = toolStepDisplay({
      name: ev.name,
      status: 'running',
      intent: ev.intent,
      detail: ev.detail,
    });
    target.agentSteps.push({
      kind: 'tool', name: ev.name, callId: ev.callId, status: 'running',
      label: started.label || labels.running,
      intent: ev.intent, detail: started.detail, command: ev.command,
      startedAt: ev.timestamp || Date.now(), elapsedMs: 0, planKey: currentPlanKey(target),
    });
    return;
  }
  const step = [...target.agentSteps]
    .reverse()
    .find((item) => item.kind === 'tool'
      && item.status === 'running'
      && (ev.callId ? item.callId === ev.callId : item.name === ev.name));
  if (ev.phase === 'progress') {
    if (step?.kind === 'tool') {
      step.stage = ev.stage;
      step.label = ev.label || step.label;
      if (ev.elapsedMs != null) step.elapsedMs = Math.max(step.elapsedMs || 0, ev.elapsedMs);
    }
    return;
  }
  const status = ev.phase === 'failed' ? 'failed' : 'completed';
  updatePlanItem(target, ev.name, ['running', 'pending'], status);
  if (step?.kind === 'tool') {
    step.status = status;
    step.durationMs = step.startedAt && ev.timestamp
      ? Math.max(0, ev.timestamp - step.startedAt)
      : step.elapsedMs;
    step.preview = status === 'completed' && ev.preview ? String(ev.preview).slice(0, 2000) : undefined;
    step.error = status === 'failed' && ev.error ? String(ev.error).slice(0, 2000) : undefined;
    if (status === 'completed' && (ev.name === 'bash' || ev.name === 'use_skill') && ev.preview) {
      step.warning = extractSkillUnavailableWarning(String(ev.preview)) || undefined;
    }
    if (ev.meta?.action) {
      const action = ev.meta.action;
      step.operation = String(action.operation || '');
      step.fileId = action.file_id ? String(action.file_id) : undefined;
      step.added = Math.max(0, Number(action.added) || 0);
      step.removed = Math.max(0, Number(action.removed) || 0);
    }
    const display = toolStepDisplay({
      name: ev.name,
      status,
      operation: step.operation,
      intent: step.intent,
      target: ev.meta?.action?.target ? String(ev.meta.action.target) : undefined,
      detail: step.detail,
      count: typeof ev.meta?.count === 'number' ? ev.meta.count : undefined,
    });
    step.label = display.label;
    step.target = display.target;
    step.detail = display.detail;
    if (status === 'completed' && (ev.name === 'download_url' || ev.name === 'browser_fetch') && ev.meta?.urls?.length) {
      step.urls = ev.meta.urls;
    }
    if (
      status === 'completed'
      && (ev.name === 'browser_fetch' || ev.name === 'browser_open' || ev.name === 'browser_act')
      && typeof ev.meta?.shot === 'string'
      && ev.meta.shot.startsWith('data:')
    ) {
      step.shot = ev.meta.shot;
    }
    if (status === 'completed' && ev.name === 'search_web' && ev.meta) {
      if (ev.meta.urls?.length) step.urls = ev.meta.urls;
      if (typeof ev.meta.count === 'number') step.count = ev.meta.count;
      if (Array.isArray(ev.meta.read) && ev.meta.read.length) {
        step.pages = ev.meta.read
          .filter((page: any) => page && (page.url || page.title))
          .map((page: any) => ({
            title: String(page.title || page.url || '网页'),
            url: String(page.url || ''),
          }));
      }
      if (ev.meta.images?.length) {
        const known = new Set((target.citations || []).map((citation) => citation.url));
        const fresh: CitationSource[] = [];
        for (const item of ev.meta.images) {
          if (!item.url || known.has(item.url)) continue;
          known.add(item.url);
          fresh.push({
            type: 'image', title: item.title || '图片', url: item.url,
            source: item.source || '', thumbnail: item.thumbnail || '',
          });
        }
        if (fresh.length) target.citations = [...(target.citations || []), ...fresh];
      }
    }
  }
  if (step?.kind === 'tool' && ev.meta?.review_status) {
    const reviewStatus = String(ev.meta.review_status);
    const reviewBlocked = reviewStatus === 'failed';
    if (reviewBlocked) step.label = '可用性检查发现问题';
    target.agentSteps.push({
      kind: 'verification',
      status: reviewBlocked ? 'failed' : 'completed',
      label: reviewBlocked ? '可用性检查发现问题' : '已完成可用性检查',
      reviewStatus,
      planKey: currentPlanKey(target),
    });
  }
  if (ev.meta?.files?.length) {
    const byId = new Map((target.generatedFiles || []).map((file) => [file.id, { ...file }]));
    const brandNew: typeof ev.meta.files = [];
    for (const file of ev.meta.files) {
      if (!file?.id) continue;
      const previous = byId.get(file.id);
      if (previous) {
        byId.set(file.id, {
          ...previous,
          ...file,
          size: typeof file.size === 'number' ? file.size : previous.size,
          filename: file.filename || previous.filename,
          mime: file.mime || previous.mime,
          review: file.review || previous.review,
        });
      } else {
        byId.set(file.id, { ...file });
        brandNew.push(file);
      }
    }
    target.generatedFiles = Array.from(byId.values());
    for (const item of target.agentSteps || []) {
      if (item.kind === 'artifact' && item.files?.length) {
        item.files = item.files.map((file) => byId.get(file.id) || file);
      }
    }
    const delivered = brandNew.filter((file) => isDeliverableFile(file) && !isCompanionFile(file.filename));
    if (delivered.length && ARTIFACT_PRODUCERS.has(ev.name)) {
      const reviewFailed = delivered.some((file) => file.review?.status === 'failed');
      const researchReport = ev.name === 'research' || delivered.some((file) => file.source === 'research' || file.origin?.tool === 'research');
      target.agentSteps.push({
        kind: 'artifact',
        label: reviewFailed
          ? `${delivered.length} 个产物可用性检查未通过，已保存为草稿`
          : researchReport
            ? '研究报告已生成'
            : `已生成并保存 ${delivered.length} 个文件`,
        status: reviewFailed ? 'failed' : 'completed',
        files: delivered.map((file) => byId.get(file.id) || file),
        planKey: currentPlanKey(target),
      });
    }
  }
}

/** 保留 capability.loaded 数据以兼容旧历史；skill: 不再投影为可见执行行。 */
export function applyCapabilityLoaded(target: ExecutionMessage, names: string[]) {
  const fresh = names.map((name) => String(name || '').trim()).filter(Boolean);
  if (!fresh.length) return;
  const known = new Set(target.loadedCapabilities || []);
  const added = fresh.filter((name) => !known.has(name));
  if (!added.length) return;
  target.loadedCapabilities = [...known, ...added];
  const labels = added.filter((name) => !name.startsWith('skill:'));
  if (!labels.length) return;
  if (!target.agentSteps) target.agentSteps = [];
  const text = `已加载能力：${labels.join('、')}`;
  if (!target.agentSteps.some((step) => step.kind === 'note' && step.text === text)) {
    target.agentSteps.push({ kind: 'note', text, planKey: currentPlanKey(target) });
  }
}

/** 正典 artifact.saved 投影；tool.completed 已带同一 file_id 时会自然去重。 */
export function applyArtifactSaved(
  target: ExecutionMessage,
  payload: { source?: string; files: GeneratedFile[] },
) {
  if (!payload.files.length) return;
  const research = payload.source === 'research';
  const files = research
    ? payload.files.map((file) => ({
        ...file,
        source: file.source || 'research',
        origin: file.origin?.tool ? file.origin : { ...(file.origin || {}), tool: 'research' },
      }))
    : payload.files;
  applyTimelineTool(target, {
    phase: 'completed',
    name: research ? 'research' : (payload.source || 'artifact'),
    meta: { files },
  });
}

export function applyResearchProgress(target: ExecutionMessage, payload: ResearchProgressPayload) {
  target.researchProgress = {
    stage: payload.stage || target.researchProgress?.stage,
    topic: payload.topic ?? target.researchProgress?.topic ?? '',
    topicIndex: payload.topicIndex ?? target.researchProgress?.topicIndex ?? 0,
    topicTotal: payload.topicTotal ?? target.researchProgress?.topicTotal ?? 0,
    sourcesFound: Math.max(
      Number(payload.sourcesFound || payload.citationCount) || 0,
      Number(target.researchProgress?.sourcesFound) || 0,
    ),
    searchCalls: Math.max(
      Number(payload.searchCalls) || 0,
      Number(target.researchProgress?.searchCalls) || 0,
    ),
    citationCount: Math.max(
      Number(payload.citationCount || payload.sourcesFound) || 0,
      Number(target.researchProgress?.citationCount) || 0,
    ),
    label: payload.label || target.researchProgress?.label,
    team: mergeResearchTeam(target.researchProgress?.team, payload.team),
  };
}

export interface ArtifactPageView {
  index: number;
  total: number;
  title: string;
  svg?: string;
  html?: string;
}

const ARTIFACT_PAGES_MAX = 60;

function applyArtifactPage(
  target: ExecutionMessage,
  data: { index?: number; total?: number; title?: string; svg?: string; html?: string },
) {
  const index = Number(data.index) || 0;
  const svg = String(data.svg || '');
  const html = String(data.html || '');
  if (index <= 0 || (!svg && !html)) return;
  const pages = target.artifactPages || (target.artifactPages = []);
  const page: ArtifactPageView = {
    index,
    total: Math.max(Number(data.total) || 0, index),
    title: String(data.title || `第 ${index} 页`),
    ...(html ? { html } : { svg }),
  };
  const at = pages.findIndex((item) => item.index === index);
  if (at >= 0) pages.splice(at, 1, page);
  else if (pages.length < ARTIFACT_PAGES_MAX) pages.push(page);
  pages.sort((left, right) => left.index - right.index);
}

/** tool.* 的唯一前端入口：时间线与简要状态同时由事件驱动。 */
export function applyToolEvent(target: ExecutionMessage, ev: ToolStepEvent) {
  clearInitialProgressPreamble(target);
  if (ev.phase === 'started') {
    // 保留刚结束的两三句弱提示；下一轮 reasoning 首 token 到达时原位替换。
    parkTransientReasoning(target);
    target.executionCollapsed = false;
  }
  applyTimelineTool(target, ev);
  if (!target.toolSteps) target.toolSteps = [];
  if (ev.phase === 'started') {
    target.toolSteps.push({ name: ev.name, callId: ev.callId, status: 'running' });
    return;
  }
  if (ev.phase === 'progress') {
    if (ev.stage === 'artifact_page' && (ev.progressDetail?.svg || ev.progressDetail?.html)) {
      applyArtifactPage(target, ev.progressDetail);
    }
    return;
  }
  const status = ev.phase === 'failed' ? 'failed' : 'completed';
  const step = [...target.toolSteps].reverse().find(
    (item) => item.status === 'running'
      && (ev.callId ? item.callId === ev.callId : item.name === ev.name),
  );
  if (step) step.status = status;
  else target.toolSteps.push({ name: ev.name, status });
}

export function markRunStarted(target: ExecutionMessage, timestamp?: number) {
  // 只记一次：断线重连/HITL 恢复重放 run.started 不得重置总计时
  if (!target.runStartedAt) target.runStartedAt = timestamp || Date.now();
  ensureRunningThought(target);
}

/** 终态事件缺失兜底：run 已收尾但个别步骤没等到自己的 tool.completed/failed，就地关闭 */
function closeRunningSteps(target: ExecutionMessage, status: StepStatus, suffix = '') {
  const kept: AgentStep[] = [];
  for (const step of target.agentSteps || []) {
    if (step.kind === 'compaction' && step.status === 'running') {
      step.status = status === 'failed' ? 'failed' : 'completed';
      if (!step.seconds && step.startedAt) {
        step.seconds = Math.max(1, Math.round((Date.now() - step.startedAt) / 1000));
      }
      kept.push(step);
      continue;
    }
    if (step.kind === 'thinking' && step.status === 'running') {
      const body = compactReasoningSummary(step.text);
      if (!body) continue;
      step.status = 'completed';
      step.text = body;
      stampThoughtSeconds(step);
      kept.push(step);
      continue;
    }
    if ((step.kind === 'tool' || step.kind === 'subagent') && step.status === 'running') {
      step.status = status;
      if (suffix) step.label = `${step.label}${suffix}`;
    }
    kept.push(step);
  }
  if (target.agentSteps) target.agentSteps = kept;
  for (const chip of target.toolSteps || []) {
    if (chip.status === 'running') chip.status = status;
  }
  for (const chip of target.subagentCalls || []) {
    if (chip.status === 'running') chip.status = status;
  }
  for (const item of target.executionPlan?.items || []) {
    if (item.status === 'running') item.status = status;
  }
}

/** 终态时关闭同一 Run 的临时执行行；权威 Plan 快照不在前端改写。 */
export function settleRunExecutionSteps(
  messages: Array<{ role?: string; runId?: string } & ExecutionMessage>,
  runId: string | undefined,
  status: StepStatus = 'completed',
) {
  if (!runId) return;
  for (const message of messages) {
    if (message.role !== 'assistant' || message.runId !== runId) continue;
    closeRunningSteps(message, status);
  }
}

export function markRunCompleted(target: ExecutionMessage, timestamp?: number) {
  clearInitialProgressPreamble(target);
  parkTransientReasoning(target);
  clearTransientReasoning(target);
  target.executionCollapsed = false;
  const completedAt = timestamp || Date.now();
  target.runCompletedAt = completedAt;
  if (target.runStartedAt) target.runDurationMs = Math.max(0, completedAt - target.runStartedAt);
  // 终态以 run.completed 为准：期间即使有步骤重试失败过，最终收到完成事件就不算整轮失败
  target.runFailed = false;
  target.runPartial = false;
  // 「已停止」残留清除（第三批 P1-3）：停止请求失败/pending 后 Run 若自然跑完，
  // 真实终态以本帧为准——不能让卡片继续显示「已停止」
  target.runCancelled = false;
  closeRunningSteps(target, 'completed');
}

export function markRunFailed(target: ExecutionMessage, timestamp?: number) {
  clearInitialProgressPreamble(target);
  parkTransientReasoning(target);
  clearTransientReasoning(target);
  target.executionCollapsed = false;
  const completedAt = timestamp || Date.now();
  target.runCompletedAt = completedAt;
  if (target.runStartedAt) target.runDurationMs = Math.max(0, completedAt - target.runStartedAt);
  target.runFailed = true;
  target.runPartial = false;
  target.runCancelled = false; // 同上：真失败终态覆盖过期的「已停止」预标记
  closeRunningSteps(target, 'failed');
}

/** Completion Verifier 的部分完成终态：冻结执行行，保留权威 Plan 中的未完步骤。 */
export function markRunPartial(target: ExecutionMessage, timestamp?: number) {
  clearInitialProgressPreamble(target);
  parkTransientReasoning(target);
  clearTransientReasoning(target);
  target.executionCollapsed = false;
  const completedAt = timestamp || Date.now();
  target.runCompletedAt = completedAt;
  if (target.runStartedAt) target.runDurationMs = Math.max(0, completedAt - target.runStartedAt);
  target.runPartial = true;
  target.runFailed = false;
  target.runCancelled = false;
  closeRunningSteps(target, 'failed');
}

/** 用户主动停止：冻结计时并把未完步骤标注「已停止」 */
export function markRunCancelled(target: ExecutionMessage, timestamp?: number) {
  clearInitialProgressPreamble(target);
  parkTransientReasoning(target);
  clearTransientReasoning(target);
  target.executionCollapsed = false;
  target.runCancelled = true;
  target.runPartial = false;
  target.runFailed = false;
  const completedAt = timestamp || Date.now();
  if (!target.runCompletedAt) {
    target.runCompletedAt = completedAt;
    if (target.runStartedAt) target.runDurationMs = Math.max(0, completedAt - target.runStartedAt);
  }
  closeRunningSteps(target, 'failed', '（已停止）');
}

/** 答案首个 delta 到达后，保持本轮真实执行过程展开。 */
export function revealAssistantOutput(target: ExecutionMessage) {
  clearTransientReasoning(target);
  if (target.executionAutoCollapsed) return;
  if (!target.agentSteps?.length && !target.runStartedAt) return;
  target.executionAutoCollapsed = true;
  target.executionCollapsed = false;
}

/**
 * 把历史轨迹里独立的 kind=read 行并回上一条 search_web。
 * 旧轨迹仍会吐出「搜索 + 浏览」两行；新口径把已打开页面挂在搜索步骤上。
 */
export function foldSearchReadSteps(steps: AgentStep[]): AgentStep[] {
  const out: AgentStep[] = [];
  for (const step of steps) {
    if (step.kind === 'read') {
      const prev = out[out.length - 1];
      if (prev && prev.kind === 'tool' && prev.name === 'search_web') {
        out[out.length - 1] = {
          ...prev,
          pages: step.pages?.length ? step.pages : prev.pages,
        };
        continue;
      }
    }
    out.push(step);
  }
  return out;
}

const ACTIVE_TRACE_STATUSES = new Set([
  'created',
  'routing',
  'running',
  'waiting_user',
  'waiting_confirmation',
  'waiting_system',
]);

function restoreStep(raw: any, preserveRunning: boolean): AgentStep | null {
  if (!raw || typeof raw !== 'object') return null;
  const planKey = raw.planKey ? String(raw.planKey) : undefined;
  if (raw.kind === 'compaction') {
    return {
      kind: 'compaction',
      status:
        raw.status === 'running' && preserveRunning
          ? 'running'
          : raw.status === 'failed'
            ? 'failed'
            : 'completed',
      seconds: typeof raw.seconds === 'number' && raw.seconds > 0 ? raw.seconds : undefined,
      planKey,
    };
  }
  if (raw.kind === 'thinking') {
    const text = String(raw.text || '').trim();
    if (!text) return null;
    return {
      kind: 'thinking',
      text,
      status: raw.status === 'running' && preserveRunning ? 'running' : 'completed',
      seconds: typeof raw.seconds === 'number' && raw.seconds > 0 ? raw.seconds : undefined,
      startedAt:
        raw.status === 'running' && preserveRunning && raw.startedAt
          ? Number(raw.startedAt)
          : undefined,
      planKey,
    };
  }
  if (raw.kind === 'note') {
    const text = String(raw.text || '');
    return text ? { kind: 'note', text, planKey } : null;
  }
  if (raw.kind === 'summary') {
    const text = String(raw.text || '');
    return text ? { kind: 'note', text, planKey } : null;
  }
  if (raw.kind === 'read') {
    const pages = Array.isArray(raw.pages)
      ? raw.pages.filter((p: any) => p && p.url).map((p: any) => ({ title: String(p.title || p.url), url: String(p.url) }))
      : [];
    return pages.length ? { kind: 'read', pages, planKey } : null;
  }
  if (raw.kind === 'subagent') {
    const name = String(raw.name || '子智能体');
    // running 残留＝历史孤儿：Run 在收到 subagent.completed/failed 前异常终止，历史回放没有
    // 实时流再推它离开——归一成失败态视觉（复用既有失败态样式），但用「中断」措辞和 preview
    // 提示区分于真失败，既不永远转圈也不假装完成
    const interrupted = raw.status === 'running' && !preserveRunning;
    const status: StepStatus =
      raw.status === 'running' && preserveRunning
        ? 'running'
        : interrupted || raw.status === 'failed'
          ? 'failed'
          : 'completed';
    return {
      kind: 'subagent', name, status,
      label: raw.label
        ? String(raw.label)
        : interrupted
          ? `「${name}」的委派记录已中断（未完成）`
          : status === 'running'
            ? `「${name}」正在处理`
            : status === 'failed'
              ? `「${name}」的委派任务失败`
              : `「${name}」已返回处理结果`,
      task: raw.task ? String(raw.task) : undefined,
      preview: raw.preview
        ? String(raw.preview)
        : interrupted
          ? '这次委派没有等到结果就中断了（进程异常终止或连接中断，并非任务失败）'
          : undefined,
      runKey: raw.runKey ? String(raw.runKey) : undefined,
      planKey,
    };
  }
  if (raw.kind === 'tool') {
    const name = String(raw.name || '');
    // running 残留同上一分支：不能悄悄归一成 completed（假成功），也复用失败态视觉收尾
    const interrupted = raw.status === 'running' && !preserveRunning;
    const status: StepStatus =
      raw.status === 'running' && preserveRunning
        ? 'running'
        : interrupted || raw.status === 'failed'
          ? 'failed'
          : 'completed';
    const operation = raw.operation ? String(raw.operation) : '';
    // 显示归一与实时链路共用同一个函数：轨迹里存的是**清理前**的 target（bash 是原始 shell
    // 命令、glob 是匹配式），只在实时侧降噪就等于「刷新一次就打回原形」
    const display = toolStepDisplay({
      name,
      status,
      operation,
      intent: raw.intent ? String(raw.intent) : undefined,
      target: raw.target ? String(raw.target) : undefined,
      detail: raw.detail ? String(raw.detail) : undefined,
      count: typeof raw.count === 'number' ? raw.count : undefined,
      summary: raw.summary ? String(raw.summary) : undefined,
    });
    // 失败文案的落库口径统一（2026-07-28）：后端此前把 tool.failed 的错误存进 `preview`，
    // 实时侧存进 `error`——同一次失败，刷新前行内有红字摘要、刷新后只剩「输出」段里的一坨。
    // 后端已改成写 `error`；这里读回 `preview` 只是**旧轨迹**的兜底，不是双写。
    const legacyFailureText = status === 'failed' && !raw.error && raw.preview
      ? String(raw.preview)
      : '';
    return {
      kind: 'tool', name, status,
      callId: raw.callId ? String(raw.callId) : undefined,
      label: interrupted
        ? '执行记录已中断（未完成）'
        : operation === 'subagent_prepare' || operation === 'subagent_node'
          ? String(raw.label || display.label)
          : display.label,
      // 模型现写的调用意图：回放同实时，行标题优先用它（restoreExecutionTrace 已还原）
      intent: raw.intent ? String(raw.intent).slice(0, 80) : undefined,
      urls: Array.isArray(raw.urls) && raw.urls.length ? raw.urls.map(String) : undefined,
      count: typeof raw.count === 'number' ? raw.count : undefined,
      pages: Array.isArray(raw.pages) && raw.pages.length
        ? raw.pages
            .filter((p: any) => p && (p.url || p.title))
            .map((p: any) => ({
              title: String(p.title || p.url || '网页'),
              url: String(p.url || ''),
            }))
        : undefined,
      // 页面快照要跟着历史一起回来（2026-07-27）：只在实时链路显示的话，刷新一次图就没了，
      // 用户会以为「上次明明有图」是错觉。只认 data: 开头，不让任意外链混进 img src；
      // 失败步骤一律不还原图（2026-07-28「不可以展示无法打开的网页」，与实时侧同一口径——
      // 早先落过库的失败截图会随这条闸静默退场，不必回删轨迹）。
      shot: status === 'completed' && typeof raw.shot === 'string' && raw.shot.startsWith('data:')
        ? raw.shot
        : undefined,
      durationMs: raw.durationMs > 0 ? Number(raw.durationMs) : undefined,
      operation: operation || undefined,
      runKey: raw.runKey ? String(raw.runKey) : undefined,
      target: display.target,
      // 统一经过 toolStepDisplay：文件区扫描不展示内部文件数，其他工具 detail 仍会回放。
      detail: display.detail,
      fileId: raw.fileId ? String(raw.fileId) : undefined,
      added: raw.added > 0 ? Number(raw.added) : undefined,
      removed: raw.removed > 0 ? Number(raw.removed) : undefined,
      // Codex 式面板回放：跑了什么（command）+ 跑出了什么（preview）
      command: raw.command ? String(raw.command).slice(0, 4000) : undefined,
      // 旧轨迹里 preview 装的就是失败原文，转投 error 后不再重复占「输出」段
      preview: !legacyFailureText && raw.preview ? String(raw.preview).slice(0, 2000) : undefined,
      // v3.0 技能不可用告警与实时同源：刷新前后必须长得一样（优先落库值，兜底从 preview 提取）
      warning: raw.warning
        ? String(raw.warning).slice(0, 120)
        : extractSkillUnavailableWarning(raw.preview ? String(raw.preview) : '') || undefined,
      error: interrupted
        ? '这一步没有跑完（连接中断，并非任务失败）'
        : status === 'failed'
          ? String(raw.error || legacyFailureText).slice(0, 2000) || undefined
          : undefined,
      planKey,
    };
  }
  if (raw.kind === 'artifact') {
    // 与实时口径一致（2026-07-27）：历史回放同样只把交付物计进产物行，否则刷新一次
    // 时间线就从「1 个文件」变回「3 个文件」，而底下的卡片还是一张。
    const files = Array.isArray(raw.files)
      ? raw.files.filter((file: any) => file?.id && isDeliverableFile(file) && !isCompanionFile(file.filename))
      : [];
    if (!files.length) return null;
    const failed = files.some((file: any) => file.review?.status === 'failed');
    const researchReport = files.some((file: any) => file.source === 'research' || file.origin?.tool === 'research');
    return {
      kind: 'artifact',
      status: failed ? 'failed' : 'completed',
      label: failed
        ? `${files.length} 个产物可用性检查未通过，已保存为草稿`
        : researchReport
          ? '研究报告已生成'
          : `已生成并保存 ${files.length} 个文件`,
      files,
      planKey,
    };
  }
  if (raw.kind === 'verification') {
    const interrupted = raw.status === 'running' && !preserveRunning;
    const status: StepStatus =
      raw.status === 'running' && preserveRunning
        ? 'running'
        : interrupted || raw.status === 'failed'
          ? 'failed'
          : 'completed';
    return {
      kind: 'verification',
      status,
      label: interrupted
        ? '可用性检查记录已中断（未完成）'
        : status === 'running'
          ? String(raw.label || '正在检查生成产物')
          : String(raw.label || (status === 'failed' ? '产物检查未通过' : '已检查生成产物')),
      reviewStatus: raw.reviewStatus ? String(raw.reviewStatus) : undefined,
      planKey,
    };
  }
  return null;
}

function restoreSubagentRun(raw: any, index: number, preserveRunning: boolean): SubagentRun | null {
  if (!raw || typeof raw !== 'object') return null;
  const name = String(raw.name || '子智能体');
  // running 残留：与上面 agentSteps 的 subagent 分支同一归一规则（两处保持一致）——
  // 「工作窗口」卡片同样不能永远显示进行中
  const interrupted = raw.status === 'running' && !preserveRunning;
  const status: StepStatus =
    raw.status === 'running' && preserveRunning
      ? 'running'
      : interrupted || raw.status === 'failed'
        ? 'failed'
        : 'completed';
  const nodes = Array.isArray(raw.nodes)
    ? raw.nodes
        .filter((node: any) => node && node.label)
        .map((node: any) => ({ label: String(node.label), status: String(node.status || '') }))
    : [];
  return {
    id: String(raw.id || ''),
    runKey: String(raw.runKey || `history-subagent-${index}`),
    name,
    ...(raw.icon ? { icon: String(raw.icon) } : {}),
    task: raw.task ? String(raw.task) : undefined,
    status,
    interrupted: interrupted || undefined,
    nodes,
    output: raw.output ? String(raw.output) : '',
    preview: raw.preview ? String(raw.preview) : undefined,
    error: raw.error
      ? String(raw.error)
      : interrupted
        ? '子智能体委派记录已中断（未完成，并非任务失败）'
        : undefined,
    // 执行团队一期：委派扩展与验收随历史回放（服务端轨迹构建同名字段）
    ...(raw.roleName ? { roleName: String(raw.roleName) } : {}),
    ...(raw.managerRole ? { managerRole: String(raw.managerRole) } : {}),
    ...(Array.isArray(raw.subtasks) && raw.subtasks.length
      ? { subtasks: raw.subtasks.map((s: any) => String(s)) }
      : {}),
    ...(Array.isArray(raw.acceptanceCriteria) && raw.acceptanceCriteria.length
      ? { acceptanceCriteria: raw.acceptanceCriteria.map((s: any) => String(s)) }
      : {}),
    ...(raw.review && Array.isArray(raw.review.verdicts)
      ? {
          review: {
            verdicts: raw.review.verdicts.map((v: any) => ({
              criterion: String(v?.criterion || ''),
              passed: typeof v?.passed === 'boolean' ? v.passed : null,
              evidence: v?.evidence ? String(v.evidence) : undefined,
            })),
            passedCount: Number(raw.review.passedCount || 0),
            total: Number(raw.review.total || 0),
          },
        }
      : {}),
    ...(raw.acceptance && typeof raw.acceptance === 'object'
      ? {
          acceptance: {
            passedCount: Number(raw.acceptance.passed_count ?? raw.acceptance.passedCount ?? 0),
            total: Number(raw.acceptance.total || 0),
          },
        }
      : {}),
    ...(Array.isArray(raw.files)
      ? {
          files: raw.files
            .filter((file: any) => file?.id && file?.filename)
            .map((file: any) => ({
              id: String(file.id),
              filename: String(file.filename),
              size: Math.max(0, Number(file.size) || 0),
              mime: file.mime ? String(file.mime) : undefined,
              source: ['uploaded', 'generated', 'material', 'research'].includes(String(file.source))
                ? file.source
                : 'generated',
              review: file.review && typeof file.review === 'object' ? file.review : undefined,
              origin: file.origin && typeof file.origin === 'object' ? file.origin : undefined,
              versionNo: file.versionNo ? Number(file.versionNo) : undefined,
              draft: file.draft === true || undefined,
              deliverable: file.deliverable !== false,
            })),
        }
      : {}),
  };
}

/** 成员卡按**身份**去重（2026-07-28）。
 *
 *  subagentRuns 是「每次委派一条」的运行档，node/delta/验收都靠它归属，必须保持一条一次。
 *  但展示层不能照搬：同一个子智能体在 max_calls 内被调用 3 次（「确认 → 提交」这种多步
 *  业务本来就该这样），执行团队面板就画出 3 张同名成员卡、「任务协作」的角标也显示 3 ——
 *  角标名义上是团队规模，实际读出来是调用次数。
 *
 *  这里按 id（缺 id 退回 name）合并：保留**最新一次**的状态与验收（成员当前是什么样），
 *  callCount 记下被委派过几次，runKey 用最新一次的（面板聚焦/下钻仍指向最近一次运行档）。 */
export function mergeTeamMembers(runs: SubagentRun[] | undefined): SubagentRun[] {
  const order: string[] = [];
  const byIdentity = new Map<string, SubagentRun>();
  for (const run of runs || []) {
    const key = run.id || run.name || run.runKey;
    const prev = byIdentity.get(key);
    if (!prev) {
      order.push(key);
      byIdentity.set(key, { ...run, callCount: 1 });
      continue;
    }
    // 后到的那条即当前状态；roleName/managerRole/subtasks 等派发期字段若后一次没带，
    // 沿用先前记下的（模型只在首次委派时起名的场景很常见）
    byIdentity.set(key, {
      ...prev,
      ...run,
      roleName: run.roleName || prev.roleName,
      managerRole: run.managerRole || prev.managerRole,
      subtasks: run.subtasks?.length ? run.subtasks : prev.subtasks,
      acceptanceCriteria: run.acceptanceCriteria?.length ? run.acceptanceCriteria : prev.acceptanceCriteria,
      callCount: (prev.callCount || 1) + 1,
    });
  }
  return order.map((key) => byIdentity.get(key)!).filter(Boolean);
}

/** 历史消息的执行轨迹还原：结构化步骤 + 状态 + 耗时 + 文件卡；思考回放可展开正文 */
export function restoreExecutionTrace(
  trace: ExecutionTracePayload | null | undefined,
): Partial<ExecutionMessage> {
  if (!trace) return {};
  // 活动 Run 的快照天然会含有 running 步骤：这是「后台仍在执行」，
  // 不是「切页导致连接中断」。只有 Run 已终态时残留的 running 孤儿步骤
  // 才需要收口为 interrupted，避免它永久转圈。
  const preserveRunning = ACTIVE_TRACE_STATUSES.has(String(trace.status || '').toLowerCase());
  const tracePlanText = String(trace.plan_report || '').trim();
  let restoredPlanReport = extractProposedPlanText(tracePlanText) || tracePlanText;
  const steps = (Array.isArray(trace.steps) ? trace.steps : [])
    .map((raw: any) => {
      // v1.104 初期的 Responses 事件已有官方块标签，但没有 kind=plan。
      // 旧投影因此把整份计划存成 note。历史恢复时直接升格为计划卡，
      // 并从执行时间线删掉原始 <proposed_plan> 标签文本。
      if (raw?.kind === 'note' || raw?.kind === 'summary') {
        const legacyPlan = extractProposedPlanText(String(raw?.text || ''));
        if (legacyPlan) {
          restoredPlanReport = mergePlanReport(restoredPlanReport, legacyPlan);
          return null;
        }
      }
      const step = restoreStep(raw, preserveRunning);
      // 交错归属回放：后端按持久化事件顺序为每步标注当时进行中的任务步骤
      if (step && raw?.planKey) step.planKey = String(raw.planKey);
      return step;
    })
    .filter((s): s is AgentStep => s !== null);
  // 旧轨迹：search_web 后另起 kind=read；新口径把已打开页面并回搜索步骤本身。
  const folded = foldSearchReadSteps(steps);
  if (
    !folded.some((step) => step.kind === 'thinking')
    && String(trace.reasoning_summary || '').trim()
  ) {
    folded.unshift({
      kind: 'thinking',
      text: compactReasoningSummary(String(trace.reasoning_summary)),
      status: 'completed',
      seconds: typeof trace.reasoning_seconds === 'number' && trace.reasoning_seconds > 0
        ? trace.reasoning_seconds
        : undefined,
    });
  }
  const loadedCapabilities = (trace.loaded_capabilities || []).map(String)
    .filter((name) => name && !name.startsWith('skill:'));
  if (
    loadedCapabilities.length
    && !folded.some((step) => step.kind === 'tool' && step.name === 'use_skill')
  ) {
    folded.unshift({
      kind: 'note',
      text: `已加载能力：${loadedCapabilities.map((name) => name.replace(/^skill:/, '')).join('、')}`,
    });
  }
  const planItems = (Array.isArray(trace.plan) ? trace.plan : [])
    .filter((raw: any) => raw && raw.name)
    .map((raw: any, index) => ({
      key: String(raw.key || `history-plan-${index}`),
      name: String(raw.name),
      label: planLabel(String(raw.name)),
      status: (raw.status === 'failed' ? 'failed' : raw.status === 'running' ? 'running' : raw.status === 'pending' ? 'pending' : 'completed') as PlanItemStatus,
      detail: raw.detail ? String(raw.detail) : undefined,
    }));
  const taskPlan = (Array.isArray(trace.task_plan) ? trace.task_plan : [])
    .filter((raw: any) => raw && raw.title)
    .map((raw: any, index) => ({
      key: String(raw.key || `plan-${index}`),
      title: String(raw.title),
      status: normalizeTaskPlanStatus(raw.status),
      detail: raw.detail ? String(raw.detail) : undefined,
      acceptance: raw.acceptance
        ? String(raw.acceptance)
        : (Array.isArray(raw.acceptance_criteria) && raw.acceptance_criteria[0]
          ? String(raw.acceptance_criteria[0])
          : undefined),
    }));
  const restoredContract = trace.goal_contract && typeof trace.goal_contract === 'object'
    ? {
        goal: trace.goal_contract.goal ? String(trace.goal_contract.goal) : undefined,
        deliverable: trace.goal_contract.deliverable
          ? String(trace.goal_contract.deliverable)
          : undefined,
        success_criteria: Array.isArray(trace.goal_contract.success_criteria)
          ? trace.goal_contract.success_criteria.map(String)
          : undefined,
        forbidden: Array.isArray(trace.goal_contract.forbidden)
          ? trace.goal_contract.forbidden.map(String)
          : undefined,
        budget_hint: trace.goal_contract.budget_hint
          ? String(trace.goal_contract.budget_hint)
          : undefined,
      }
    : undefined;
  const subagentRuns = (Array.isArray(trace.subagents) ? trace.subagents : [])
    .map((raw, index) => restoreSubagentRun(raw, index, preserveRunning))
    .filter((run): run is SubagentRun => run !== null);
  // generatedFiles 保留全量（.slides.json 配对、后续轮次认亲都要用），只有**展示**口径筛过
  const restoredFiles = trace.files?.length ? trace.files : undefined;
  const restoredDelivered = (restoredFiles || []).filter((file) => isDeliverableFile(file) && !isCompanionFile(file.filename));
  if (restoredDelivered.length && !folded.some((step) => step.kind === 'artifact')) {
    const failed = restoredDelivered.some((file) => file.review?.status === 'failed');
    const researchReport = restoredDelivered.some((file) => file.source === 'research' || file.origin?.tool === 'research');
    folded.push({
      kind: 'artifact',
      status: failed ? 'failed' : 'completed',
      label: failed
        ? `${restoredDelivered.length} 个产物可用性检查未通过，已保存为草稿`
        : researchReport
          ? '研究报告已生成'
          : `已生成并保存 ${restoredDelivered.length} 个文件`,
      files: restoredDelivered,
      // 这个合成步骤不是按原始事件顺序恢复出来的（旧数据没有显式 artifact 步骤），没有真实
      // planKey——打尾部哨兵而不是留空，避免落进 buildExecutionRows 的 pre 桶被排到执行卡
      // 最顶端（时序上显得文件在任务开始前就生成了，和实际发生顺序相反）
      planKey: TAIL_PLAN_KEY,
    });
  }
  return {
    runStartedAt: trace.startedAt || undefined,
    runCompletedAt: trace.completedAt || undefined,
    runDurationMs: trace.durationMs || undefined,
    runCancelled: trace.status === 'cancelled' ? true : undefined,
    runPartial: trace.status === 'partial' ? true : undefined,
    // 整轮终态透传（后端 AgentRun.status，任务模式设计稿 §14 的事实源）：只有 run 真正以失败
    // 收尾（未被后续重试挽回）才是 'failed'；trace.steps 里夹杂的早前失败 attempt 不改变它，
    // 执行卡标题据此判定「执行未完成」而不是遍历 steps 找 failed（历史失败 attempt 会误判）
    runFailed: trace.status === 'failed' ? true : undefined,
    error: trace.status === 'failed' ? String(trace.error || '').trim() || undefined : undefined,
    taskPlan: taskPlan.length ? taskPlan : undefined,
    taskPlanEverStarted: taskPlan.length ? true : undefined,
    taskPlanVersion: trace.plan_version != null ? Number(trace.plan_version) || undefined : undefined,
    taskPlanApprovedVersion: trace.approved_version != null
      ? Number(trace.approved_version)
      : undefined,
    taskPlanDiverged: trace.diverged ? true : undefined,
    taskGoalContract: restoredContract,
    agentSteps: folded.length ? folded : undefined,
    executionPlan: planItems.length ? { items: planItems } : undefined,
    subagentRuns: subagentRuns.length ? subagentRuns : undefined,
    generatedFiles: restoredFiles,
    agentMode: trace.agent_mode ? String(trace.agent_mode) : undefined,
    researchProgress: trace.research_progress
      ? { ...trace.research_progress, team: parseResearchTeam(trace.research_progress.team) } : undefined,
    loadedCapabilities: (trace.loaded_capabilities || []).map(String).filter(Boolean).length
      ? (trace.loaded_capabilities || []).map(String).filter(Boolean)
      : undefined,
    attachmentIssues: trace.attachments_status?.length ? trace.attachments_status : undefined,
    // 系统首帧占位语（正在处理…）不得作为历史开场白复活——否则工具/答案都出来了
    // 执行过程里仍挂着假进度，观感像“还在转”。
    preamble: (() => {
      const raw = String(trace.preamble || '').trim();
      if (!raw) return undefined;
      if (!isSystemInitialProgressPreamble(raw)) return raw;
      const acted = folded.some((step) => step.kind === 'tool' || step.kind === 'subagent');
      if (acted || trace.completedAt || trace.status === 'completed' || trace.status === 'partial' || trace.status === 'failed' || trace.status === 'cancelled') {
        return undefined;
      }
      return raw;
    })(),
    // 计划卡刷新后还在：后端 trace 重建时把 kind='plan' 的 commentary 单独还原成 plan_report
    planReport: restoredPlanReport || undefined,
    // 常驻标记的刷新恢复（2026-07-29 深扫补齐的后半段）：这四样在实时侧一旦出现就**常驻**
    // 在那条助手消息上（上下文压缩提示 chip、路由到的智能体、外部/内部推荐卡），用户不点
    // 也不会消失——刷新后没了就是真缺口。后端已把它们投影进 trace（白名单 + 投影分支
    // 双闸，见 test_trace_event_whitelist.py），这里是**唯一**的消费点：漏了这段，后端
    // 那半就是"落库了但界面永远看不到"，最容易被误判成已完成。
    //
    // recommendedAgents 只存 id 让上层现场匹配（后端投影 recommended_agent_ids 而非整卡）：
    // 存快照的话智能体改名/下架后卡面与真实应用对不上。
    compactedNote: trace.compacted_note || undefined,
    routedAgent: trace.routed_agent?.name || undefined,
    externalRecs: trace.recommendations?.length ? trace.recommendations : undefined,
    recommendedAgentIds: trace.recommended_agent_ids?.length
      ? trace.recommended_agent_ids
      : undefined,
    recommendationIntent: trace.recommendation_meta?.intent || undefined,
    recommendationReasons: trace.recommendation_meta?.reasons || undefined,
    // 历史恢复与实时终态保持同一呈现：markRunCompleted / markRunFailed
    // 都会保持公开执行过程展开。如果这里独自默认折叠，刷新后完整的
    // preamble / tool / summary / note 会变成一行“已运行”，看起来像轨迹丢失。
    executionCollapsed: false,
  };
}

// ===== 执行卡交错渲染（Codex 式）：任务步骤为组头，详细执行步骤嵌在所属步骤下 =====

export type ExecutionRow =
  | { type: 'plan'; item: NonNullable<ExecutionMessage['taskPlan']>[number] }
  /** stepIndex=在 agentSteps 里的原始下标，保证事件投影重排时 key 稳定；
   *  nested=true 表示该步骤归属于某个计划步骤，渲染时缩进 */
  | { type: 'step'; step: AgentStep; stepIndex: number; nested: boolean; groupMember?: boolean };

/** 把「任务计划 + 时间线步骤」编排成交错渲染行：
    ① 计划出现前的步骤（无 planKey）平铺在最上（公开进度等，时序如实）；
    ② 每个计划步骤一行，其间发生的详细步骤（planKey 匹配 key）缩进嵌在它下面；
    ③ planKey 已对不上现计划的步骤（模型改写过标题，或全部收尾后又发生的收尾步骤）
       兜底平铺在最后，不丢步骤。
    无任务计划时退化为原样平铺（时序不变）。 */
/** 折叠族：连续、同族、已完成、无失败的步骤折成一个组头。
 *
 *  `min` 为触发阈值。沙箱探查沿用 2（bash 的行文案本身没有信息量，两行就该收）；
 *  其余族用 3 —— 它们的行带文件名，两行折成「组头 + 展开箭头」反而更难读。
 *
 *  ⚠️ `noTarget` 只对沙箱探查族成立：那里的 target 是**产物计数**，带产物就不能藏。
 *  读取/下载族的 target 是被操作的文件名，不是结果，可以折。 */
const QUIET_FAMILIES: Array<{
  id: string;
  names: string[];
  label: string;
  unit: string;
  icon: string;
  min: number;
  noTarget?: boolean;
  expandable?: boolean;
}> = [
  // 探路性动作全是 bash（ls/cat/find），不折就是一屏「已在沙箱中完成处理」。
  // 组头图标跟着族里的实际动作走（2026-07-27）：沙箱探查=bash 提示符、下载族=下载箭头。
  { id: 'exec', names: ['bash'], label: '沙箱探查', unit: '步', icon: 'bash', min: 2, noTarget: true },
  { id: 'read', names: ['read_file'], label: '读取文件', unit: '个文件', icon: 'read', min: 3 },
  { id: 'find', names: ['glob'], label: '查找文件', unit: '次', icon: 'files', min: 3 },
  { id: 'down', names: ['download_url'], label: '下载文件', unit: '个文件', icon: 'download', min: 3 },
  // 单次搜索直接摊代表结果页；连续多次才归拢，展开后成员仍保留完整单行事实。
  { id: 'search', names: ['search_web'], label: '已完成网络搜索', unit: '次网页搜索', icon: 'web', min: 2 },
  // 搜索词本身保留在主时间线；连续的页面抓取归拢成「查阅网页 N 次」。网页标题、URL、
  // 正文与抓取输出不在这里展开，最终引用仍走正文下方的来源面板。
  {
    id: 'web',
    names: ['browser_fetch', 'browser_open'],
    label: '查阅网页',
    unit: '次',
    icon: 'web',
    min: 3,
  },
];

/** 同类归拢（2026-07-24 用户拍板，2026-07-27 泛化到多个工具族）：连续同族的「已完成、
 *  无失败」步骤折成一行「<族名> · N <单位>」组头，展开时组头后原样跟随成员行。
 *  失败行、运行中的行、沙箱探查里产出产物的行**永不折叠**——折叠只收编探路性动作，
 *  不藏结果与失败（真机那批 download_url 里有两个 404，它们必须留在外面可见）。
 *  跨族/跨计划组/跨嵌套层级不合并；组键=消息id:rg:首步下标（步骤只追加，键稳定）。 */
export function collapseSandboxRuns(
  rows: ExecutionRow[],
  messageId: string,
  expandedKeys: Record<string, boolean>,
): ExecutionRow[] {
  const out: ExecutionRow[] = [];
  let buf: Array<Extract<ExecutionRow, { type: 'step' }>> = [];
  let family: (typeof QUIET_FAMILIES)[number] | null = null;
  const flush = () => {
    if (family && buf.length >= family.min) {
      const first = buf[0];
      const groupKey = `${messageId}:rg:${first.stepIndex}`;
      const expandable = family.expandable !== false;
      const expanded = expandable && Boolean(expandedKeys[groupKey]);
      // 组头缩略图：取成员里前 3 张。只在收起时用得上，但恒定计算——展开/收起是纯展示态，
      // 不该改变数据形状（否则 expandedKeys 一变，行的 key 与内容跟着抖）。
      // ⚠️ 这是**派生值**，不像行内缩略图那样读 step 原对象引用：MessageList 的行缓存
      // （execRowsSignature）不收 shot（那是有意的，见该函数注释——纯展示字段进指纹会让
      // 流式期间的缓存等于没加）。所以组头的图靠的是「shot 恒与 status 同一个事件到达」：
      // running→completed/failed 的跃迁在指纹里，行必然重建，shots 跟着重算。
      // 哪天 tool.progress 也开始带 shot（边浏览边出图），这条就不成立了，届时要么给
      // runGroup 存成员引用改成实时读，要么把 shot 的有无补进指纹。
      // 只取 completed 成员的图：「查阅网页」这一族**失败也会被折进来**（见下方 hit 判定），
      // 而打不开的网页不给用户看图（2026-07-28 用户拍板）。
      const shots = buf
        .map((r) => (r.step.kind === 'tool' && r.step.status === 'completed' ? r.step.shot : undefined))
        .filter((s): s is string => typeof s === 'string' && s.startsWith('data:'))
        .slice(0, 3);
      out.push({
        type: 'step',
        stepIndex: first.stepIndex,
        nested: first.nested,
        step: {
          kind: 'runGroup',
          count: buf.length,
          // 失败成员计数（2026-07-29）：组头据此不再一律宣称「已完成」
          failedCount: buf.filter(
            (r) => r.step.kind === 'tool' && r.step.status === 'failed',
          ).length,
          expanded,
          groupKey,
          label: family.label,
          unit: family.unit,
          icon: family.icon,
          familyId: family.id,
          expandable,
          shots: shots.length ? shots : undefined,
        },
      });
      if (expanded) out.push(...buf.map((row) => ({ ...row, groupMember: true })));
    } else {
      out.push(...buf);
    }
    buf = [];
    family = null;
  };
  for (const row of rows) {
    const candidate = row.type === 'step' && row.step.kind === 'tool'
      ? QUIET_FAMILIES.find(
          (f) => f.names.includes((row.step as { name: string }).name)
            && !(f.noTarget && (row.step as { target?: unknown }).target),
        )
      : undefined;
    // 联网搜索/查阅的失败尝试通常会被下一次搜索或抓取自动兜底；主时间线允许把这些
    // 已结束的尝试归入同一组，展开后仍逐条显示失败事实。其他工具仍维持失败永不折叠。
    const hit = candidate && (
      candidate.id === 'web' || candidate.id === 'search'
        ? row.type === 'step' && row.step.kind === 'tool' && row.step.status !== 'running'
        : row.type === 'step' && row.step.kind === 'tool' && row.step.status === 'completed' && !row.step.error
    ) ? candidate : undefined;
    const sameContext = !buf.length
      || (row.type === 'step' && row.nested === buf[buf.length - 1].nested
        && row.step.planKey === buf[buf.length - 1].step.planKey);
    if (hit) {
      // 上下文（嵌套层级/所属计划组）或工具族变了就先收上一组，本行另起新组的缓冲
      if (!sameContext || (family && family.id !== hit.id)) flush();
      family = hit;
      buf.push(row as Extract<ExecutionRow, { type: 'step' }>);
      continue;
    }
    flush();
    out.push(row);
  }
  flush();
  return out;
}

export function buildExecutionRows(
  plan: ExecutionMessage['taskPlan'],
  steps: AgentStep[],
  /** 渐进披露（运行中传 true）：尚未开始且名下无步骤的 pending 计划行先不显示，
      轮到它（变 running / 有步骤归属）才浮现——步骤实时逐个出现，而非计划一到全量铺开。
      终态回看传 false：被跳过的 pending 步骤如实展示，便于发现没做的事。 */
  hidePending = false,
): ExecutionRow[] {
  if (!plan?.length) {
    return steps.map((step, i) => ({ type: 'step', step, stepIndex: i, nested: false }));
  }
  // 分桶按 item.key（不按 title）：模型给出重复标题时 title 会撞车，第二次 set 覆盖第一次的
  // 空桶引用，导致两个计划项共享同一批步骤（重复渲染 + v-for key 撞车）。key 由 applyTaskPlan
  // /restoreExecutionTrace 保证同一快照内唯一，不会有这个问题。
  // byTitle 仅作旧历史数据兼容：新产生的实时事件与后端 execution_trace 都统一保存稳定 key；
  // 遗留 title 数据遇到重复标题时只认第一次出现，不把同一批步骤复制到多个桶。
  const byKey = new Map<string, ExecutionRow[]>();
  const byTitle = new Map<string, ExecutionRow[]>();
  for (const item of plan) {
    const bucket: ExecutionRow[] = [];
    byKey.set(item.key, bucket);
    if (!byTitle.has(item.title)) byTitle.set(item.title, bucket);
  }
  const pre: ExecutionRow[] = [];
  const tail: ExecutionRow[] = [];
  steps.forEach((step, i) => {
    const bucket = step.planKey ? byKey.get(step.planKey) || byTitle.get(step.planKey) : undefined;
    if (bucket) bucket.push({ type: 'step', step, stepIndex: i, nested: true });
    else if (step.planKey) tail.push({ type: 'step', step, stepIndex: i, nested: false });
    else pre.push({ type: 'step', step, stepIndex: i, nested: false });
  });
  const rows: ExecutionRow[] = [...pre];
  for (const item of plan) {
    const bucket = byKey.get(item.key) || [];
    if (item.status === 'invalidated') {
      // 被替换的步骤不再当组头，名下已发生的动作落到尾部，避免大纲出现在生成之后。
      for (const row of bucket) tail.push({ ...row, nested: false });
      continue;
    }
    if (hidePending && item.status === 'pending' && !bucket.length) continue;
    rows.push({ type: 'plan', item });
    rows.push(...bucket);
  }
  rows.push(...tail);
  return rows;
}

/**
 * 执行行缓存的指纹（2026-07-28）——`executionRows()` 复算与否只看它变没变。
 *
 * 为什么需要：生成期间 `nowTick` 每 250ms 跳一次，MessageList 的渲染函数因此每秒重跑 4 次；
 * 而 `executionRows` 是普通函数（模板里 `v-if` 和 `v-for` 各调一次），于是
 * `buildExecutionRows → collapseSubagentRows → dedupeRepeatedNotes → collapseSandboxRuns`
 * 这条链对**全部历史消息**每秒各跑 8 遍——历史消息的行一个字都不会变。
 *
 * 指纹只收「会改变**行结构**」的字段，故意不收 label / preview / 耗时 / favicon 这些：
 * 行对象持有的是 step **原对象引用**，展示字段变了模板照样重新渲染（读的是 `row.step.x`），
 * 不必让整条链失效。参与的是三路输入——
 *   ① 分桶：plan 的 key/title/status + step.planKey；
 *   ② 折叠：step 的 kind/name/status/有无 error/有无 target（`noTarget` 族判据）+ 归拢展开态；
 *   ③ 去重：相邻 note 的正文。
 * 漏收任何一项的表现都是「行结构该变却没变」，比多收一项贵得多——加字段时宁可多收。
 */
export function execRowsSignature(
  message: {
    taskPlan?: ExecutionMessage['taskPlan'];
    agentSteps?: AgentStep[];
    /** 多子智能体聚合头的展开态（收起时成员行整批不渲染）；字段挂在 ChatMessage 上 */
    subCollabExpanded?: boolean;
  },
  running: boolean,
  /** 本消息名下当前处于展开态的归拢组键（展开/收起会改变成员行是否渲染） */
  expandedGroupKeys: string[] = [],
): string {
  // 字段之间必须有分隔符（\u0001 分字段、\u0002 分条目）：直接拼串时相邻字段会粘连，
  // name='a'+status='' 与 name=''+status='a' 会撞出同一个指纹，缓存于是吐出过期的行。
  const parts: string[] = [running ? 'r1' : 'r0', `c${message.subCollabExpanded ? 1 : 0}`];
  for (const item of message.taskPlan || []) {
    parts.push(['p', item.key, item.title, item.status].join('\u0001'));
  }
  for (const step of message.agentSteps || []) {
    const s = step as { name?: string; status?: string; error?: string; target?: string; text?: string };
    parts.push([
      's', step.kind, s.name || '', s.status || '', step.planKey || '',
      s.error ? '1' : '0', s.target ? '1' : '0',
      // 公开文字参与行结构与去重判定，必须逐字进指纹。
      step.kind === 'note' ? String(s.text || '') : '',
    ].join('\u0001'));
  }
  for (const key of [...expandedGroupKeys].sort()) parts.push(`g\u0001${key}`);
  return parts.join('\u0002');
}

// ===== 顶栏「任务运行面板」统一派生（P0：单一状态源） =====
// 顶栏面板不再自行聚合另一套 taskPlan/subagentRuns 视图，而是与消息内执行卡共用
// 当前执行分段的同一份时间线数据——本函数是两者唯一的派生入口。

export interface RunPanelModel {
  /** 面板锚定的消息（当前执行分段；无则为 null，面板显示空态） */
  message: ExecutionMessage | null;
  /** 语义任务计划（update_plan 整表快照），与消息执行卡同一数组引用 */
  plan: NonNullable<ExecutionMessage['taskPlan']>;
  planVersion?: number;
  approvedVersion?: number;
  planDiverged?: boolean;
  /** 本轮目标契约（与计划同源，可缺省） */
  goalContract?: ExecutionMessage['taskGoalContract'];
  /** 统一时间线步骤（工具/子智能体/产物/检查/说明），与消息执行卡同一数组引用 */
  steps: AgentStep[];
  /** 本轮子智能体运行档（点击可打开独立对话窗） */
  subagentRuns: SubagentRun[];
  /** 是否有仍在运行的步骤/计划项 */
  running: boolean;
  /** Run 已到达终态；计划快照仍保留在历史里，但顶栏不应再把它作为活动任务展示。 */
  settled: boolean;
  /** 生成的文件（交付物），与消息文件卡同一数组引用 */
  files: GeneratedFile[];
}

type RunPanelMessage = { role: string } & ExecutionMessage & {
  runId?: string;
  executionSegmentIndex?: number;
};

/**
 * @deprecated 起任务协作面板**不再**用工具逐步回填。
 * 面板只展示语义 taskPlan（update_plan / 后端 provisional 整体步骤）。
 * 保留导出仅供旧测试/调试引用，生产 deriveRunPanel 已停用。
 */
export function activityPlanFromAgentSteps(
  steps: AgentStep[] | undefined | null,
  opts?: { failOpen?: boolean },
): NonNullable<ExecutionMessage['taskPlan']> {
  void steps;
  void opts;
  return [];
}

export function deriveRunPanel(messages: RunPanelMessage[]): RunPanelModel {
  const latest = [...messages].reverse().find((m) => m.role === 'assistant') || null;
  // 运行中追加要求会把同一 Run 切成新的 assistant 分段。计划是整轮状态，不是某一段
  // 的临时步骤：新分段即使已经收到了自己的工具事件，也不能用空 taskPlan 覆盖旧计划，
  // 否则面板会先变空、等模型再次 update_plan 才“重新出现”。只有新段明确发来自己的
  // 计划快照时才切换到它；不同 Run 之间绝不借用，避免上一轮计划串到新任务。
  const continuationWithoutPlan = latest
    && Number(latest.executionSegmentIndex || 0) > 0
    && Boolean(latest.runId)
    && !latest.taskPlan?.length;
  const planSource = continuationWithoutPlan
    ? [...messages].reverse().find(
        (message) => message !== latest
          && message.role === 'assistant'
          && message.runId === latest.runId
          && Boolean(message.taskPlan?.length),
      ) || latest
    : latest;
  // 面板其余数据仍读取最新分段：用户追加要求后新产生的工具、团队和文件要实时出现；
  // 只有“计划”跨分段继承，不能为了保计划把整个面板冻结在旧分段。
  // ：只认语义计划（update_plan / provisional）。禁止把每次 search/bash 的 intent
  // 摊成「当前进度」列表——那是执行时间线的事，不是任务协作整体步骤。
  const rawPlan = visibleTaskPlan((planSource?.taskPlan || []).filter(
    (item) => item && !String(item.key || '').startsWith('activity-'),
  ));
  const steps = latest?.agentSteps || [];
  const subagentRuns = latest?.subagentRuns || [];
  // Run 终态只决定面板是否继续跳动；Plan 状态仍原样展示服务端快照。
  const runSettled = Boolean(
    latest?.runCompletedAt
    || (
      latest?.runId
      && messages.some(
        (message) => message.role === 'assistant'
          && message.runId === latest.runId
          && Boolean(message.runCompletedAt),
      )
    ),
  );
  const plan = rawPlan;
  const running = !runSettled && (
    plan.some((item) => {
      const st = String(item.status || '').toLowerCase();
      return st === 'running' || st === 'in_progress' || st === 'active';
    }) ||
    steps.some((s) => 'status' in s && s.status === 'running') ||
    subagentRuns.some((r) => r.status === 'running')
  );
  return {
    message: latest,
    plan,
    planVersion: planSource?.taskPlanVersion,
    approvedVersion: planSource?.taskPlanApprovedVersion,
    // 只信显式 diverged：plan_version 在纯 status 推进时也会 +1，不能拿 version>approved 推断偏离。
    planDiverged: Boolean(planSource?.taskPlanDiverged),
    goalContract: planSource?.taskGoalContract,
    steps,
    subagentRuns,
    running,
    settled: runSettled,
    files: latest?.generatedFiles || [],
  };
}

// ===== 计时/展示格式化 =====

/** 运行中整数秒向下取整（12s），终态由服务端起止时间冻结到 0.1 秒（12.4s），超一分钟转分秒 */
export function formatDuration(ms: number, running = false): string {
  const safe = Math.max(0, Number(ms) || 0);
  const seconds = safe / 1000;
  if (seconds >= 60) {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}分${s}秒`;
  }
  return running ? `${Math.floor(seconds)}s` : `${seconds.toFixed(1)}s`;
}

export function formatFileSize(size: number): string {
  const bytes = Math.max(0, Number(size) || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  // GB 档（2026-07-26）：此前封顶 MB，1.5GB 的包显示成 "1536.0 MB"
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

export function fileTypeLabel(filename: string, mime?: string): string {
  const ext = filename.split('.').pop()?.toUpperCase();
  if (ext && ext.length <= 6) return ext;
  return mime || '文件';
}
