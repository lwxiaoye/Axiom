import { computed, getCurrentScope, onScopeDispose, ref, watch } from 'vue';
import { addChatQueueItem, cancelChatRun, confirmChatQueue, createAgentChatCompletion, decideGatewayApproval, deleteChatQueueItem, deleteThread, getChatQueue, getRunByClientRequest, getRunState, getSkills, getSubagents, getThreadActiveRun, getThreadMessages, getThreadSettings, getThreads, submitChatRunInput, pinThread, popChatQueue, renameThread, reorderChatQueue, resumeChatTurn, subscribeChatRun, submitMessageFeedback, updateChatQueueItem, updateThreadModel, uploadChatFile, uploadWorkspaceFile, type ActiveRun, type AssistantPreset, type ChatQueueAttachment, type ChatQueueItem, type Clarification, type KnowledgeSelection, type SkillItem, type SubagentItem, type TaskPlanEvent, type ThreadItem, type ThreadReference, type ThreadScope, type ToolStepEvent, type UploadedFile } from '../agentApi';
import { openAgentRunWindow } from '@/views/workflow/shared/runtimeRoute';
import type { ChatMessage } from '../components/MessageList.vue';
import type { CenterSectionKey } from './useAgentMarket';
import type { UserFileSelection, WorkFolderSelection } from '../myfiles.api';
import { findActiveRunReplayTarget, findActiveRunTraceSnapshot } from './activeRunHistory';
import {
  composerBubbleAttachments as buildComposerBubbleAttachments,
  isComposerReferenceKind,
} from '../utils/composerBubbleAttachments';
import { chatUploadFileError, isChatImageFile } from '../utils/chatUploadTypes';
import { limitFileSelection } from './filePicker';
import {
  currentDraftUserId,
  deleteDraftRecord,
  getDraftRecord,
  isListableAnonymousDraft,
  listDraftRecords,
  MAX_LISTED_ANONYMOUS_DRAFTS,
  newAnonymousDraftId,
  saveDraftRecord,
  type PersistedChatDraft,
} from './chatDrafts';
import {
  applyAttachmentsStatus,
  applyArtifactSaved,
  applyCapabilityLoaded,
  applyModelConnection,
  ingestPlanReportText,
  looksLikePlanReport,
  shouldHoldPlanStreamOffBody,
  applyPlanUpdate,
  applyResearchProgress,
  applyTaskPlan,
  applySubagentStep,
  applyCompaction,
  applyToolEvent,
  clearTransientReasoning,
  ensureRunningThought,
  revealAssistantOutput,
  markRunCancelled,
  markRunCompleted,
  markRunPartial,
  settleHitlToolSteps,
  settleRunExecutionSteps,
  markRunFailed,
  markRunStarted,
  restoreExecutionTrace,
  type ExecutionTracePayload,
} from './executionTimeline';
import { absorbFailedRunIfUserClarification } from '../utils/userClarification';
import {
  isSealedAssistantSegment,
  routeArtifactEventToRun,
  routeToolEventToRun,
  sealSegmentSteps,
  segmentSplitOffset,
} from './runSegmentState';
import {
  CANCEL_INTENT_RE,
  canInstructQueueItem,
  resolveFollowUpIntent,
} from './chatQueueRules';
import { hasSkillReference, messageTurnSkills } from './messageTurnContext';
import type { InterviewInput } from '../builtinAssistants/interview/types';
import {
  researchRunDelivered,
  type RunTerminalOutcome,
} from './researchProfileLifecycle';
import { createSmoothStreamText } from './smoothStreamText';
import { isResearchTurn } from '../utils/researchReport';
import {
  createCommentaryStream,
  createReasoningStream,
  type CommentaryStream,
  type ReasoningStream,
} from './harnessProcessStreams';
import {
  CAMPUS_ASSISTANT_PRESET,
  PRESENTATION_ASSISTANT_PRESET,
  getBuiltinAssistantByPreset,
  getBuiltinUiPolicy,
  isRestrictedAssistantPreset,
} from '../utils/builtinAssistants';

type UseCenterChatOptions = {
  activeSection: { value: CenterSectionKey };
  appList: { value: any[] };
  reloadApps: () => Promise<void>;
  showError: (error: unknown) => void;
  showNotice: (message: string) => void;
  remountChatPage?: () => void;
  /** Fixed system-application identity.  When set, drafts and loaded Threads cannot switch it. */
  fixedAssistantPreset?: AssistantPreset;
  /** Server-side history partition; defaults to the fixed preset or ordinary main chat. */
  threadScope?: ThreadScope;
};

export type ChatTaskState = 'idle' | 'running' | 'completed';

type TurnAttachment = {
  filename: string;
  text: string;
  file_id?: string;
  sha256?: string;
  kind?: string;
  image_url?: string;
  /** 图片压缩缩略图（data URL）：随消息落库（attachments_json），历史回放图片卡据此显示 */
  preview_url?: string;
  status?: string;
  note?: string;
};

const REC_RE = /\[\[RECOMMEND:([^\]]*)\]\]/;

// 取消意图词表——必须与后端 turn_context_builder._CANCEL_WORDS 逐字一致。
// 两边不一致会互相打架：前端认、后端不认 → 旧卡标「已跳过」但后端照常走修订循环出新卡；
// 后端认、前端不认 → 用户说了取消却被前端硬拦住发不出去。
const PAUSE_INTENT_RE = /^(先)?(暂停|停一下|等一下|稍等|先别做|先停)[。！! ]*$/;
const REPLACE_INTENT_RE = /(停止|结束|取消).{0,8}(重新|重来|新任务)|(推翻|从头).{0,5}(重做|开始)/;
const NEW_TASK_INTENT_RE = /(另一个|另外一个|全新|无关).{0,8}(任务|问题|项目)/;

const DEFAULT_MODEL_STORAGE_KEY = 'agent-active-model';

function readDefaultModel(): string {
  if (typeof localStorage === 'undefined') return '';
  return String(localStorage.getItem(DEFAULT_MODEL_STORAGE_KEY) || '');
}


/** 客户端幂等键（N-02）：每次「发送」生成一次，随 POST /chat 携带。首帧丢失后凭它
 *  反查服务端是否已建 Run——决定「订阅续接」还是「恢复草稿允许重发」。 */
function newClientRequestId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID().replace(/-/g, '');
    }
  } catch {
    // 落回时间戳+随机数
  }
  return `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 14)}`;
}

/** 小型稳定摘要：只用于判断一次「未确认受理」的重试是否仍是同一份输入，不承担安全用途。 */
function requestFingerprint(value: unknown): string {
  const raw = JSON.stringify(value);
  let hash = 2166136261;
  for (let i = 0; i < raw.length; i += 1) {
    hash ^= raw.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return `${raw.length}:${(hash >>> 0).toString(16)}`;
}

/** 计划确认续接：与后端 choice_approves_plan 同一批执行词，前端用来判断是否解锁执行轮正文。 */
const PLAN_EXECUTE_RESUME_RE = /开始执行|批准修订|批准这次|确认执行|执行此计划|执行计划|实施此计划|实施计划|按计划执行|按这个计划|跟着计划|开始干活/;
const PLAN_SKIP_RESUME_RE = /^(?:跳过|__PLAN_SKIP__)[。.!！]?$/;

function visiblePlanResumeText(value: unknown): string {
  if (typeof value === 'string') {
    const text = value.trim();
    if (!text || PLAN_SKIP_RESUME_RE.test(text)) return '';
    return text;
  }
  if (Array.isArray(value)) {
    return value.map((item) => String(item ?? '').trim()).filter(Boolean).join(' ').trim();
  }
  return '';
}

/** 等待网络恢复（N-03）：navigator.onLine=false 时挂在 online 事件上。返回 true=已回到
 *  在线（或本就在线/超时后已在线）；false=等待被中止或超时后仍离线。离线等待不烧重连
 *  额度——「12 秒断网再恢复」这类场景应在 online 一瞬间按游标重订，而不是额度耗尽后放弃。 */
function waitForOnline(signal: AbortSignal, timeoutMs: number): Promise<boolean> {
  if (typeof window === 'undefined' || typeof navigator === 'undefined' || navigator.onLine !== false) {
    return Promise.resolve(true);
  }
  return new Promise((resolve) => {
    let timer: number | null = null;
    const cleanup = (result: boolean) => {
      window.removeEventListener('online', onOnline);
      signal.removeEventListener('abort', onAbort);
      if (timer != null) window.clearTimeout(timer);
      resolve(result);
    };
    const onOnline = () => cleanup(true);
    const onAbort = () => cleanup(false);
    window.addEventListener('online', onOnline);
    signal.addEventListener('abort', onAbort);
    timer = window.setTimeout(() => cleanup(navigator.onLine !== false), timeoutMs);
  });
}


export function useCenterChat(options: UseCenterChatOptions) {
  const fixedAssistantPreset = options.fixedAssistantPreset;
  const threadScope: ThreadScope = options.threadScope || fixedAssistantPreset || 'ordinary';
  if (fixedAssistantPreset && threadScope !== fixedAssistantPreset) {
    throw new Error('系统内置应用预设与会话范围不一致');
  }
  const activeModel = ref(readDefaultModel());
  const chatInput = ref('');
  const chatMessages = ref<ChatMessage[]>([]);
  const chatLoading = ref(false);
  let interviewInputProvider: ((message: string) => InterviewInput | null) | undefined;
  let interviewInputRejected: ((failure: unknown) => void) | undefined;
  function setInterviewInputProvider(provider: (message: string) => InterviewInput | null, onRejected?: (failure: unknown) => void) {
    interviewInputProvider = provider;
    interviewInputRejected = onRejected;
  }

  async function sendInterviewMessage(message: string, input: InterviewInput, attachments?: UploadedFile[]) {
    if (assistantPreset.value !== 'interview' || chatLoading.value || stopInFlight) return;
    // These controls change interview state without consuming the answer draft.
    if (['pause', 'resume', 'hint', 'finish', 'retry'].includes(input.action)) {
      const userMessage: ChatMessage = { id: nextLocalId(), role: 'user', content: message };
      chatMessages.value.push(userMessage);
      await runAssistantTurn(message, {
        interviewInput: input,
        userMessageLocalId: userMessage.id,
        contextOverride: { model: activeModel.value },
      });
      return;
    }
    chatInput.value = message;
    pendingAttachments.value = attachments || [];
    await sendChat({ interviewInput: input });
  }
  const chatTaskState = ref<ChatTaskState>('idle');
  // 进入主对话始终是欢迎/新对话。历史列表后台预加载，供用户从抽屉主动打开。
  const restoringLatestThread = ref(false);
  const threadList = ref<ThreadItem[]>([]);
  const threadsLoading = ref(false);
  const currentThreadId = ref('');
  const selectedWorkFolder = ref<WorkFolderSelection | null>(null);
  const uploadingWorkFolder = ref(false);
  const assistantPreset = ref<AssistantPreset | undefined>(fixedAssistantPreset);
  const selectedSkills = ref<SkillItem[]>([]);
  const selectedKnowledgeList = ref<KnowledgeSelection[]>([]);
  // composer 选中的「我的文件」：发送即消费（气泡上留图标），重新生成沿用上一轮。
  const selectedFileList = ref<UserFileSelection[]>([]);
  // composer 「最近的对话」（2026-07-28，+ 菜单）：引用历史会话的对话记录。
  // 一次性（发送即清空，语义同 Skill）：一份历史转录读一次就够了。
  const selectedThreadList = ref<ThreadReference[]>([]);
  // 重新生成/消歧重发沿用上一轮的引用（同 lastTurnSkills 的理由：一次性选择已被清空，
  // 不记住就会「重答一遍，但这次没看引用的对话」）
  let lastTurnThreads: ThreadReference[] = [];
  let lastTurnFiles: UserFileSelection[] = [];
  let lastTurnKnowledge: KnowledgeSelection[] = [];
  // @ 选中的智能体：下一轮主对话携带 subagent_id，由主对话直接委托；发送后清空。
  const selectedSubagent = ref<SubagentItem>();
  // 执行团队成员的独立过程窗。runKey 绑定本次委派运行档，让窗口直接消费主对话
  // 收到的流式 subagent.* 事件；它与 composer 的一次性 selectedSubagent 语义分离。
  const openSubagents = ref<Array<SubagentItem & { runKey?: string }>>([]);
  const subagents = ref<SubagentItem[]>([]);
  const mentionSkills = ref<SkillItem[]>([]);
  // 「网页搜索」pill（+ 菜单选中）：本会话内强制模型先联网再答的显式指令。联网搜索工具
  // 本身常驻后端（模型自主判断何时搜索），pill 只是用户点名要搜——会话级状态，随
  // clearConversationSelections 清空，不做全局持久（永久强制先搜会又慢又费钱）
  const webSearchOn = ref(false);
  const pendingAttachments = ref<UploadedFile[]>([]);
  const uploadingFile = ref(false);
  // Plan Profile 开关：从发送到终态保持选中，挂起等待用户时不归位。
  // 活动 Run 的 agent_mode 是跨刷新的权威事实源，本地 Set 只补足当前页面内的短暂窗口。
  const planMode = ref(false);
  // 本页已知的 Plan Profile Run，用于创建回执到活动 Run 快照之间的短暂衔接。
  const planProfileRuns = new Set<string>();
  // 「开始执行」之后禁止被历史 run.started（agent_mode=plan）再次点亮胶囊。
  const unlockedPlanRuns = new Set<string>();
  // Deep Research 与计划模式互斥，且是一次性 Profile：用户显式开启后，
  // 只在本轮权威 run.completed 后自动归位。停止、部分完成、失败或等待用户时
  // 不得误关，否则「继续」/重试会静默降成 standard。已完成报告的 agent_mode
  // 仍保留在消息上供蓝框查看，不由 composer 开关反推。
  // 活动 Run 仍以 agent_mode=research 作为刷新/切会话时的权威恢复事实。
  const researchProfile = ref(false);
  const researchProfileRuns = new Set<string>();
  // 运行中消息队列（B，Codex 式）：服务端持久化——GET 拉取回填、POST 追加、PUT 编辑、
  // DELETE 删除、POST /pop 原子取队首供终态派发；前端左侧手柄重排后 PATCH 完整顺序。
  const messageQueue = ref<ChatQueueItem[]>([]);
  const queueLoading = ref(false); // GET 拉取中
  const queueBusy = ref(false); // 增/删/改/派发网络请求中：队列 UI 操作按钮据此禁用
  const editingQueueId = ref(''); // 正在编辑的队列项 id（''=未在编辑）
  // 用户主动停止后暂停派发：中断说明「我要改主意了」，此时把排队消息一股脑冲出去是
  // 用户最不想要的结果。队列原样保留，等用户点「继续」或删改后再派发。
  // 只有点停止按钮才置位；sendChat 内部为了打断-重发调用的 stopChat 不置位（那是发新消息，
  // 不是喊停）。
  // per-thread（审计项 21）：暂停是「A 会话那次停止」的局部状态——切到 B 不得泄漏，
  // 切回 A 也不悄悄解除（A 的队列仍等用户点「继续」）。
  const queuePausedThreads = ref(new Set<string>());
  const queuePaused = computed(() => queuePausedThreads.value.has(currentThreadId.value || ''));
  function setQueuePaused(threadId: string, paused: boolean) {
    const key = threadId || '';
    if (queuePausedThreads.value.has(key) === paused) return;
    const next = new Set(queuePausedThreads.value);
    if (paused) next.add(key);
    else next.delete(key);
    queuePausedThreads.value = next;
  }
  const editingQueueText = ref('');
  // 队首派发失败（Codex 对齐 2026-07-26）：失败态是**每条队列项**自己的，不是队列级横幅
  // ——Codex 的 `isMessagePaused(id)` 只标那一条：警告图标 + 两行 tooltip，
  // 并且该条的主按钮由「引导」原地变成「重试」。队列级横幅只留给「中断导致的暂停」。
  const failedQueueItemId = ref('');
  function retryQueueDispatch() {
    // 不乐观清除失败标记：重试若再次失败（尤其是**挂死不返回**）标记就永远补不回来，
    // 用户会看到警告消失、按钮变回「引导」，却什么都点不动。成功路径自己会清。
    const threadId = currentThreadId.value;
    if (threadId) void dispatchNextQueuedItem(threadId);
  }
  /** 队首派发的兜底超时：pop 挂住不返回时，`queueBusy` 会永久为真，把整张候车区
   *  （引导/删除/编辑/拖拽）全部锁死，只能刷新页面才能恢复——真机用挂起请求实测到过。
   *  这里不追求取消底层请求，只保证 UI 一定能解锁、队列项原样留着可重试；
   *  服务端那条 dispatching 项由 90s 租约超时自动回收，不丢不重。 */
  const QUEUE_POP_TIMEOUT_MS = 15000;
  function startQueuePopWatchdog(threadId: string) {
    const state = { tripped: false, cancel: () => {} };
    const timer = setTimeout(() => {
      state.tripped = true;
      queueBusy.value = false;
      failedQueueItemId.value = messageQueue.value[0]?.id || '';
      if (currentThreadId.value === threadId) {
        options.showNotice('待发送队列派发超时，可在这条消息上点「重试」');
      }
    }, QUEUE_POP_TIMEOUT_MS);
    state.cancel = () => clearTimeout(timer);
    return state;
  }
  // 跟进行为对齐 Codex 排队模式（截图交互）：运行中回车默认进队列；
  // 点「调整方向」才注入当前 Run。localStorage=steer 视为关掉排队。
  const FOLLOW_UP_MODE_KEY = 'center-chat-follow-up-mode';
  type FollowUpMode = 'steer' | 'queue';
  function readFollowUpMode(): FollowUpMode {
    try {
      return localStorage.getItem(FOLLOW_UP_MODE_KEY) === 'steer' ? 'steer' : 'queue';
    } catch {
      return 'queue';
    }
  }
  const followUpMode = ref<FollowUpMode>(readFollowUpMode());
  function setFollowUpMode(mode: FollowUpMode) {
    followUpMode.value = mode;
    try {
      localStorage.setItem(FOLLOW_UP_MODE_KEY, mode);
    } catch {
      // 隐私模式/存储配额：内存生效即可，不因为存不下就拒绝切换
    }
  }
  // 立即引导（C）：区别于「停止」——不取消当前 Run，只是把消息作为引导发给它
  const submittingRunInput = ref(false);
  // 引导对账：提交时记入 pending，收到 input.applied 记入 applied。
  // Run 收尾时两者相减＝「说了但从没被读到」的引导——必须如实告诉用户，否则用户以为
  // 自己插了话、模型却全程没看见（旧实现里这种情况完全静默）。
  const pendingInstructionIds = new Set<string>();
  const appliedInstructionIds = new Set<string>();
  // 会话级 composer 草稿（切画面不丢附件修复，2026-07-15；审计项 15 扩展）：切会话时把
  // 「输入文字 + 待发附件 + 知识库/文件/联网选择」按 threadId 暂存（'' = 新对话草稿），
  // 切回原会话时恢复——既不跨会话串味，选择也不再「切走即丢」。仅内存，刷新即失。
  type ComposerDraft = {
    input: string;
    attachments: UploadedFile[];
    knowledge?: KnowledgeSelection[];
    files?: UserFileSelection[];
    threads?: ThreadReference[];
    webSearch?: boolean;
    /** D-03：Skill 与任务模式同属发送上下文，草稿不带会「表面恢复、语义已变」 */
    skills?: SkillItem[];
    planMode?: boolean;
    assistantPreset?: AssistantPreset;
    workspaceFolder?: WorkFolderSelection;
  };
  const conversationDrafts = new Map<string, ComposerDraft>();
  // 首帧前断线的请求身份同时按输入摘要和草稿键保存，保证切会话/刷新后的同稿重试仍去重。
  const pendingClientRequests = new Map<string, string>();
  const pendingRequestByDraft = new Map<string, { fingerprint: string; requestId: string }>();
  // 未发送新会话的稳定草稿 id（D-02）：不再让所有匿名新会话共用空字符串键——B、C 两个
  // 未发送草稿各有身份、互不覆盖；首次发送成功后删除草稿记录（threadId 接管）。
  const currentDraftId = ref(newAnonymousDraftId(threadScope));
  /** composer 归属键：有会话=threadId（与既有内存草稿键兼容），无会话=当前草稿 id。 */
  function composerKey(): string {
    return currentThreadId.value || currentDraftId.value;
  }
  // 历史抽屉「未发送草稿」分组（D-02）：仅列匿名新会话草稿；会话内草稿跟着会话走
  type DraftEntry = { draftId: string; preview: string; updatedAt: number };
  const draftEntries = ref<DraftEntry[]>([]);
  let draftUserId = '';
  const draftUserIdReady = currentDraftUserId().then((id) => {
    draftUserId = id || 'anon';
    return draftUserId;
  });
  // 图片附件缩略图内存缓存（dbId → previewUrl 列表）：previewUrl 是本地 data URL、有意不落库，
  // loadThread 从 DB 重建消息时据此回填，会话内切走再切回图片卡不退化成无图卡。LRU 上限防膨胀。
  const attachmentPreviewCache = new Map<number, Array<string | undefined>>();
  const PREVIEW_CACHE_MAX = 40;

  function cacheAttachmentPreviews(dbId: number, atts: Array<{ previewUrl?: string }>) {
    if (!atts.some((a) => a.previewUrl)) return;
    attachmentPreviewCache.delete(dbId); // 重插到尾部（LRU 触碰）
    attachmentPreviewCache.set(dbId, atts.map((a) => a.previewUrl));
    while (attachmentPreviewCache.size > PREVIEW_CACHE_MAX) {
      const oldest = attachmentPreviewCache.keys().next().value;
      if (oldest === undefined) break;
      attachmentPreviewCache.delete(oldest);
    }
  }

  /** 离开当前会话前暂存 composer 草稿；进入会话时恢复它的草稿（无则清空）。
   *  知识库/文件/联网选择随草稿保存（审计项 15）：切走再切回，选择不再默默丢失。 */
  function saveComposerDraft() {
    // 键=composerKey（D-02）：会话用 threadId，未发送新会话用其独立草稿 id——
    // 多个匿名草稿不再共用空字符串键互相覆盖
    const key = composerKey();
    const hasContent =
      chatInput.value.trim()
      || pendingAttachments.value.length
      || selectedSkills.value.length
      || selectedKnowledgeList.value.length
      || selectedFileList.value.length
      || selectedThreadList.value.length
      || webSearchOn.value
      || (!currentThreadId.value && planMode.value);
    if (hasContent) {
      conversationDrafts.set(key, {
        input: chatInput.value,
        attachments: pendingAttachments.value,
        knowledge: selectedKnowledgeList.value.length ? [...selectedKnowledgeList.value] : undefined,
        files: selectedFileList.value.length ? [...selectedFileList.value] : undefined,
        threads: selectedThreadList.value.length ? [...selectedThreadList.value] : undefined,
        webSearch: webSearchOn.value || undefined,
        skills: selectedSkills.value.length ? [...selectedSkills.value] : undefined,
        planMode: planMode.value || undefined,
        assistantPreset: assistantPreset.value,
        workspaceFolder: selectedWorkFolder.value ? { ...selectedWorkFolder.value } : undefined,
      });
    } else {
      conversationDrafts.delete(key);
    }
  }

  function restoreComposerDraft(key: string) {
    const draft = conversationDrafts.get(key || '');
    chatInput.value = draft?.input || '';
    pendingAttachments.value = draft?.attachments || [];
    if (draft?.knowledge?.length) selectedKnowledgeList.value = [...draft.knowledge];
    if (draft?.files?.length) selectedFileList.value = limitFileSelection(draft.files);
    if (draft?.threads?.length) selectedThreadList.value = [...draft.threads];
    if (draft?.webSearch) webSearchOn.value = true;
    if (draft?.skills?.length) selectedSkills.value = [...draft.skills];
    if (!currentThreadId.value) assistantPreset.value = fixedAssistantPreset || draft?.assistantPreset;
    if (!currentThreadId.value) selectedWorkFolder.value = assistantPreset.value ? null : draft?.workspaceFolder || null;
    // 任务模式只对「新对话草稿」恢复：会话视图的开关态由该会话活动 Run 决定（§A）
    if (!currentThreadId.value && draft?.planMode) planMode.value = true;
    syncUploadingFlag();
  }

  /** 取某会话草稿的附件容器（没有则建空草稿）：异步预处理完成时用户可能已切会话，
   *  占位卡/成果必须写回原会话的容器（审计项 7）。 */
  function draftAttachmentsFor(key: string): UploadedFile[] {
    let draft = conversationDrafts.get(key);
    if (!draft) {
      draft = { input: '', attachments: [] };
      conversationDrafts.set(key, draft);
    }
    return draft.attachments;
  }

  // ---- 草稿本机持久化（D-01/D-02/D-03）：内存 Map 之外再落 IndexedDB，刷新/重启不丢 ----

  /** 落盘键：会话草稿 thread:<id>（与匿名草稿命名空间隔离），新会话草稿即其 draftId。 */
  function draftStoreKey(): string {
    return currentThreadId.value ? `thread:${currentThreadId.value}` : currentDraftId.value;
  }

  function draftPreview(record: PersistedChatDraft): string {
    return (
      (record.content || '').trim().slice(0, 40)
      || (record.attachments?.length ? `[附件] ${record.attachments[0].filename}` : '（空草稿）')
    );
  }

  function upsertDraftEntry(record: PersistedChatDraft) {
    // 历史主列表只收有实质内容的匿名草稿；仅开关/上下文不算「聊过什么」。
    if (!isListableAnonymousDraft(record)) {
      removeDraftEntryLocal(record.draftId);
      return;
    }
    const next = draftEntries.value.filter((d) => d.draftId !== record.draftId);
    next.unshift({ draftId: record.draftId, preview: draftPreview(record), updatedAt: record.updatedAt });
    // 最新优先；超出上限的旧草稿移出列表并清理本机存储，防止失败恢复堆成失败清单。
    const kept = next.slice(0, MAX_LISTED_ANONYMOUS_DRAFTS);
    const dropped = next.slice(MAX_LISTED_ANONYMOUS_DRAFTS);
    draftEntries.value = kept;
    for (const item of dropped) {
      conversationDrafts.delete(item.draftId);
      const pending = pendingRequestByDraft.get(item.draftId);
      if (pending) pendingClientRequests.delete(pending.fingerprint);
      pendingRequestByDraft.delete(item.draftId);
      void deleteDraftRecord(item.draftId);
    }
  }

  function removeDraftEntryLocal(draftId: string) {
    if (draftEntries.value.some((d) => d.draftId === draftId)) {
      draftEntries.value = draftEntries.value.filter((d) => d.draftId !== draftId);
    }
  }

  function buildDraftRecord(): PersistedChatDraft | null {
    const hasContent =
      chatInput.value.trim()
      || pendingAttachments.value.length
      || selectedSkills.value.length
      || selectedKnowledgeList.value.length
      || selectedFileList.value.length
      || selectedThreadList.value.length
      || webSearchOn.value
      || (!currentThreadId.value && planMode.value);
    if (!hasContent) return null;
    const storeKey = draftStoreKey();
    const pendingRequest = pendingRequestByDraft.get(storeKey);
    return {
      draftId: storeKey,
      userId: draftUserId,
      threadId: currentThreadId.value || null,
      content: chatInput.value,
      // 附件只存展示元数据 + 压缩缩略图（D-03）：原图 data URL / 解析全文刻意不落盘
      attachments: pendingAttachments.value.map((a) => ({
        filename: a.filename,
        kind: a.kind,
        fileId: a.file_id,
        sha256: a.sha256,
        status: a.status,
        note: a.note,
        thumbUrl: a.thumbUrl,
      })),
      skills: [...selectedSkills.value],
      subagent: selectedSubagent.value ? { ...selectedSubagent.value } : undefined,
      knowledge: [...selectedKnowledgeList.value],
      files: selectedFileList.value.map((f) => ({ id: f.id, filename: f.filename })),
      threads: selectedThreadList.value.map((t) => ({ id: t.id, title: t.title })),
      webSearch: webSearchOn.value,
      planMode: planMode.value,
      assistantPreset: assistantPreset.value,
      workspaceFolder: selectedWorkFolder.value ? { ...selectedWorkFolder.value } : undefined,
      pendingRequestId: pendingRequest?.requestId,
      pendingRequestFingerprint: pendingRequest?.fingerprint,
      updatedAt: Date.now(),
      version: 1,
    };
  }

  let draftPersistTimer: number | null = null;

  /** 立即落盘当前 composer（切会话/页面隐藏/发送前 flush；空内容=删除记录）。 */
  function persistComposerDraftNow() {
    if (draftPersistTimer != null) {
      window.clearTimeout(draftPersistTimer);
      draftPersistTimer = null;
    }
    const key = draftStoreKey();
    const record = buildDraftRecord();
    if (record) {
      // 用户信息解析是异步的；不能把启动最初几百毫秒内的输入写到共享 anon 命名空间。
      void draftUserIdReady.then((userId) => saveDraftRecord({ ...record, userId }));
      if (!record.threadId) upsertDraftEntry({ ...record, userId: draftUserId || 'anon' });
    } else {
      void deleteDraftRecord(key);
      removeDraftEntryLocal(key);
    }
  }

  function schedulePersistComposerDraft() {
    if (draftPersistTimer != null) window.clearTimeout(draftPersistTimer);
    draftPersistTimer = window.setTimeout(() => {
      draftPersistTimer = null;
      persistComposerDraftNow();
    }, 400);
  }

  // 输入/附件/发送上下文任一变化即（防抖）落盘；发送清空 composer 后同一管线自动删除记录
  watch(
    [chatInput, pendingAttachments, selectedSkills, selectedSubagent, selectedKnowledgeList, selectedFileList,
      selectedThreadList, webSearchOn, planMode, assistantPreset, selectedWorkFolder],
    schedulePersistComposerDraft,
    { deep: true },
  );

  /** 把落盘草稿应用到 composer。附件如实降级（D-03）：解析全文/原图不持久化，
   *  卡片标注「需重新上传」，不假装内容还在。 */
  function applyDraftRecord(record: PersistedChatDraft) {
    chatInput.value = record.content || '';
    const restoredAttachments = (record.attachments || []).map((a) => ({
      uid: nextLocalId(),
      filename: a.filename,
      kind: a.kind || 'file',
      file_id: a.fileId || '',
      sha256: a.sha256 || '',
      text: '',
      chars: 0,
      truncated: false,
      status: a.fileId ? 'ok' as const : 'failed' as const,
      note: a.fileId ? a.note : '旧版草稿没有文件身份，请重新上传',
      // 缩略图只用于展示，不能冒充原图 image_url 发送给模型。
      previewUrl: undefined,
      thumbUrl: a.thumbUrl,
      needsReupload: !a.fileId,
    }));
    if (!currentThreadId.value) assistantPreset.value = fixedAssistantPreset || record.assistantPreset;
    if (!currentThreadId.value) selectedWorkFolder.value = assistantPreset.value ? null : record.workspaceFolder || null;
    const draftPreset = fixedAssistantPreset || record.assistantPreset;
    const restrictedDraft = isRestrictedAssistantPreset(draftPreset);
    const draftPolicy = getBuiltinUiPolicy(draftPreset);
    pendingAttachments.value = draftPolicy?.imageOnlyUpload
      ? restoredAttachments.filter((item) => item.kind === 'image')
      : restoredAttachments;
    selectedSkills.value = restrictedDraft ? [] : [...(record.skills || [])];
    selectedSubagent.value = restrictedDraft
      ? undefined
      : record.subagent ? { ...record.subagent } : undefined;
    selectedKnowledgeList.value = draftPolicy?.hideKnowledge ? [] : [...(record.knowledge || [])];
    selectedFileList.value = draftPolicy?.hideFiles
      ? []
      : limitFileSelection((record.files || []).map((f) => ({ ...f })) as UserFileSelection[]);
    selectedThreadList.value = draftPolicy?.hideThreads ? [] : (record.threads || []).map((t) => ({ ...t }));
    webSearchOn.value = draftPolicy?.hideWebSearchToggle ? false : Boolean(record.webSearch);
    if (!currentThreadId.value) planMode.value = draftPolicy?.hidePlanMode ? false : Boolean(record.planMode);
    if (record.pendingRequestId && record.pendingRequestFingerprint) {
      pendingClientRequests.set(record.pendingRequestFingerprint, record.pendingRequestId);
      pendingRequestByDraft.set(record.draftId, {
        fingerprint: record.pendingRequestFingerprint,
        requestId: record.pendingRequestId,
      });
    }
    syncUploadingFlag();
  }

  function composerIsEmpty(): boolean {
    return !chatInput.value.trim()
      && !pendingAttachments.value.length
      && !selectedSkills.value.length
      && !selectedSubagent.value
      && !selectedKnowledgeList.value.length
      && !selectedFileList.value.length
      && !selectedThreadList.value.length
      && (Boolean(currentThreadId.value) || !selectedWorkFolder.value)
      && !webSearchOn.value
      && !planMode.value;
  }

  function draftMatchesThreadScope(record: PersistedChatDraft): boolean {
    const recordScope: ThreadScope = record.assistantPreset || 'ordinary';
    // Thread drafts already have a stable thread:<id> key and are validated against the backend
    // when opened.  Missing preset on an old thread draft is accepted, then forced to this page.
    if (record.threadId && fixedAssistantPreset && !record.assistantPreset) return true;
    return recordScope === threadScope;
  }

  /** 打开会话后（内存草稿缺席时）从本机存储回填（D-01）：仅 composer 仍为空时应用。 */
  async function restorePersistedThreadDraft(threadId: string) {
    if (!composerIsEmpty()) return;
    const record = await getDraftRecord(`thread:${threadId}`);
    if (!record || !draftMatchesThreadScope(record)) return;
    if (currentThreadId.value !== threadId || !composerIsEmpty()) return;
    applyDraftRecord(record);
  }

  // 启动回填（D-01/D-02）：解析用户 → 抽屉草稿列表 → 新对话视图为空时接管最近一份匿名草稿
  void (async () => {
    draftUserId = await draftUserIdReady;
    try {
      const rows = (await listDraftRecords(draftUserId)).filter(draftMatchesThreadScope);
      // 匿名草稿：只列有正文/附件的；超量与空壳（仅开关）清理，避免历史像失败清单。
      const listable = rows.filter((r) => isListableAnonymousDraft(r));
      const kept = listable.slice(0, MAX_LISTED_ANONYMOUS_DRAFTS);
      const dropIds = new Set([
        ...listable.slice(MAX_LISTED_ANONYMOUS_DRAFTS).map((r) => r.draftId),
        ...rows.filter((r) => !r.threadId && !isListableAnonymousDraft(r)).map((r) => r.draftId),
      ]);
      for (const draftId of dropIds) {
        void deleteDraftRecord(draftId);
      }
      draftEntries.value = kept.map((r) => ({
        draftId: r.draftId,
        preview: draftPreview(r),
        updatedAt: r.updatedAt,
      }));
      const latest = kept[0];
      if (latest && !currentThreadId.value && composerIsEmpty()) {
        currentDraftId.value = latest.draftId;
        applyDraftRecord(latest);
      }
    } catch {
      // 草稿是增强路径：读取失败不影响主链路
    }
  })();

  // 页面隐藏/关闭前强制 flush（D-03）：debounce 窗口内的最后一次输入不丢
  const handleDraftVisibilityChange = () => {
    if (document.visibilityState === 'hidden') persistComposerDraftNow();
  };
  const handleDraftBeforeUnload = () => persistComposerDraftNow();
  if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
    document.addEventListener('visibilitychange', handleDraftVisibilityChange);
  }
  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('beforeunload', handleDraftBeforeUnload);
  }

  /** 历史抽屉「未发送草稿」条目点击（D-02）：切到该匿名草稿继续编辑。 */
  async function openDraft(draftId: string) {
    if (!draftId) return;
    historyOpen.value = false;
    options.activeSection.value = 'chat';
    if (!currentThreadId.value && currentDraftId.value === draftId) return; // 已在该草稿
    // 离开当前视图：断前台流 + 暂存并落盘当前草稿（同 resetChat 语义）
    abortController?.abort();
    abortController = null;
    stopVisibleSubscription();
    saveComposerDraft();
    persistComposerDraftNow();
    viewVersion += 1;
    chatMessages.value = [];
    currentThreadId.value = '';
    currentDraftId.value = draftId;
    clearConversationSelections();
    restoreComposerDraft(draftId);
    if (composerIsEmpty()) {
      const record = await getDraftRecord(draftId);
      if (record && !currentThreadId.value && currentDraftId.value === draftId && composerIsEmpty()) {
        applyDraftRecord(record);
      }
    }
    refreshRunUi();
  }

  /** 删除一份未发送草稿（抽屉入口）。正在编辑的就是它时顺带清空 composer。 */
  async function discardDraft(draftId: string) {
    conversationDrafts.delete(draftId);
    const pending = pendingRequestByDraft.get(draftId);
    if (pending) pendingClientRequests.delete(pending.fingerprint);
    pendingRequestByDraft.delete(draftId);
    removeDraftEntryLocal(draftId);
    await deleteDraftRecord(draftId);
    if (!currentThreadId.value && currentDraftId.value === draftId) {
      chatInput.value = '';
      pendingAttachments.value = [];
      clearConversationSelections();
      syncUploadingFlag();
    }
  }

  /** 按 uid 在「当前 composer + 各会话草稿」里定位附件所在数组（上传是异步的，
      完成回调时用户可能已切到别的会话——不跨容器找就会留一张永远转圈的占位卡）。 */
  function findAttachmentContainer(uid?: number): UploadedFile[] | null {
    if (uid == null) return null;
    if (pendingAttachments.value.some((a) => a.uid === uid)) return pendingAttachments.value;
    for (const draft of conversationDrafts.values()) {
      if (draft.attachments.some((a) => a.uid === uid)) return draft.attachments;
    }
    return null;
  }
  let subagentsLoaded = false;
  let mentionSkillsLoaded = false;
  const historyOpen = ref(false);
  const threadSearch = ref('');
  const threadHasMore = ref(false);
  const THREADS_PAGE_SIZE = 30;
  let searchTimer: number | null = null;
  let chatTaskStateTimer: number | null = null;
  let abortController: AbortController | null = null;
  // 首帧前断线时，反查接口也可能暂时不可用/尚未看到 Run。此时恢复输入，但同一份输入
  // 再次发送必须复用原 client_request_id；否则用户点重试会绕开服务端幂等约束，产生双 Run。
  // stopChat 打断互斥（P0-4）：stopChat 内部对已启动的 Run 会先同步清空 activeRuns（chatLoading
  // 因此提前变 false），再 await 后端取消确认——这段网络往返窗口内如果又有 sendChat/stopChat
  // 调用，必须排在同一次停止流程后面，不能看着 chatLoading 已被清空就误判"没有在生成"而抢先起飞。
  let stopInFlight: Promise<void> | null = null;
  // 当前前台回答的流式绘制器：SSE 可能跨进程成批抵达，由它在视觉帧内平滑追赶。
  // stopChat/切段必须先 stop，避免已取消的旧轮继续吐字。
  let activeTypewriter: { stop: () => void } | null = null;
  // commentary 与最终正文是两条独立的视觉流。后端的 message.commentary 是完整权威
  // 事件，前端只在当前页面做渐进绘制；停止/切段时要与正文一起立即冻结。
  let activeCommentaryStream: { stop: () => void } | null = null;
  // provider reasoning 可能在判定工具轮后以一整段补发。单独的视觉流负责把这段真实
  // reasoning 渐进画完；解析器会等它收束后才消费下一条工具事件。
  let activeReasoningStream: { stop: () => void } | null = null;
  // 本轮在飞请求的幂等键（审计项 8/N-02）：run.started 尚未到达就点停止时，凭它反查
  // 服务端是否已建 Run，查到就补一刀 cancel——不留后台幽灵 Run 跑完整轮。
  let activeClientRequestId = '';
  let lastTurnSkills: SkillItem[] = []; // 上一轮实际用过的 skill，供“重新生成”沿用
  let lastTurnSubagentName = '';

  type BubbleAttachment = NonNullable<ChatMessage['attachments']>[number];

  function composerBubbleAttachments(opts?: {
    uploads?: Array<{ filename: string; kind?: string; previewUrl?: string; status?: string; note?: string; file_id?: string }>;
    files?: Array<{ id?: string; filename: string }>;
    threads?: Array<{ id?: string; title?: string }>;
    knowledge?: KnowledgeSelection[];
    skills?: SkillItem[];
    subagent?: { name?: string } | null;
    webSearch?: boolean;
  }): BubbleAttachment[] {
    // 传入 opts 时只采用显式字段。缺省回落到当前 composer 会把 upload 调用和 extraDisplay
    // 拼出两套相同的 Skill/文件卡。
    const live = !opts;
    return buildComposerBubbleAttachments({
      uploads: opts?.uploads ?? (live ? pendingAttachments.value : []),
      files: opts?.files ?? (live ? selectedFileList.value : []),
      threads: opts?.threads ?? (live ? selectedThreadList.value : []),
      knowledge: opts?.knowledge ?? (live ? selectedKnowledgeList.value : []),
      skills: opts?.skills ?? (live ? selectedSkills.value : []),
      subagent: opts && 'subagent' in opts ? opts.subagent : (live ? selectedSubagent.value : null),
      webSearch: opts && 'webSearch' in opts ? Boolean(opts.webSearch) : (live ? webSearchOn.value : false),
    });
  }

  function hasComposerResources(): boolean {
    return Boolean(
      pendingAttachments.value.length
      || selectedFileList.value.length
      || selectedThreadList.value.length
      || selectedKnowledgeList.value.length
      || selectedSkills.value.length,
    );
  }
  // 上一轮随消息发送的上传附件（一次性，发送时 pendingAttachments 已消费清空）：
  // 重新生成/消歧卡点选重发沿用，否则原轮的文档/图片会在重发轮静默丢失
  let lastTurnAttachments: TurnAttachment[] = [];
  // 上一轮是否带过附件（响应式镜像，供降级横幅判断「重试本轮」能否真正重新携带附件——
  // 刷新后本内存副本已失，按钮应换成「重新上传后可重试」提示而非空承诺）
  const lastTurnHadAttachments = ref(false);
  let viewVersion = 0;
  // 单调递增的本地消息 id：避免多条消息在同一毫秒用 Date.now() 生成相同 id，导致按 id 更新时命中错条
  let localIdSeq = 0;
  const nextLocalId = () => (localIdSeq += 1);
  const activeRuns = ref<Record<string, ActiveRun>>({});
  const currentRunModel = computed(() => (
    currentThreadId.value ? activeRuns.value[currentThreadId.value]?.model || '' : ''
  ));
  const runPlans = ref<Record<string, TaskPlanEvent>>({});
  const runPlanSnapshotLoads = new Set<string>();
  const currentRunPlan = computed(() => {
    const activeId = currentThreadId.value ? activeRuns.value[currentThreadId.value]?.id : '';
    const latestMessageRunId = [...chatMessages.value].reverse().find((item) => item.runId)?.runId || '';
    return runPlans.value[activeId || latestMessageRunId] || null;
  });

  function taskPlanFromSnapshot(snapshot: Record<string, any>): TaskPlanEvent {
    const contract = snapshot.goal_contract && typeof snapshot.goal_contract === 'object'
      ? snapshot.goal_contract
      : undefined;
    const firstStep = Array.isArray(snapshot.steps) ? snapshot.steps[0] : null;
    const approvedRaw = snapshot.approved_version ?? snapshot.approved_plan_version
      ?? (firstStep && typeof firstStep === 'object' ? firstStep.approved_version : undefined);
    return {
      goal_revision: Math.max(0, Number(snapshot.goal_revision) || 0),
      plan_version: Math.max(0, Number(snapshot.plan_version) || 0),
      approved_version: approvedRaw == null || approvedRaw === ''
        ? undefined
        : Math.max(0, Number(approvedRaw) || 0),
      diverged: Boolean(snapshot.diverged || (firstStep && typeof firstStep === 'object' && firstStep.diverged)),
      goal_contract: contract
        ? {
            goal: contract.goal ? String(contract.goal) : undefined,
            deliverable: contract.deliverable ? String(contract.deliverable) : undefined,
            success_criteria: Array.isArray(contract.success_criteria)
              ? contract.success_criteria.map(String)
              : undefined,
            forbidden: Array.isArray(contract.forbidden)
              ? contract.forbidden.map(String)
              : undefined,
            budget_hint: contract.budget_hint ? String(contract.budget_hint) : undefined,
          }
        : undefined,
      steps: (Array.isArray(snapshot.steps) ? snapshot.steps : [])
        .map((step: Record<string, any>, index: number) => ({
          key: String(step.step_id || step.key || `plan-${index}`),
          title: String(step.title || '').trim(),
          status: (String(step.status || '') === 'in_progress'
            ? 'running'
            : ['pending', 'running', 'completed', 'skipped', 'invalidated'].includes(String(step.status || ''))
              ? String(step.status)
              : 'pending') as NonNullable<TaskPlanEvent['steps']>[number]['status'],
          detail: String(step.reason || step.status_reason || step.detail || '').trim() || undefined,
          blocked: Boolean(step.blocked) || String(step.reason || step.detail || '').includes('等待上一步'),
          acceptance: String(
            step.acceptance
            || (Array.isArray(step.acceptance_criteria) ? step.acceptance_criteria[0] : '')
            || '',
          ).trim() || undefined,
        }))
        .filter((step: TaskPlanEvent['steps'][number]) => Boolean(step.title)),
    };
  }

  function applyAuthoritativeRunPlan(runId: string, snapshot: Record<string, any>, target?: ChatMessage) {
    const plan = taskPlanFromSnapshot({
      ...snapshot,
      goal_contract: snapshot.goal_contract,
    });
    const previous = runPlans.value[runId];
    if (previous && Number(previous.plan_version || 0) > Number(plan.plan_version || 0)) return;
    runPlans.value = { ...runPlans.value, [runId]: plan };
    const message = target || [...chatMessages.value].reverse().find(
      (item) => item.role === 'assistant' && item.runId === runId,
    );
    if (message) applyTaskPlan(message, plan);
  }

  async function reloadRunPlanSnapshot(runId: string) {
    if (!runId || runPlanSnapshotLoads.has(runId)) return;
    runPlanSnapshotLoads.add(runId);
    try {
      const state = await getRunState(runId);
      if (state.plan) {
        applyAuthoritativeRunPlan(runId, {
          ...(state.plan as Record<string, any>),
          goal_contract: state.goal_contract,
          approved_plan_version: state.approved_plan_version,
        });
      }
    } catch {
      // 短暂无法重拉时保留已提交的最新整表事件，后续断层会再次触发。
    } finally {
      runPlanSnapshotLoads.delete(runId);
    }
  }

  /** 返回 false 表示重复/乱序/冲突事件不应投影；版本断层会同时重拉快照。 */
  function recordRunPlan(runId: string, plan: TaskPlanEvent): boolean {
    const previous = runPlans.value[runId];
    const previousVersion = Number(previous?.plan_version || 0);
    const nextVersion = Number(plan.plan_version || 0);
    if (previous && previousVersion > nextVersion) return false;
    if (previous && previousVersion === nextVersion && JSON.stringify(previous) === JSON.stringify(plan)) {
      return false;
    }
    if (previous && nextVersion > 0 && previousVersion === nextVersion) {
      void reloadRunPlanSnapshot(runId);
      return false;
    }
    const versionGap = nextVersion > 1 && (!previous || previousVersion <= 0 || nextVersion > previousVersion + 1);
    runPlans.value = { ...runPlans.value, [runId]: plan };
    if (versionGap) void reloadRunPlanSnapshot(runId);
    return true;
  }
  const runControllers = new Map<string, AbortController>();
  // 去重登记按控制器持有所有权（Map 而非 Set）：摘除必须身份比对——切走后晚到的旧订阅
  // finally 不能删掉新活跃订阅的登记（否则交接路径可对同一 Run 双订阅、双写两个气泡）
  const visibleRunSubscriptions = new Map<string, AbortController>();
  // 当前可见会话的订阅流控制器：切走会话时要**单独** abort 它——它不是 abortController（那是
  // 前台发送流）。不清会留下写向幽灵消息的孤儿 SSE 连接，并让切回被去重挡住、界面卡死。
  let visibleSubController: AbortController | null = null;
  let visibleSubRunId = '';
  // 切走/新建对话时中止当前可见订阅流：中止本身不清 activeRun（后台 Run 仍在跑，切回要重订阅），
  // 并从去重集移除，保证切回能重新订阅到一条能写进新消息数组的活订阅。
  function stopVisibleSubscription() {
    const controller = visibleSubController;
    const runId = visibleSubRunId;
    visibleSubController = null;
    visibleSubRunId = '';
    if (!controller) return;
    controller.abort();
    if (runId) {
      if (runControllers.get(runId) === controller) runControllers.delete(runId);
      if (visibleRunSubscriptions.get(runId) === controller) visibleRunSubscriptions.delete(runId);
    }
  }
  // 本会话已结束（完成/停止/出错）的 run id：loadThreads 不再据服务端状态把它们复活成活动态，
  // 兜住后端偶发晚清导致的“主对话导航一直转圈”。有界，超限淘汰最旧。
  const finishedRunIds = new Set<string>();
  function rememberFinishedRun(runId?: string) {
    if (!runId) return;
    finishedRunIds.add(runId);
    if (finishedRunIds.size > 300) {
      const oldest = finishedRunIds.values().next().value;
      if (oldest) finishedRunIds.delete(oldest);
    }
  }

  function isFinishedRun(runId?: string) {
    return Boolean(runId && finishedRunIds.has(runId));
  }

  // 已收游标登记（P0-3）：runId → 已接受 sequence / 已收正文 / 所写消息 id。停止请求失败
  // （旧 Run 仍在服务器跑）时按游标重新订阅、续写原消息——不登记就只能全量回放，
  // tool/subagent 事件会重复灌进时间线、正文重复成第二个气泡。有界，超限淘汰最旧。
  const runStreamCursors = new Map<string, { seq: number; content: string; messageId: number; contentOffset: number }>();
  type RunSegmentController = {
    split: () => number;
  };
  // 同一 Run 的实时渲染目标。用户插话时切换“后续事件写到哪条助手消息”，而不是移动
  // 已经发生的执行记录；这样旧记录 → 用户插话 → 新记录的顺序从一开始就正确。
  const runSegmentControllers = new Map<string, RunSegmentController>();

  /**
   * Plan/HITL 续接前摘除同 Run 的旧前端观察者。abort 只关闭本地 SSE/POST
   * 读取，不会取消后端 Run；随后的 resume 流会从权威游标继续。
   */
  function detachRunObserversBeforeResume(runId: string) {
    if (visibleSubRunId === runId) stopVisibleSubscription();
    const observers = new Set<AbortController>();
    const runController = runControllers.get(runId);
    const visibleController = visibleRunSubscriptions.get(runId);
    if (runController) observers.add(runController);
    if (visibleController) observers.add(visibleController);
    observers.forEach((controller) => controller.abort());
    if (runController && runControllers.get(runId) === runController) {
      runControllers.delete(runId);
    }
    if (visibleController && visibleRunSubscriptions.get(runId) === visibleController) {
      visibleRunSubscriptions.delete(runId);
    }
    if (abortController && observers.has(abortController)) abortController = null;
    runSegmentControllers.delete(runId);
  }

  const inputSegmentIds = new Map<number, number>();
  function trackRunCursor(
    runId: string, messageId: number,
    patch: { seq?: number; content?: string; contentOffset?: number },
  ) {
    if (!runId) return;
    const prev = runStreamCursors.get(runId);
    runStreamCursors.delete(runId); // 重插到尾部（LRU 触碰）
    runStreamCursors.set(runId, {
      seq: patch.seq ?? prev?.seq ?? 0,
      content: patch.content ?? prev?.content ?? '',
      messageId,
      // 插话切段后的正文起点（2026-07-26 并发审计）：游标此前不记它，按游标恢复订阅时
      // segmentContentOffset 归 0、setContent 从 0 切片，于是第一段正文在第二段里又出现
      // 一遍（点「调整方向」会 steer 切段，这条路仍会走）
      contentOffset: patch.contentOffset ?? prev?.contentOffset ?? 0,
    });
    while (runStreamCursors.size > 60) {
      const oldest = runStreamCursors.keys().next().value;
      if (oldest === undefined) break;
      runStreamCursors.delete(oldest);
    }
  }

  // 已发出停止请求的 Run（第三批 P0-2/P1-3）：cancel pending/失败恢复订阅后，pump 收敛
  // 回推的 run.failed("已停止生成") 是「取消收尾」而非真失败——订阅通道据此按「已停止」
  // 视觉定格、不标红。终态收尾（clearActiveRun）时移除。
  const stopRequestedRuns = new Set<string>();

  // 停止流程进行中（第四批 2a）：从发出 cancel 到「服务端确认收敛（含 pending 等 pump
  // 收敛期）/恢复失败解除」为止。UI 据此把停止按钮置禁用「停止中…」，防重复点重复软提示；
  // 终态落定（clearActiveRun）/订阅退出/恢复失败时解除。
  // per-thread（审计项 20）：A 会话停止中不该让 B 会话的按钮也显示「停止中」。
  const stoppingRunId = ref('');
  const stoppingThreadId = ref('');
  const stopping = computed(() =>
    Boolean(stoppingRunId.value)
    && (!stoppingThreadId.value || currentThreadId.value === stoppingThreadId.value));
  function clearStoppingState(runId?: string) {
    if (runId && stoppingRunId.value !== runId) return;
    stoppingRunId.value = '';
    stoppingThreadId.value = '';
  }

  /** 服务端仲裁公共出口（第三批 P0-1）：优先单 Run 权威终态接口 /chat/runs/{id}/state
   *  （能区分 completed/failed/cancelled/waiting_*；running 但进程已死会被后端当场收敛为
   *  failed 并补落 run.failed 帧）。旧后端无此接口（404/异常）或返回 unknown 时退回线程级
   *  活动 Run 判断——running=仍在跑（或状态查不到＝网络不通，保守按仍在跑）；
   *  ended=已终态（outcome=null 表示旧后端只知道「已结束」，拿不到具体终态）。 */
  async function arbitrateRunTerminalState(
    threadId: string,
    runId: string,
  ): Promise<
    | { kind: 'running' }
    | { kind: 'waiting'; status: string }
    | { kind: 'ended'; outcome: 'completed' | 'partial' | 'failed' | 'cancelled' | null; error?: string }
  > {
    try {
      const state = await getRunState(runId);
      const status = String(state?.status || 'unknown');
      if (status === 'completed' || status === 'partial' || status === 'failed' || status === 'cancelled') {
        return { kind: 'ended', outcome: status, error: state.error };
      }
      if (isWaitingForUserStatus(status)) return { kind: 'waiting', status };
      if (isActiveRunStatus(status)) return { kind: 'running' };
      // unknown：接口在但拿不准——落回旧的线程级仲裁
    } catch {
      // 旧后端无此接口（404）/网络异常：退回线程级活动 Run 判断
    }
    let serverRun: ActiveRun | null | undefined;
    try {
      serverRun = await getThreadActiveRun(threadId, threadScope);
    } catch {
      serverRun = undefined;
    }
    if (serverRun === undefined || serverRun?.id === runId) return { kind: 'running' };
    return { kind: 'ended', outcome: null };
  }

  function isActiveRunStatus(status?: string) {
    return ['created', 'running', 'routing', 'waiting_user', 'waiting_confirmation', 'waiting_system'].includes(status || '');
  }

  function isWaitingForUserStatus(status?: string) {
    return status === 'waiting_user' || status === 'waiting_confirmation';
  }

  // 自动恢复仍属于正在执行的任务：保持停止按钮、模式锁定、排队和观察连接。
  // 只有等待用户输入/确认才放开交互卡，不能把 waiting_system 一并当作空闲。
  function isGeneratingRunStatus(status?: string) {
    return ['created', 'running', 'routing', 'waiting_system'].includes(status || '');
  }

  function clearChatTaskTimer() {
    if (chatTaskStateTimer) {
      window.clearTimeout(chatTaskStateTimer);
      chatTaskStateTimer = null;
    }
  }

  function refreshRunUi(completed = false) {
    const anyRunning = Object.values(activeRuns.value).some((run) => isGeneratingRunStatus(run?.status));
    // 新会话（尚未拿到 thread_id）期间本地仍有发送流在飞（abortController 非空＝runAssistantTurn/
    // submitResume 已开跑但还没收到 run.started）：loadThreads 等与本次发送无关的路径调用本函数时
    // 不许把 chatLoading 拍回 false，否则会绕开 sendChat 的打断互斥（P0-6）。
    // 已 abort 的控制器不算「在飞」：stopChat 兜底分支停掉未注册 run 后要能把 loading 收回去，
    // 否则 sendChat 的打断等待循环会对着一个永远为 true 的 chatLoading 空转（P0-4 残余）
    const pendingNewThreadRun = !currentThreadId.value
      && Boolean(abortController && !abortController.signal.aborted);
    // 等用户输入/确认不算 loading（P0 2026-07-17 真机：需求卡整卡被 submitting 禁用「点不了」）：
    // 挂起 Run 留在 activeRuns 只为 submitResume/刷新恢复，但它在**等用户**而非生成中——
    // 算进 loading 会让需求卡/计划卡禁用、composer 打字被转待发送队列，后端「挂起时打字=
    // 取消/自由回答/引导回卡」的整条链路被前端旁路。regenerate/编辑重发在挂起时放行后由
    // 后端 R0 原子占位拒绝（拒绝帧提示），队列派发撞挂起 Run 走租约释放回队列，均有兜底。
    chatLoading.value = Boolean(
      pendingNewThreadRun
      || (currentThreadId.value && isGeneratingRunStatus(activeRuns.value[currentThreadId.value]?.status)),
    );
    clearChatTaskTimer();
    if (anyRunning) {
      chatTaskState.value = 'running';
    } else if (completed) {
      chatTaskState.value = 'completed';
      chatTaskStateTimer = window.setTimeout(() => {
        chatTaskState.value = 'idle';
        chatTaskStateTimer = null;
      }, 4000);
    } else {
      chatTaskState.value = 'idle';
    }
  }

  function setActiveRun(
    threadId: string,
    runId: string,
    status = 'running',
    patch: Partial<ActiveRun> = {},
  ) {
    if (!threadId || !runId) return;
    const previous = activeRuns.value[threadId];
    activeRuns.value = {
      ...activeRuns.value,
      [threadId]: {
        ...(previous?.id === runId ? previous : {}),
        ...patch,
        id: runId,
        status,
      },
    };
    if (currentThreadId.value === threadId) {
      for (const item of chatMessages.value) {
        if (item.role === 'assistant' && item.runId === runId) item.runStatus = status;
      }
    }
    refreshRunUi();
  }

  let modelUpdateSeq = 0;
  async function handleModelChange(model: string) {
    const nextModel = String(model || '').trim();
    if (!nextModel || nextModel === activeModel.value) return;
    const previous = activeModel.value;
    const threadId = currentThreadId.value;
    const seq = ++modelUpdateSeq;
    activeModel.value = nextModel;

    if (!threadId) {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem(DEFAULT_MODEL_STORAGE_KEY, nextModel);
      }
      return;
    }

    const runModel = activeRuns.value[threadId]?.model || '';
    if (runModel && runModel !== nextModel && isGeneratingRunStatus(activeRuns.value[threadId]?.status)) {
      options.showNotice(`当前任务继续使用 ${runModel}；${nextModel} 将从下一轮开始使用`);
    }
    try {
      const resolved = await updateThreadModel(threadId, nextModel, threadScope);
      if (seq !== modelUpdateSeq || currentThreadId.value !== threadId) return;
      activeModel.value = resolved;
      threadList.value = threadList.value.map((item) => (
        item.id === threadId ? { ...item, model: resolved } : item
      ));
    } catch (error) {
      if (seq !== modelUpdateSeq || currentThreadId.value !== threadId) return;
      activeModel.value = previous;
      options.showError(error);
    }
  }

  function settleExecutionForRun(runId: string | undefined, status: 'completed' | 'failed' = 'completed') {
    if (!runId) return;
    settleRunExecutionSteps(chatMessages.value as any, runId, status);
  }

  function forgetThreadListRun(threadId: string, runId?: string) {
    if (!runId) return;
    let changed = false;
    const next = threadList.value.map((item) => {
      if (item.id !== threadId) return item;
      if (!item.active_run || (item.active_run.id && item.active_run.id !== runId)) return item;
      changed = true;
      return { ...item, active_run: null };
    });
    if (changed) threadList.value = next;
  }

  function clearActiveRun(threadId: string, runId?: string, completed = false) {
    if (!threadId) return;
    rememberFinishedRun(runId);
    if (runId) {
      runStreamCursors.delete(runId); // 已终态：游标不再有续订价值
      stopRequestedRuns.delete(runId); // 停止请求标记同步清理
      clearStoppingState(runId); // 停止流程随终态落定解除
    }
    const current = activeRuns.value[threadId];
    // 只在「有明确 runId 且与当前活动 Run 一致」时才清：runId 为空＝本轮从未收到 run.started
    // （如原子占位冲突被拒、或启动前就断流），此时线程上真实运行的 Run 不该被这次未启动的
    // 请求收尾抹掉——否则真实 Run 的运行态与后台订阅会被误清。
    if (!current || !runId || current.id !== runId) {
      forgetThreadListRun(threadId, runId);
      refreshRunUi(completed);
      return;
    }
    const next = { ...activeRuns.value };
    delete next[threadId];
    activeRuns.value = next;
    forgetThreadListRun(threadId, runId);
    refreshRunUi(completed);
  }

  // 终态驱动 Profile 收尾（§A）：Plan 归位；Research 在 completed / partial
  // 交付后自动归位。调用方只在真正终态分支调用，并必须传入真实 outcome。
  function lastUnfinishedResearchRunId(): string | undefined {
    for (let i = chatMessages.value.length - 1; i >= 0; i -= 1) {
      const item = chatMessages.value[i];
      if (item.role !== 'assistant') continue;
      if (item.agentMode !== 'research' || !item.runId) return undefined;
      // partial 是“报告已交付但有非关键证据缺口”，不是可跨轮续跑的中断态。
      if (item.runCancelled || item.runFailed) return item.runId;
      return undefined;
    }
    return undefined;
  }

  function settlePlanProfile(threadId: string, runId?: string) {
    if (!runId || !planProfileRuns.has(runId)) return;
    planProfileRuns.delete(runId);
    if (currentThreadId.value === threadId) planMode.value = false;
  }

  function settleResearchProfile(
    threadId: string,
    runId?: string,
    outcome: RunTerminalOutcome = null,
  ) {
    if (!runId) return;
    const target = chatMessages.value.find(
      (item) => item.runId === runId && item.role === 'assistant',
    );
    const isResearchRun = researchProfileRuns.has(runId) || target?.agentMode === 'research';
    if (!isResearchRun) return;
    researchProfileRuns.delete(runId);
    // completed 与 partial 都已结束本轮并交付报告；partial 只描述非关键证据缺口。
    // cancelled / failed 保留开关便于用户直接继续或重试；waiting_* 不会进入本函数。
    if (!researchRunDelivered(outcome)) return;
    if (currentThreadId.value === threadId) researchProfile.value = false;
  }

  /** 后端 auto 路由进入任务模式时，前端发送前并不知道 agent_mode。首个 task.* 事件
   *  到达即登记同一个 Run，让暂停、恢复和终态归位与手动任务模式完全一致。 */
  function registerPlanProfileRun(threadId: string, runId: string) {
    if (getBuiltinUiPolicy(assistantPreset.value)?.hidePlanMode) return;
    if (!runId || unlockedPlanRuns.has(runId)) return;
    if (chatMessages.value.some((item) => item.runId === runId && item.planExecutionUnlocked)) {
      unlockedPlanRuns.add(runId);
      return;
    }
    planProfileRuns.add(runId);
    if (threadId && currentThreadId.value === threadId) planMode.value = true;
  }

  function registerResearchProfileRun(threadId: string, runId: string) {
    if (getBuiltinUiPolicy(assistantPreset.value)?.hideResearch) return;
    if (!runId) return;
    researchProfileRuns.add(runId);
    if (threadId && currentThreadId.value === threadId) researchProfile.value = true;
  }

  // 运行中队列派发（§B）：Run 到达真正终态后，若队列非空则原子取队首、作为新一轮发送。
  // 只在"该会话仍是当前可见会话"时派发——不把已派发消息写进用户此刻正看着的另一个会话。
  async function dispatchNextQueuedItem(threadId: string, resumeSourceRunId?: string) {
    if (!threadId || currentThreadId.value !== threadId) return;
    if (chatLoading.value || !messageQueue.value.length) return;
    // 用户刚喊过停：不自动接着发下一条（见 queuePaused 说明；per-thread，审计项 21）
    if (queuePausedThreads.value.has(threadId)) return;
    queueBusy.value = true;
    let item: ChatQueueItem | null = null;
    // 看门狗而非 Promise.race：race 会在**正常路径**上多插两个微任务，足以打乱既有的
    // 派发时序（真的把 4 个既有用例改红过）。看门狗只在真挂住时才动手。
    const watchdog = startQueuePopWatchdog(threadId);
    try {
      item = await popChatQueue(threadId, threadScope);
      if (watchdog.tripped) return; // 已超时判失败并解锁，迟到的结果不再继续派发
      failedQueueItemId.value = '';
    } catch (error) {
      if (watchdog.tripped) return;
      // 派发失败必须让用户看见：静默 return 的表现是「队列卡着不动」，用户无从判断
      // 是还没轮到、还是出错了。toast 会飘走，所以把失败标在**具体那一条**上（队首，
      // 即这次没能取出来的那条），由它自己出警告图标与「重试」——对齐 Codex 的
      // isMessagePaused(id) 粒度。
      console.warn('Failed to pop message queue:', error);
      failedQueueItemId.value = messageQueue.value[0]?.id || '';
      options.showNotice('待发送队列派发失败，可在这条消息上点「重试」');
      return;
    } finally {
      watchdog.cancel();
      queueBusy.value = false;
    }
    if (!item) {
      // pop 返回空 ≠ 队列一定为空（可能有活动 Run/派发中项）：重新拉取服务端真实队列，
      // 不再武断清空本地视图（被释放回队列的项要能继续显示）。
      void loadMessageQueue(threadId);
      return;
    }
    // 派发窗口内用户已手动发送或切走会话：该项在服务端仍是 dispatching，
    // 90s 租约超时后自动回收为 queued（position 不变，顺序保留），下次终态再派发，不丢不重（§3）。
    if (currentThreadId.value !== threadId || chatLoading.value) {
      // 本地不能先把卡片藏掉：服务端尚未确认本轮 Run，租约回收后它仍会排队发送。
      void loadMessageQueue(threadId);
      return;
    }
    messageQueue.value = messageQueue.value.filter((q) => q.id !== item!.id);
    // §10.6 P0：不再提前 confirm 删除——派发请求携带 queue_item_id + leaseToken，
    // 由服务端在 Run 成功持久化后原子绑定并确认；confirm 后建 Run 失败/断网导致
    // 消息永久丢失的窗口从此关闭。旧后端（pop 未下发 leaseToken）走原 confirm 回退。
    if (!item.leaseToken) {
      try {
        await confirmChatQueue(item.id);
      } catch (error) {
        console.warn('Failed to confirm dispatched item:', error);
      }
    }
    const atts: TurnAttachment[] | undefined = item.attachments?.length
      ? item.attachments.map((a) => ({
          filename: a.filename,
          text: a.text || '',
          file_id: a.file_id,
          sha256: a.sha256,
          kind: a.kind,
          image_url: a.image_url,
          preview_url: a.preview_url,
          status: a.status,
          note: a.note,
        }))
      : undefined;
    const presentationQueue = isRestrictedAssistantPreset(assistantPreset.value);
    const displayAtts = composerBubbleAttachments({
      uploads: (item.attachments || []).map((a) => ({
        filename: a.filename,
        kind: a.kind,
        previewUrl: a.image_url || a.preview_url,
        status: a.status,
        note: a.note,
        file_id: a.file_id,
      })),
      files: item.context?.files,
      threads: item.context?.threads,
      knowledge: item.context?.knowledge,
      skills: presentationQueue ? [] : item.context?.skills,
      subagent: presentationQueue ? null : item.context?.subagent,
      webSearch: Boolean(item.context?.webSearch),
    });
    const queuedUserMessage: ChatMessage = {
      id: nextLocalId(), role: 'user', content: item.content,
      attachments: displayAtts.length ? displayAtts : undefined,
      turnSkills: presentationQueue ? [] : [...(item.context?.skills || [])],
    };
    chatMessages.value.push(queuedUserMessage);
    const accepted = await runAssistantTurn(item.content, {
      resumeSourceRunId,
      attachments: atts,
      // TurnContext 快照（审计项 1）：任务模式与 Skill/KB/文件/模型都以入队时刻为准；
      // 无快照（旧后端/旧队列项）退回当前全局选择
      // context 存在就以快照为准；false 也是有意义的值。只有旧队列项完全没有 context
      // 才退回当前开关，避免“入队时普通对话、派发前开了任务模式”发生语义漂移。
      planMode: getBuiltinUiPolicy(assistantPreset.value)?.hidePlanMode
        ? false
        : (item.context ? Boolean(item.context.planMode) : planMode.value),
      researchProfile: getBuiltinUiPolicy(assistantPreset.value)?.hideResearch
        ? false
        : (item.context
          ? Boolean(item.context.researchProfile)
          : (researchProfile.value || Boolean(lastUnfinishedResearchRunId()))),
      subagentId: presentationQueue ? undefined : item.context?.subagent?.id,
      subagentName: presentationQueue ? undefined : item.context?.subagent?.name,
      queueDispatch: item.leaseToken ? { itemId: item.id, leaseToken: item.leaseToken } : undefined,
      userMessageLocalId: queuedUserMessage.id,
      contextOverride: item.context
        ? {
            skills: item.context.skills,
            knowledge: item.context.knowledge,
            files: item.context.files,
            // 「最近的对话」引用（2026-07-29 深扫 P0）：enqueueMessage 早就把它存进了
            // 队列项快照，但这里既没透传、contextOverride 类型里也没这个字段——
            // runAssistantTurn 见 ctxOverride 非空即取 ctxOverride.threads，于是恒为
            // 空数组：排队发出的那一轮**必然丢掉**用户选中的会话引用，气泡上也不显示。
            threads: item.context.threads,
            webSearch: item.context.webSearch,
            model: item.context.model,
          }
        : undefined,
    });
    if (!accepted) {
      // 租约冲突时服务端会把该项放回队列；本地这条只是乐观气泡，必须撤掉。
      chatMessages.value = chatMessages.value.filter((message) => message.id !== queuedUserMessage.id || !!message.dbId);
      void loadMessageQueue(threadId);
    }
  }

  // Run 抵达真正终态（非挂起、非断流交接）时统一收尾：
  // Plan 归位、Research 按真实终态决定是否退出，然后派发下一条排队消息。
  function onRunSettled(
    threadId?: string,
    runId?: string,
    outcome: RunTerminalOutcome = null,
  ) {
    if (!threadId) return;
    settlePlanProfile(threadId, runId);
    settleResearchProfile(threadId, runId, outcome);
    void reconcileInstructions(runId);
    void dispatchNextQueuedItem(threadId, runId);
  }

  /** Run 收尾时只清理本地回执账本。是否完成由 Run 终态与 Completion Verifier 决定，
   *  前端不再读取第二份指令列表去推断。 */
  async function reconcileInstructions(runId?: string) {
    void runId;
    pendingInstructionIds.clear();
    appliedInstructionIds.clear();
  }

  async function loadMessageQueue(threadId: string) {
    if (!threadId) {
      messageQueue.value = [];
      return;
    }
    // 暂停态 per-thread（审计项 21）：切会话不复位别的会话的暂停，也不泄漏到本会话
    queueLoading.value = true;
    try {
      const items = await getChatQueue(threadId, threadScope);
      if (currentThreadId.value === threadId) messageQueue.value = items;
    } catch (error) {
      // 拉取失败不再清空本地队列：服务端可能仍有排队项，清空后本端此后就再也不派发了
      // （用户看到队列凭空消失，消息却还在服务端）。保留现有视图并如实提示。
      console.warn('Failed to load message queue:', error);
      if (currentThreadId.value === threadId && messageQueue.value.length) {
        options.showNotice('待发送队列刷新失败，显示的可能不是最新状态');
      }
    } finally {
      queueLoading.value = false;
    }
  }

  /** 运行中再发送的默认去向：追加到服务端队列尾部（§B）。409（队列已满）等错误直接提示。 */
  async function enqueueMessage(content: string, atts: UploadedFile[]): Promise<boolean> {
    const threadId = currentThreadId.value;
    if (!threadId) {
      options.showNotice('新会话建立后才能排队，请稍候片刻再试');
      return false;
    }
    // 重入保护：入队是「先 await 网络、成功后才清输入框」，连按两次回车会在第一次的
    // await 里撞进第二次，同一条内容入队两遍。其余队列操作都有这道门，只有它漏了。
    if (queueBusy.value) {
      options.showNotice('待发送队列正在处理，请稍后再试');
      return false;
    }
    queueBusy.value = true;
    const turnAtts: ChatQueueAttachment[] = atts.map((a) => ({
      filename: a.filename,
      text: a.text,
      file_id: a.file_id,
      sha256: a.sha256,
      kind: a.kind,
      image_url: a.previewUrl,
      preview_url: a.thumbUrl,
      status: a.status,
      note: a.note || '',
    }));
    // TurnContext 快照（审计项 1）：把入队时刻的选择随消息存进服务端。派发时按快照执行，
    // 排队期间改选 Skill/知识库/文件/模型不影响已排队的消息；队列卡也能核对绑定的上下文。
    const presentationQueue = isRestrictedAssistantPreset(assistantPreset.value);
    const context = {
      skills: !presentationQueue && selectedSkills.value.length ? [...selectedSkills.value] : undefined,
      subagent: !presentationQueue && selectedSubagent.value ? { ...selectedSubagent.value } : undefined,
      knowledge: selectedKnowledgeList.value.length ? [...selectedKnowledgeList.value] : undefined,
      files: selectedFileList.value.length
        ? selectedFileList.value.map((f) => ({ id: f.id, filename: f.filename }))
        : undefined,
      threads: selectedThreadList.value.length
        ? selectedThreadList.value.map((t) => ({ id: t.id, title: t.title }))
        : undefined,
      webSearch: webSearchOn.value,
      model: activeModel.value || undefined,
      planMode: planMode.value,
      researchProfile: researchProfile.value,
    };
    const hasContext = Boolean(
      context.skills || context.subagent || context.knowledge || context.files || context.threads || context.model
      // 布尔 false 也必须落库；新队列项始终携带完整上下文快照。
      || typeof context.webSearch === 'boolean' || typeof context.planMode === 'boolean'
      || typeof context.researchProfile === 'boolean',
    );
    try {
      const item = await addChatQueueItem(
        threadId, content, turnAtts.length ? turnAtts : undefined,
        hasContext ? context : undefined,
        threadScope,
      );
      if (currentThreadId.value === threadId) messageQueue.value = [...messageQueue.value, item];
      return true;
    } catch (error) {
      options.showError(error);
      return false;
    } finally {
      queueBusy.value = false;
    }
  }

  /** 运行中发送默认入队：把当前 composer 快照放进队列，当前 Run 完成后再新起一轮。
   *  「调整方向」才把排队项注入正在跑的这一轮。 */
  async function queueCurrentMessage() {
    const content = chatInput.value.trim();
    const atts = [...pendingAttachments.value];
    if (!content && !atts.length && !hasComposerResources()) return;
    if (queueBusy.value) {
      options.showNotice('待发送队列正在处理，请稍后再试');
      return;
    }
    const queued = await enqueueMessage(content, atts);
    if (!queued) return;
    chatInput.value = '';
    pendingAttachments.value = [];
    selectedSkills.value = [];
    selectedSubagent.value = undefined;
    selectedThreadList.value = [];
    selectedFileList.value = [];
    selectedKnowledgeList.value = [];
    webSearchOn.value = false;
    syncUploadingFlag();
    options.showNotice('已加入下一轮，当前任务完成后自动发送');
  }

  function startQueueEdit(item: ChatQueueItem) {
    // 派发中的项不可编辑（审计项 10）：它可能已绑定 Run 正在发出，改了也不生效，
    // 还会与服务端删除竞态
    if (!canInstructQueueItem(item.status)) {
      options.showNotice('这条消息正在派发中，稍候片刻');
      return;
    }
    editingQueueId.value = item.id;
    editingQueueText.value = item.content;
  }

  function cancelQueueEdit() {
    editingQueueId.value = '';
    editingQueueText.value = '';
  }

  async function commitQueueEdit() {
    const id = editingQueueId.value;
    const clean = editingQueueText.value.trim();
    if (!id || !clean || queueBusy.value) return;
    queueBusy.value = true;
    try {
      await updateChatQueueItem(id, clean);
      const idx = messageQueue.value.findIndex((q) => q.id === id);
      if (idx !== -1) {
        const next = [...messageQueue.value];
        next[idx] = { ...next[idx], content: clean };
        messageQueue.value = next;
      }
      editingQueueId.value = '';
      editingQueueText.value = '';
    } catch (error) {
      options.showError(error);
    } finally {
      queueBusy.value = false;
    }
  }

  async function removeQueueItem(id: string) {
    if (!id || queueBusy.value) return;
    // 派发中的项不可删除（审计项 10）：可能已绑定 Run 正在发出，删除会与服务端确认竞态
    const target = messageQueue.value.find((q) => q.id === id);
    if (target?.status === 'dispatching') {
      options.showNotice('这条消息正在派发中，稍候片刻');
      return;
    }
    queueBusy.value = true;
    try {
      await deleteChatQueueItem(id);
      messageQueue.value = messageQueue.value.filter((q) => q.id !== id);
      if (editingQueueId.value === id) cancelQueueEdit();
    } catch (error) {
      options.showError(error);
    } finally {
      queueBusy.value = false;
    }
  }

  /** 移回输入框：删除队列项后把内容塞回 composer；附件按已解析文本重建（无需重新上传）。 */
  async function moveQueueItemToInput(id: string) {
    const originThreadId = currentThreadId.value;
    const item = messageQueue.value.find((q) => q.id === id);
    if (!item || queueBusy.value) return;
    // 派发中的项不可移回（审计项 10）：同编辑/删除口径
    if (item.status === 'dispatching') {
      options.showNotice('这条消息正在派发中，稍候片刻');
      return;
    }
    queueBusy.value = true;
    try {
      await deleteChatQueueItem(id);
      const rebuiltAtts: UploadedFile[] = (item.attachments || []).map((a) => ({
        uid: nextLocalId(),
        filename: a.filename,
        kind: a.kind || 'file',
        file_id: a.file_id,
        sha256: a.sha256,
        text: a.text || '',
        chars: (a.text || '').length,
        truncated: false,
        status: (a.status === 'partial' || a.status === 'failed' ? a.status : 'ok') as 'ok' | 'partial' | 'failed',
        note: a.note,
        previewUrl: a.image_url,
        thumbUrl: a.preview_url,
      }));
      // 归属校验（审计项 19）：删除等待期间用户可能已切会话——A 的消息写回 A 的草稿，
      // 绝不注入此刻正看着的 B 会话输入框
      if (currentThreadId.value !== originThreadId) {
        const draft = conversationDrafts.get(originThreadId || '') || { input: '', attachments: [] };
        draft.input = draft.input.trim()
          ? `${draft.input.replace(/\s+$/, '')}\n${item.content}`
          : item.content;
        draft.attachments = [...draft.attachments, ...rebuiltAtts];
        conversationDrafts.set(originThreadId || '', draft);
        return;
      }
      messageQueue.value = messageQueue.value.filter((q) => q.id !== id);
      // 追加而非覆盖：直接赋值会把用户正在打的草稿吞掉（附件本来就是 append，
      // 正文却是覆盖，两者语义不一致）。
      chatInput.value = chatInput.value.trim()
        ? `${chatInput.value.replace(/\s+$/, '')}\n${item.content}`
        : item.content;
      if (rebuiltAtts.length) {
        pendingAttachments.value = [...pendingAttachments.value, ...rebuiltAtts];
        syncUploadingFlag();
      }
      if (editingQueueId.value === id) cancelQueueEdit();
    } catch (error) {
      options.showError(error);
    } finally {
      queueBusy.value = false;
    }
  }

  /** 拖动排序：先乐观更新本地顺序，服务端失败时恢复，避免拖完后列表闪回旧位置。 */
  async function reorderQueueItems(orderedIds: string[]) {
    const threadId = currentThreadId.value;
    if (!threadId || queueBusy.value || orderedIds.length !== messageQueue.value.length) return;
    const current = [...messageQueue.value];
    const itemById = new Map(current.map((item) => [item.id, item]));
    const reordered = orderedIds.map((id) => itemById.get(id));
    if (reordered.some((item) => !item)) return;
    if (orderedIds.every((id, index) => id === current[index]?.id)) return;

    messageQueue.value = reordered as ChatQueueItem[];
    queueBusy.value = true;
    try {
      await reorderChatQueue(threadId, orderedIds, threadScope);
    } catch (error) {
      if (currentThreadId.value === threadId) messageQueue.value = current;
      options.showError(error);
    } finally {
      queueBusy.value = false;
    }
  }

  /** 把“运行中追加要求”插进当前对话流。
   *  执行流由 Run 分段控制器在该位置切段，不能再移动整条助手消息；否则插话前已经发生
   *  的工具记录也会被挪到用户消息下方，刷新前后时间顺序不一致。 */
  function insertActiveRunInstruction(
    runId: string,
    content: string,
    attachments: ChatQueueAttachment[] | undefined,
    dbId?: number,
    pendingSubmission = false,
    extraDisplay?: BubbleAttachment[],
  ): number {
    const displayAttachments = composerBubbleAttachments({
      uploads: (attachments || []).map((a) => ({
        filename: a.filename,
        kind: a.kind,
        previewUrl: a.image_url || a.preview_url,
        status: a.status,
        note: a.note,
        file_id: a.file_id,
      })),
    }).concat(extraDisplay || []);
    const userMessage: ChatMessage = {
      id: nextLocalId(),
      dbId,
      role: 'user',
      content,
      runId,
      attachments: displayAttachments.length ? displayAttachments : undefined,
    };
    if (dbId && displayAttachments.length) cacheAttachmentPreviews(dbId, displayAttachments);

    chatMessages.value.push(userMessage);
    const segmentId = runSegmentControllers.get(runId)?.split();
    if (segmentId) {
      // 只登记分段归属；成功路径**不写任何回执文案**（Codex 实证 2026-07-26：引导注入后
      // 没有任何 UI 回执，确认由模型下一段叙事/最终回答的对账完成）。失败路径才写，
      // 见 submitActiveRunInput 的 setSegmentPreamble。pendingSubmission 保留形参是为了
      // 让调用方语义可读——乐观插入与已确认插入在失败回滚时的处理并不相同。
      inputSegmentIds.set(userMessage.id, segmentId);
      void pendingSubmission;
    }
    return userMessage.id;
  }

  /** 乐观插入被服务端受理后，补齐历史消息主键，供附件预览缓存和后续操作使用。 */
  function confirmActiveRunInstruction(messageId: number, dbId?: number, inputId?: string) {
    const index = chatMessages.value.findIndex((message) => message.id === messageId);
    if (index < 0) return;
    const current = chatMessages.value[index];
    chatMessages.value[index] = { ...current, ...(dbId ? { dbId } : {}) };
    if (dbId && current.attachments?.length) cacheAttachmentPreviews(dbId, current.attachments);
    const segmentId = inputSegmentIds.get(messageId);
    if (segmentId) {
      const segment = chatMessages.value.find((message) => message.id === segmentId);
      // 只补 inputId（回放配对与 rejected 定位要用），不写回执文案
      if (segment && inputId) segment.executionSegmentInputId = inputId;
      inputSegmentIds.delete(messageId);
    }
  }

  /** 直接引导：把排队项送入当前 Run，成功后再移出队列；当前输入框草稿不受影响。 */
  async function instructQueueItem(id: string) {
    const threadId = currentThreadId.value;
    const active = threadId ? activeRuns.value[threadId] : undefined;
    const item = messageQueue.value.find((entry) => entry.id === id);
    if (!threadId || !active?.id || !item || queueBusy.value || submittingRunInput.value) return;
    // 派发中的项不可再作为引导发出（审计项 10）：它可能已绑定 Run 正在发出，双发必重复
    if (item.status === 'dispatching') {
      options.showNotice('这条消息正在派发中，稍候片刻');
      return;
    }
    if (!isGeneratingRunStatus(active.status)) {
      options.showNotice('当前任务已不在运行，消息将继续留在待发送队列');
      return;
    }

    queueBusy.value = true;
    submittingRunInput.value = true;
    try {
      const accepted = await submitChatRunInput(active.id, {
        content: item.content,
        threadId,
        clientInputId: newClientRequestId(),
        expectedRunId: active.id,
        attachments: item.attachments?.length ? item.attachments : undefined,
      });
      if (accepted?.inputId) pendingInstructionIds.add(accepted.inputId);
      const localMessageId = insertActiveRunInstruction(
        active.id,
        item.content,
        item.attachments?.length ? item.attachments : undefined,
        accepted.messageId,
      );
      confirmActiveRunInstruction(localMessageId, accepted.messageId, accepted.inputId);
      // 引导已受理，这条就不能再留在队列里（否则本轮结束后会被再发一次）。删除失败自动
      // 重试一次；仍失败才本地摘掉并如实告知——把「请手动删除以免重复发送」直接甩给用户
      // 是最差的兜底。
      let removed = false;
      for (let attempt = 0; attempt < 2 && !removed; attempt += 1) {
        try {
          await deleteChatQueueItem(id);
          removed = true;
        } catch (error) {
          console.warn('Failed to remove instructed queue item:', error);
        }
      }
      messageQueue.value = messageQueue.value.filter((entry) => entry.id !== id);
      if (editingQueueId.value === id) cancelQueueEdit();
      if (!removed) {
        options.showNotice('该条未能从服务端队列移除，本轮结束后可能重复发送一次');
      }
    } catch (error) {
      // 409＝本轮已经结束（前端的「生成中」还没落幕，后端 Run 已收尾——打字机长尾里
      // 常见）。这不是错误：该条仍在队列里，本轮落幕后会自动作为下一轮发出。
      // 弹红色报错会让用户以为消息丢了。
      if ((error as { status?: number })?.status === 409) {
        options.showNotice('当前回答已结束，这条将在下一轮自动发送');
      } else {
        options.showError(error);
      }
    } finally {
      queueBusy.value = false;
      submittingRunInput.value = false;
    }
  }

  function togglePlanProfile() {
    if (getBuiltinUiPolicy(assistantPreset.value)?.hidePlanMode) return;
    planMode.value = !planMode.value;
    if (planMode.value) researchProfile.value = false;
  }

  function toggleResearchProfile() {
    if (getBuiltinUiPolicy(assistantPreset.value)?.hideResearch) return;
    researchProfile.value = !researchProfile.value;
    if (researchProfile.value) planMode.value = false;
  }

  /** 立即引导（C）：运行中或已暂停时把消息作为引导发给当前 Run——不取消 Run、不进队列，
   *  与「停止」严格分开。暂停期间的要求在恢复后应用。 */
  const canInstruct = computed(() => {
    const threadId = currentThreadId.value;
    const active = threadId ? activeRuns.value[threadId] : undefined;
    // 两种模式都有消费方，故不再限任务模式（旧行为：普通对话下恒 false，导致每张队列卡
    // 都挂着一个永远点不动的「引导」按钮）：
    //  · 普通对话 → main_agent.stream_tool_loop 在工具轮边界注入正在跑的这一轮（不中断）
    //  · 任务模式 → orchestrator 在整图收尾时并入重规划
    return Boolean(
      threadId
      && active?.id
      && isGeneratingRunStatus(active.status),
    );
  });

  async function submitActiveRunInput() {
    const threadId = currentThreadId.value;
    const active = threadId ? activeRuns.value[threadId] : undefined;
    const content = chatInput.value.trim();
    const sourceAttachments = [...pendingAttachments.value];
    const inputContext = {
      skills: selectedSkills.value.length ? [...selectedSkills.value] : undefined,
      knowledge: selectedKnowledgeList.value.length ? [...selectedKnowledgeList.value] : undefined,
      files: selectedFileList.value.length ? [...selectedFileList.value] : undefined,
      webSearch: webSearchOn.value || undefined,
    };
    // 「最近的对话」引用刻意**不**随插话消费（2026-07-28）：运行中的这一轮上下文与工具集在
    // 开工时就构建完了，一份几千 token 的历史转录没有任何通道能塞进去（Run input 只走
    // _format_run_input 的一行文字）。只报个「新增引用会话《X》」而正文进不去，模型会
    // 照着标题编。所以选择留在 composer 不清空，下一轮真正发送时才生效。
    // 仅附件引导放行（审计项 25）：后端本就接受 attachments-only 的引导，前端不再强制文字
    if (!threadId || !active?.id || (!content && !sourceAttachments.length) || submittingRunInput.value) return;
    submittingRunInput.value = true;
    const atts: ChatQueueAttachment[] = sourceAttachments.map((a) => ({
      filename: a.filename,
      text: a.text,
      file_id: a.file_id,
      sha256: a.sha256,
      kind: a.kind,
      image_url: a.previewUrl,
      preview_url: a.thumbUrl,
      status: a.status,
      note: a.note || '',
    }));
    // Codex 式即时反馈：先把消息插入对话流并清空 composer，不等待网络往返。
    // 服务端拒绝时再移除气泡并恢复草稿，保证既“立刻可见”也不吞用户输入。
    const extraDisplay = composerBubbleAttachments({
      files: selectedFileList.value,
      knowledge: selectedKnowledgeList.value,
      skills: selectedSkills.value,
      subagent: selectedSubagent.value,
      webSearch: webSearchOn.value,
    });
    const persistAtts: ChatQueueAttachment[] = [
      ...atts,
      ...extraDisplay
        .filter((item) => isComposerReferenceKind(item.kind))
        .map((item) => ({ filename: item.filename, kind: item.kind, text: '' })),
    ];
    const localMessageId = insertActiveRunInstruction(
      active.id,
      content,
      atts.length ? atts : undefined,
      undefined,
      true,
      extraDisplay,
    );
    chatInput.value = '';
    pendingAttachments.value = [];
    selectedSkills.value = [];
    selectedFileList.value = [];
    selectedKnowledgeList.value = [];
    selectedThreadList.value = [];
    webSearchOn.value = false;
    syncUploadingFlag();
    try {
      const clientInputId = newClientRequestId();
      const accepted = await submitChatRunInput(active.id, {
        content,
        threadId,
        clientInputId,
        expectedRunId: active.id,
        attachments: persistAtts.length ? persistAtts : undefined,
        context: inputContext,
      });
      if (accepted?.inputId) pendingInstructionIds.add(accepted.inputId);
      confirmActiveRunInstruction(localMessageId, accepted.messageId, accepted.inputId);
    } catch (error) {
      chatMessages.value = chatMessages.value.filter((message) => message.id !== localMessageId);
      const segmentId = inputSegmentIds.get(localMessageId);
      inputSegmentIds.delete(localMessageId);
      // 分段占位（此前被写成「正在提交到当前任务…」）的收尾文案必须等分支定下来再写：
      // 409 重新入队成功时「原任务仍在继续」与事实相反（后端 Run 已收尾），而那句话会
      // 永久留在对话历史里。按 id 现取而非提前捕获引用——中途可能有整体替换消息对象的写法。
      const setSegmentPreamble = (text: string) => {
        if (!segmentId) return;
        const segment = chatMessages.value.find((message) => message.id === segmentId);
        if (segment) segment.preamble = text;
      };
      selectedSkills.value = inputContext.skills || [];
      selectedFileList.value = limitFileSelection(inputContext.files);
      selectedKnowledgeList.value = inputContext.knowledge || [];
      webSearchOn.value = Boolean(inputContext.webSearch);
      // 409＝后端 Run 已收尾，只是前端还没落幕（打字机长尾窗口）。此时把消息退回队列，
      // 本轮落幕后自动作为下一轮发出——比把草稿塞回输入框再弹红报错好得多：
      // 用户的原意是「让它接着做」，而不是「什么都没发生，你自己再按一次」。
      const status = (error as { status?: number })?.status;
      const runEnded = status === 409;
      // 409 或引导失败：优先排队，避免「服务暂时不可用」假故障（真机连发踩中）
      {
        const queued = await enqueueMessage(content, sourceAttachments);
        if (queued) {
          selectedSkills.value = [];
          selectedFileList.value = [];
          selectedKnowledgeList.value = [];
          setSegmentPreamble(
            runEnded
              ? '当前回答已结束，这条已排队，将在下一轮自动发送。'
              : '引导未立刻生效，这条已排队，当前任务结束后自动发送。',
          );
          options.showNotice(
            runEnded
              ? '当前回答已结束，这条已排队，将在下一轮自动发送'
              : '已加入排队，当前任务完成后自动发送',
          );
          return;
        }
      }
      // 连入队也失败时
      setSegmentPreamble(runEnded
        ? '当前回答已结束，这条追加要求未送达。'
        : '追加要求未送达，原任务仍在继续。');
      if (currentThreadId.value === threadId) {
        chatInput.value = chatInput.value.trim() ? `${content}\n${chatInput.value}` : content;
        pendingAttachments.value = [...sourceAttachments, ...pendingAttachments.value];
        syncUploadingFlag();
      } else {
        conversationDrafts.set(threadId, { input: content, attachments: sourceAttachments });
      }
      options.showError(error);
    } finally {
      submittingRunInput.value = false;
    }
  }

  const chatPlaceholder = computed(() => {
    const builtinPlaceholder = getBuiltinUiPolicy(assistantPreset.value)?.composerPlaceholder;
    if (builtinPlaceholder) return builtinPlaceholder;
    const kbCount = selectedKnowledgeList.value.length;
    const fileCount = selectedFileList.value.length;
    const threadCount = selectedThreadList.value.length;
    // 2026-07-28：选中项收进 + 菜单后，选了什么由输入框内的行内标签逐个列出，占位文案
    // 不再重复文件名/知识库名——标签区最宽占 60%，留给占位文字的一列很窄，长句会折行
    // 而 textarea 只有一行高（高度按真实内容算，占位不计），第二行会被直接裁掉。
    // 会话引用优先播报：它是一次性的，用户最需要在按发送前确认"这轮真的会带上"
    if (threadCount && !kbCount && !fileCount) {
      return threadCount === 1 ? '结合选中的对话提问…' : `结合选中的 ${threadCount} 个对话提问…`;
    }
    // 同时选了知识库 + 文件：合并提示
    if (kbCount && fileCount) return '结合所选知识库与文件提问…';
    if (kbCount) return kbCount === 1 ? '结合所选知识库提问…' : `结合所选 ${kbCount} 个知识库提问…`;
    if (fileCount) return fileCount === 1 ? '就选中的文件提问，也可让我直接改…' : `就选中的 ${fileCount} 个文件提问…`;
    const selectedSkill = selectedSkills.value[0];
    if (selectedSkill) return `使用「${selectedSkill.name}」开始提问...`;
    return chatMessages.value.length ? '继续提问...' : '输入你的问题...';
  });

  /**
   * 插话切段正文切点 / 封存判断 / 工具终态回落：实现见 `./runSegmentState`
   * （P0 架构收口——单一权威规则，发送/resume/订阅三路径共用）。
   */
  function updateChatMessageContent(messageId: number, content: string) {
    const index = chatMessages.value.findIndex((item) => item.id === messageId);
    if (index === -1) return;
    // 封存段只读：插话后旧段若再被 delta/打字机写回，就会与新段各显一份相同正文
    if (isSealedAssistantSegment(chatMessages.value[index])) return;
    chatMessages.value[index] = {
      ...chatMessages.value[index],
      content,
    };
  }

  /**
   * 最终回答与子智能体共用同一套 60fps 视觉节奏器；这里只绑定当前消息写入。
   */
  function createTypewriter(messageId: number) {
    return createSmoothStreamText({
      initialContent: chatMessages.value.find((item) => item.id === messageId)?.content || '',
      shouldRenderImmediately: () => isResearchTurn(chatMessages.value.find((item) => item.id === messageId)),
      commit: (content) => updateChatMessageContent(messageId, content),
    });
  }

  /**
   * 工具轮中的文字会先以 delta 抵达，随后才收到 message.commentary 的权威归属。
   * 这段短窗口若先写进最终回答，就会黑字闪现后又被剥走。已有真实动作时先缓冲
   * 短过程句，commentary 到达后插入灰色时间线；没有 commentary 的剩余正文在终态
   * 由 typewriter.finish 写成黑色终答。纯问答没有动作，仍从首字即时流式显示。
   *
   * 缓冲必须有上限：搜索结束后模型常直接写计划报告/终答（数千 token），
   * message.completed 要等整轮结束才到。若一直不画，计量停在几十 token、
   * 状态钉在「正在根据检索结果推进」，看起来像卡死；刷新才从落库/回放看见
   * 已经在执行。超过短窗口就当场流式画出。
   */
  const PROCESS_NARRATION_BUFFER_CHARS = 240;
  function shouldBufferUnclassifiedProcessNarration(messageId: number, incomingContent = ''): boolean {
    const message = chatMessages.value.find((item) => item.id === messageId);
    const acted = Boolean(
      message?.agentSteps?.some((step) => step.kind === 'tool' || step.kind === 'subagent'),
    );
    if (!acted) return false;
    const pending = String(incomingContent || '').trim();
    return pending.length < PROCESS_NARRATION_BUFFER_CHARS;
  }

  type StreamPainter = {
    push: (s: string) => void;
    reset: (s: string) => void;
    finalize: (s: string) => void;
  };

  function paintAssistantStreamText(
    updater: (fn: (target: ChatMessage) => void) => void,
    tw: StreamPainter,
    sliced: string,
    phase: 'delta' | 'complete',
    messageId: number,
  ) {
    let captured = false;
    let holdOffBody = false;
    updater((target) => {
      holdOffBody = shouldHoldPlanStreamOffBody(target);
      captured = ingestPlanReportText(target, sliced);
    });
    if (captured || holdOffBody) {
      if (phase === 'complete') tw.finalize('');
      else tw.reset('');
      return;
    }
    if (phase === 'complete') {
      tw.finalize(sliced);
      return;
    }
    if (!shouldBufferUnclassifiedProcessNarration(messageId, sliced)) {
      tw.push(sliced);
    }
  }

  function applyCommentaryAndHidePlanBody(
    updater: (fn: (target: ChatMessage) => void) => void,
    tw: StreamPainter,
    commentaryStream: CommentaryStream,
    text: string,
    strippedContent: string,
    kind: string | undefined,
    contentOffset: number,
  ) {
    const remainder = stripRecommendMark(strippedContent.slice(contentOffset));
    let hideBody = false;
    const shown = commentaryStream.show(text, kind);
    updater((target) => {
      ingestPlanReportText(target, text);
      const report = String(target.planReport || '').trim();
      const rest = remainder.trim();
      hideBody = Boolean(report) && (!rest || report.includes(rest) || looksLikePlanReport(rest));
      if (shouldHoldPlanStreamOffBody(target)) hideBody = true;
      if (hideBody) target.content = '';
    });
    tw.reset(hideBody ? '' : remainder);
    return shown;
  }

  function stripRecommendMark(text: string) {
    return text.replace(REC_RE, '').replace(/\[\[[^\]]*$/, '').trimEnd();
  }

  function parseRecommendIds(text: string): string[] {
    const match = text.match(REC_RE);
    return match ? match[1].split(',').map((item) => item.trim()).filter(Boolean) : [];
  }

  function findRecommendedApps(content: string) {
    const ids = new Set(parseRecommendIds(content));
    if (!ids.size) return [];
    return options.appList.value.filter((app: any) => ids.has(String(app.id)));
  }

  // 后端 recommend_agents 事件下发的智能体 id → 在「智能体广场」全量可见目录里回源。
  // 这里故意不用 subagents/@ 候选：外部智能体可以被推荐并新窗口打开，但不因此获得委派权限。
  // 当前广场已不可见的 id 静默丢弃；全部失效时自然退回纯文字。
  function findAppsByIds(ids: string[], reasons?: Record<string, string>) {
    const set = new Set((ids || []).map(String));
    if (!set.size) return [];
    return options.appList.value
      .filter((app: any) => set.has(String(app.id)))
      .map((app: any) => ({
        ...app,
        recommendReason: reasons?.[String(app.id)] || undefined,
      }));
  }

  /** 历史轨迹回放 + 把「内部推荐智能体 id」现场解析成卡片（2026-07-29）。
   *
   *  后端把这几样常驻标记投影进了 trace（上下文压缩提示 / 路由到的智能体 / 外部推荐 /
   *  内部推荐 id），reducer 也已透传——但内部推荐必须用**当前**应用列表匹配才能出卡：
   *  存整卡快照会在智能体改名、下架、或换用户后与真实应用对不上。匹配不到就不出卡
   *  （与实时侧 recommend_agents 事件同口径），不留一张点不开的死卡。 */
  function restoreHistoryTrace(trace: ExecutionTracePayload | null | undefined) {
    const restored = restoreExecutionTrace(trace);
    const ids = restored.recommendedAgentIds;
    if (!ids?.length) return restored;
    const apps = findAppsByIds(ids, restored.recommendationReasons);
    return {
      ...restored,
      recommendedAgents: apps.length ? apps : undefined,
      recommendationIntent: restored.recommendationIntent,
    };
  }

  function normalizeStoredMessage(
    message: Awaited<ReturnType<typeof getThreadMessages>>[number],
  ): ChatMessage {
    const content = String(message.content || '');
    const isAssistant = message.role !== 'user';
    const restored = isAssistant ? restoreHistoryTrace(message.execution_trace as ExecutionTracePayload) : {};
    const failureAnchor = isAssistant && content.trim() === '（任务执行失败，未生成回复）';
    const storedAttachments = !isAssistant && message.attachments?.length
      ? message.attachments.map((att, index) => ({
          filename: String(att.filename || '附件'),
          kind: String(att.kind || 'text'),
          text: '',
          chars: 0,
          truncated: false,
          status: att.status === 'partial' || att.status === 'failed' ? att.status : 'ok',
          note: att.note ? String(att.note) : undefined,
          fileId: att.file_id ? String(att.file_id) : undefined,
          referenceId: att.reference_id ? String(att.reference_id) : undefined,
          previewUrl:
            (message.id != null ? attachmentPreviewCache.get(message.id)?.[index] : undefined)
            ?? (att.preview_url ? String(att.preview_url) : undefined),
        }))
      : undefined;
    return {
      id: nextLocalId(),
      dbId: message.id,
      role: isAssistant ? 'assistant' : 'user',
      content: failureAnchor ? '' : isAssistant ? stripRecommendMark(content) : content,
      turnSkills: !isAssistant
        ? messageTurnSkills(undefined, storedAttachments, mentionSkills.value)
        : undefined,
      runId: message.run_id ? String(message.run_id) : undefined,
      recommendedAgents: isAssistant ? findRecommendedApps(content) : undefined,
      feedback: isAssistant ? message.feedback ?? null : undefined,
      citations: isAssistant && message.citations?.length ? message.citations : undefined,
      // 用户消息附件卡回放（attachments_json 元数据快照，P0 附件生命周期）：
      // 无正文与原图字节，仅名称/类型/读取状态 + 图片压缩缩略图 preview_url——刷新后附件
      // 与图片都不再「凭空消失」。会话内优先内存缓存的原图（更清晰）；缓存失效（刷新/LRU
      // 淘汰）回落落库缩略图；两者皆无（本修复前的旧消息）降级为文件名卡。
      attachments: storedAttachments,
      // 执行轨迹回放（结构化步骤/状态/耗时/文件卡，不含思考原文）；旧数据缺失时稳定降级
      ...restored,
      ...(failureAnchor ? {
        runFailed: true,
        error: restored.error || '本次回复未能完成。你的问题已保留，请稍后重试。',
      } : {}),
      ...(isAssistant && (message as { agent_mode?: string }).agent_mode
        ? { agentMode: String((message as { agent_mode?: string }).agent_mode) }
        : {}),
      // 重新生成的旧版本不再保留可操作的交互入口。
      ...(isAssistant && message.status === 'superseded' ? { superseded: true, interactive: null } : {}),
      // chip 回放（agent_steps）：刷新/切回后保留「这轮谁办的事」；running 残留归一成 completed
      // 不还原（历史消息不存在进行中态）
      subagentCalls: isAssistant && message.subagent_calls?.length
        ? message.subagent_calls.map((c) => ({
            name: c.name || '子智能体',
            status: c.status === 'failed' ? 'failed' as const : 'completed' as const,
          }))
        : undefined,
    };
  }

  /** 将历史接口中的执行分段放回用户插话之前。Assistant 最终消息在数据库里只落一行，
   *  而 instruction 用户消息发生在其生成期间；若只按消息表排序，刷新后会变成
   *  「所有插话 → 一整块执行」。这里先收集最终消息随带的 segments，再按
   *  inputMessageId 将每个旧执行段插回原时间位置。 */
  function expandStoredExecutionSegments(
    messages: Awaited<ReturnType<typeof getThreadMessages>>,
  ): ChatMessage[] {
    const beforeInput = new Map<number, ChatMessage[]>();
    const unmatchedSegments: ChatMessage[] = [];

    for (const raw of messages) {
      if (raw.role === 'user') continue;
      const segments = raw.execution_trace?.segments || [];
      segments.forEach((segment, index) => {
        const completedAt = Number(segment.completedAt || 0) || undefined;
        const restored = restoreExecutionTrace({
          startedAt: segment.startedAt,
          completedAt: segment.completedAt,
          durationMs: segment.durationMs,
          status: 'completed',
          steps: segment.steps || [],
          preamble: segment.preamble,
          plan_report: segment.plan_report,
          reasoning_summary: segment.reasoning_summary,
          reasoning_seconds: segment.reasoning_seconds,
        });
        const historicalSegment: ChatMessage = {
          id: nextLocalId(),
          role: 'assistant',
          content: '',
          runId: raw.run_id ? String(raw.run_id) : undefined,
          executionSegmentIndex: index,
          executionSegmentEndedAt: completedAt || Date.now(),
          executionSegmentInputId: segment.inputId
            ? String(segment.inputId)
            : undefined,
          executionSegmentStartSequence: Number(segment.startSequence || 0) || undefined,
          executionSegmentEndSequence: Number(segment.endSequence || 0) || undefined,
          ...restored,
        };
        const inputMessageId = Number(segment.inputMessageId || 0);
        if (inputMessageId > 0) {
          const list = beforeInput.get(inputMessageId) || [];
          list.push(historicalSegment);
          beforeInput.set(inputMessageId, list);
        } else {
          unmatchedSegments.push(historicalSegment);
        }
      });
    }

    const expanded: ChatMessage[] = [];
    for (const raw of messages) {
      if (raw.role === 'user' && raw.id != null) {
        expanded.push(...(beforeInput.get(Number(raw.id)) || []));
        beforeInput.delete(Number(raw.id));
      }
      if (raw.role !== 'user' && unmatchedSegments.length) {
        expanded.push(...unmatchedSegments.splice(0));
      }
      const normalized = normalizeStoredMessage(raw);
      if (raw.role !== 'user' && raw.execution_trace?.segments?.length) {
        normalized.executionSegmentIndex = raw.execution_trace.segments.length;
        const latest = raw.execution_trace.segments[raw.execution_trace.segments.length - 1];
        normalized.executionSegmentStartSequence = latest?.endSequence
          ? Number(latest.endSequence) + 1
          : undefined;
      }
      expanded.push(normalized);
    }
    for (const segments of beforeInput.values()) expanded.push(...segments);
    return expanded;
  }

  /** 重新生成历史版本归组（第四批项 3）：status=superseded 的旧回答不占对话流位置，
   *  挂到紧随其后的当前助手回答的 supersededVersions（多次重新生成按原顺序累积，
   *  MessageList 折叠为「查看上一版」入口）。序列异常（旧版本后面不是当前回答）时
   *  如实平铺，不吞消息。 */
  function groupSupersededMessages(list: ChatMessage[]): ChatMessage[] {
    const result: ChatMessage[] = [];
    let pendingOld: ChatMessage[] = [];
    for (const m of list) {
      if (m.role === 'assistant' && m.superseded) {
        pendingOld.push(m);
        continue;
      }
      if (pendingOld.length) {
        if (m.role === 'assistant') m.supersededVersions = pendingOld;
        else result.push(...pendingOld); // 异常序列（中间夹了用户消息）：平铺兜底
        pendingOld = [];
      }
      result.push(m);
    }
    if (pendingOld.length) result.push(...pendingOld); // 尾部残留（缺当前回答）：平铺兜底
    return result;
  }

  /** 裸 EOF 交接公共出口（断流≠完成，P0-2 抽取复用）：POST /chat 与 /chat/resume 流传输
   *  正常结束、但整条连接没见过业务终态/挂起信号时，按已收游标把半截气泡移交订阅通道续订
   *  （正文播种 + 原地复用同一条消息，不删除重建、不闪空白），绝不当成功收尾。
   *  返回 true=已交接（调用方跳过正常收尾、不清 activeRun，Run 状态交由订阅通道裁决）。 */
  function handOffBareEofToSubscription(params: {
    sawTerminal: boolean;
    hadError: boolean;
    suspended: boolean;
    aborted: boolean;
    runId: string;
    runThreadId: string;
    afterSequence: number;
    initialContent: string;
    reuseMessageId: number;
    contentOffset?: number;
    stopTypewriter: () => void;
    /** POST 流所属的切段控制器：交接前必须先摘除，否则 subscribeVisibleRun 仍见旧控制器、
     *  插话 split 写到已收尾的闭包（streamContent/offset 不再更新），双写复现。 */
    releaseSegmentController?: RunSegmentController | null;
  }): boolean {
    const { sawTerminal, hadError, suspended, aborted, runId, runThreadId } = params;
    if (sawTerminal || hadError || suspended || aborted || !runId || !runThreadId) return false;
    params.stopTypewriter();
    // 本条 POST 流已经结束（裸 EOF），交接前先注销它自己的控制器：注销在 runAssistantTurn
    // 的 finally 里、晚于本函数，而 subscribeVisibleRun 的「已有活流就不重订」闸（防止
    // 生成中点当前会话开出第二条订阅、把停止按钮弄失效）会把这条**合法交接**一并挡掉。
    runControllers.delete(runId);
    // 同步摘除 POST 流的 segment controller（身份比对，防误删新订阅已装的控制器）
    if (
      params.releaseSegmentController
      && runSegmentControllers.get(runId) === params.releaseSegmentController
    ) {
      runSegmentControllers.delete(runId);
    }
    void subscribeVisibleRun(runThreadId, runId, {
      afterSequence: params.afterSequence,
      initialContent: params.initialContent,
      reuseMessageId: params.reuseMessageId,
      contentOffset: params.contentOffset,
    });
    return true;
  }

  async function runAssistantTurn(
    content: string,
    opts?: {
      regenerate?: boolean;
      interviewInput?: InterviewInput;
      attachments?: TurnAttachment[];
      /** 编辑重发（F2）：后端删除 id >= 此值的历史消息（线性覆盖，与本地裁剪对齐） */
      truncateFromMessageId?: number;
      /** R5 消歧卡点选：以显式 subagent_id 重发，后端按显式 @ 委派（绕过再路由/消歧） */
      subagentId?: string;
      /** 显式委托目标名称，只用于用户气泡/历史回放标签，不参与路由。 */
      subagentName?: string;
      /** 消歧重发复用原轮一次性上下文（skill + 上传附件）——原轮发送时两者都已消费清空 */
      reuseTurnContext?: boolean;
      /** 编辑重发只覆盖 Skill：精确复用被编辑消息的快照，不消费 composer 当前 Skill。 */
      skillOverride?: SkillItem[];
      /** 本轮使用 Plan Profile。 */
      planMode?: boolean;
      /** Deep Research：本轮强制联网、必要时集中澄清，再走原生工具循环深入研究。 */
      researchProfile?: boolean;
      /** 队列派发轮（§10.6 P0）：随请求携带租约，由服务端在 Run 持久化后原子确认删除。 */
      queueDispatch?: { itemId: string; leaseToken: string };
      /** 发送前被消费的草稿记录键；用于持久化首帧前断线请求的幂等身份。 */
      draftRequestKey?: string;
      /** 本轮已乐观插入的用户气泡，收到落库 id 时只回填它，不能误绑到中途插入的引导消息。 */
      userMessageLocalId?: number;
      /** 上一轮已结束的 Run：队列发出去要当续做，不能当新任务。 */
      resumeSourceRunId?: string;
      /** TurnContext 快照（审计项 1）：队列派发按入队时刻的快照执行，不读当前全局选择——
       *  排队期间改选 Skill/知识库/文件/模型不得漂移到已排队的消息上。提供时本轮不消费/
       *  不清空 composer 的当前选择。 */
      contextOverride?: {
        skills?: SkillItem[];
        knowledge?: KnowledgeSelection[];
        files?: Array<{ id: string; filename: string }>;
        // 排队项的「最近的对话」引用快照：缺这个字段时 runAssistantTurn 取到 undefined
        // 并落成空数组，用户选的会话引用在排队派发时静默丢失（2026-07-29 深扫 P0）
        threads?: Array<{ id: string; title?: string }>;
        webSearch?: boolean;
        model?: string;
      };
    },
  ) {
    const viewToken = viewVersion;
    let runThreadId = currentThreadId.value;
    const turnAssistantPreset = assistantPreset.value;
    const turnWorkFolderId = turnAssistantPreset ? undefined : selectedWorkFolder.value?.id;
    const presentationTurn = isRestrictedAssistantPreset(turnAssistantPreset);
    const turnPolicy = getBuiltinUiPolicy(turnAssistantPreset);
    const campusTurn = turnPolicy?.imageOnlyUpload === true;
    let runId = '';
    const turnPlanProfile = turnPolicy?.hidePlanMode ? false : Boolean(opts?.planMode);
    const turnResearchProfile = turnPolicy?.hideResearch ? false : Boolean(opts?.researchProfile);
    chatLoading.value = true;
    clearChatTaskTimer();
    chatTaskState.value = 'running';

    // 消歧提问卡（ask_user_choice）：直接打字也是回答——后端会把本条消息转交 resume 续接、
    // 一次性令牌随之消费，发送即收起历史消息上残留的提问卡（点选/打字两条路都不留死卡）。
    // 收卡同时冻结该消息的执行计时（等待态没有终态事件，不冻结会一直走秒）。
    // 子智能体 HITL 表单卡不带 ask_user 标记，不受影响（打字会被后端提示走卡片）。
    for (const m of chatMessages.value) {
      if (m.interactive?.ask_user) {
        m.interactive = null;
        if (m.runStartedAt && m.runDurationMs == null) markRunCompleted(m);
      }
    }

    const assistantMessage: ChatMessage = {
      id: nextLocalId(),
      role: 'assistant',
      content: '',
      // 只记录用户真实发送时间，不在前端伪造计划或进度文案。
      runStartedAt: Date.now(),
      // 首个 run.started 可能要等鉴权、建 Run 或研究预检。模式先取本轮快照，服务端首帧
      // 到达后仍会以 agent_mode 校准。
      agentMode: turnResearchProfile ? 'research' : turnPlanProfile ? 'plan' : undefined,
      executionSegmentIndex: 0,
    };
    ensureRunningThought(assistantMessage);
    let assistantTargetId = assistantMessage.id;
    let segmentContentOffset = 0;
    let typewriter = createTypewriter(assistantTargetId);
    let commentaryStream: CommentaryStream;
    let reasoningStream: ReasoningStream;
    activeTypewriter = typewriter; // 供 stopChat 在「缓冲完成、动画进行中被打断」时停字
    chatMessages.value.push(assistantMessage);
    let completedSuccessfully = false;
    let hadError = false;
    // HTTP 4xx/断网等发生在 run.started 之前时，本轮实际没有被后端接收。
    // sendChat 据此恢复用户的文字和附件，避免点一次发送就丢稿。
    let rejectedBeforeStart = false;
    // 断流交接（整合路线图 #2）：POST 流断但 Run 已在后端创建时移交事件订阅通道续接
    let handedOff = false;
    // HITL 挂起（消歧卡/表单/审批）：本轮以卡片收尾、正文可以为空，不套「模型未返回内容」兜底
    let suspendedTurn = false;
    // 语义收口（断流≠完成）：[DONE]/TCP EOF 只是传输结束，业务终态只有 run.completed /
    // run.failed / error / 合法挂起。POST 流正常 EOF 但没见过任何业务终态时，Run 大概率
    // 仍在后端续跑——按已收游标（lastSeq）交接订阅通道续订，正文以 streamContent 播种。
    let sawTerminal = false;
    let terminalOutcome: 'completed' | 'partial' | 'failed' | 'cancelled' | null = null;
    let lastSeq = 0;
    let streamContent = '';
    const controller = new AbortController();
    abortController = controller;

    const updateAssistant = (fn: (message: ChatMessage) => void) => {
      if (runThreadId && currentThreadId.value !== runThreadId) return;
      const target = chatMessages.value.find((item) => item.id === assistantTargetId);
      if (target) fn(target);
    };
    commentaryStream = createCommentaryStream(updateAssistant);
    reasoningStream = createReasoningStream(updateAssistant);
    activeCommentaryStream = commentaryStream;
    activeReasoningStream = reasoningStream;

    // P2.10：流式活性提示（45s 无事件）——不报错、不中断，仅避免空屏误以为卡死。
    let lastStreamActivityAt = Date.now();
    const touchStreamActivity = () => {
      lastStreamActivityAt = Date.now();
    };
    const streamIdleTimer = setInterval(() => {
      const idleMs = Date.now() - lastStreamActivityAt;
      if (idleMs < 45000) return;
      updateAssistant((target) => {
        if (target.content && String(target.content).trim()) return;
        // 已有真实工具/子智能体动作时不抢开场白
        const acted = Boolean(
          target.agentSteps?.some((s) => s.kind === 'tool' || s.kind === 'subagent')
          || target.toolSteps?.length
          || target.subagentCalls?.length,
        );
        if (acted) return;
        // 45s 轻提示；3min 明确可停止（P2.10 防长空屏）
        const next = idleMs >= 180000
          ? '仍在处理，耗时较长；可继续等待，或点停止后重试。'
          : '仍在处理，请稍候…';
        if (target.preamble === next) return;
        if (
          target.preambleIsInitialProgress
          || !target.preamble
          || target.preamble === '仍在处理，请稍候…'
        ) {
          target.preamble = next;
          target.preambleIsInitialProgress = true;
        }
      });
    }, 5000);

    /**
     * 首帧前的服务不可用也要留下可见现场。此前为了避免未落库消息污染历史，直接删掉
     * 用户气泡和临时助手气泡；网络一抖，用户只会看到对话退回欢迎页，误以为内容丢失。
     * 本地失败气泡不入库，重试仍复用同一幂等键；它只负责把“没有送达”和草稿保留说清楚。
     */
    const preservePreflightFailure = (message?: string) => {
      typewriter.stop();
      updateAssistant((target) => {
        // 首帧前失败：无论乐观「接下来会…」还是系统 initial_progress，都要清掉过程语，
        // 只留错误态。否则「我正在处理…/接下来会…」与「服务暂时不可用」叠成双气泡。
        target.preamble = '';
        target.preambleIsInitialProgress = false;
        // 若模型/占位只吐出了机械确认腔正文，失败时一并清掉，避免假确认残留。
        const softBody = String(target.content || '').trim();
        if (
          !softBody
          || /^(收到[。.!！]?|好的[，,。.!！]?|我正在处理你的请求[。.!！]?|正在处理[…\.。.!！]*)$/.test(softBody)
          || /^(接下来会[：:].{0,80})$/.test(softBody)
        ) {
          target.content = '';
        }
        // 已有更具体错误文案时不要被默认 503 话术覆盖（run.failed 无 run.started 路径）。
        const existing = String(target.error || '').trim();
        const next = String(message || '').trim();
        if (next) {
          target.error = next;
        } else if (!existing) {
          target.error = '服务暂时不可用，输入已保留；请稍后重试。';
        }
        markRunFailed(target);
      });
    };

    let ownedSegmentController: RunSegmentController | null = null;
    const installSegmentController = () => {
      if (!runId) return;
      const controller: RunSegmentController = {
        split: () => {
          // 先 stop 灭活打字机，避免已排队的 tick 在定稿后写回旧气泡
          typewriter.stop();
          commentaryStream.stop();
          reasoningStream.stop();
          const previous = chatMessages.value.find((item) => item.id === assistantTargetId);
          const endedAt = Date.now();
          if (previous) {
            previous.executionSegmentEndedAt = endedAt;
            previous.executionSegmentEndSequence = lastSeq || undefined;
            // 不再给前一段落终态时长：插话不结束这一轮（Codex 契约=整轮一个执行头）。
            // 时长由本轮末段在收尾时写，首段的头按整轮口径显示。
            sealSegmentSteps(previous);
          }
          const nextSegment: ChatMessage = {
            id: nextLocalId(),
            role: 'assistant',
            content: '',
            runId,
            executionSegmentIndex: (previous?.executionSegmentIndex ?? 0) + 1,
            executionSegmentStartSequence: lastSeq ? lastSeq + 1 : undefined,
            // 继承整轮起点（同上）：插话不重开一轮计时。
            runStartedAt: previous?.runStartedAt || endedAt,
            agentMode: previous?.agentMode,
            planExecutionUnlocked: previous?.planExecutionUnlocked,
          };
          // 正文切点吸附到块边界，并把前一段就地定稿。
          // 已封存（executionSegmentEndedAt）后 updateChatMessageContent 拒写——这里必须
          // 直写定稿（封存检查只拦打字机/delta 回写，不拦本次定稿）。
          const cut = segmentSplitOffset(streamContent, segmentContentOffset);
          const sealedContent = stripRecommendMark(streamContent.slice(segmentContentOffset, cut));
          if (previous) {
            const idx = chatMessages.value.findIndex((item) => item.id === previous.id);
            if (idx >= 0) {
              chatMessages.value[idx] = { ...chatMessages.value[idx], content: sealedContent };
            }
          }
          chatMessages.value.push(nextSegment);
          assistantTargetId = nextSegment.id;
          // cut：有段落边界时是边界后（未完成块留给新段）；无边界时是 full.length（整段定稿）
          segmentContentOffset = cut;
          typewriter = createTypewriter(assistantTargetId);
          commentaryStream = createCommentaryStream(updateAssistant);
          reasoningStream = createReasoningStream(updateAssistant);
          activeTypewriter = typewriter;
          activeCommentaryStream = commentaryStream;
          activeReasoningStream = reasoningStream;
          trackRunCursor(runId, assistantTargetId, { contentOffset: segmentContentOffset });
          return assistantTargetId;
        },
      };
      ownedSegmentController = controller;
      runSegmentControllers.set(runId, controller);
    };

    // Skill / 文件 / 知识库 / 对话引用都是一次性：本轮请求带上，随后取消选中，
    // 气泡上留下对应图标。重新生成/消歧重发沿用上一轮快照。
    // 队列派发轮（审计项 1）：一律用入队时刻的快照（contextOverride），不读/不消费当前选择。
    const ctxOverride = opts?.contextOverride;
    const reuseTurn = opts?.regenerate || opts?.reuseTurnContext;
    const turnSkills = presentationTurn
      ? []
      : reuseTurn
        ? lastTurnSkills
        : opts?.skillOverride ? [...opts.skillOverride]
        : ctxOverride ? [...(ctxOverride.skills || [])] : [...selectedSkills.value];
    if (!reuseTurn) {
      lastTurnSkills = turnSkills;
      if (!ctxOverride && !opts?.skillOverride) selectedSkills.value = [];
    }
    const turnThreads: ThreadReference[] = (reuseTurn
      ? lastTurnThreads
      : ctxOverride ? [...(ctxOverride.threads || [])] : [...selectedThreadList.value])
      .map((thread) => ({ id: thread.id, title: String(thread.title || '') }));
    if (!reuseTurn) {
      lastTurnThreads = turnThreads;
      if (!ctxOverride) selectedThreadList.value = [];
    }
    const turnKnowledge = reuseTurn
      ? lastTurnKnowledge
      : ctxOverride ? [...(ctxOverride.knowledge || [])] : [...selectedKnowledgeList.value];
    if (!reuseTurn) {
      lastTurnKnowledge = turnKnowledge;
      if (!ctxOverride) selectedKnowledgeList.value = [];
    }
    const turnFiles = limitFileSelection(reuseTurn
      ? lastTurnFiles
      : ctxOverride ? [...(ctxOverride.files || [])] : [...selectedFileList.value]);
    if (!reuseTurn) {
      lastTurnFiles = turnFiles.map((f) => ({ ...f })) as UserFileSelection[];
      if (!ctxOverride) selectedFileList.value = [];
    }
    const turnWebSearch = ctxOverride ? Boolean(ctxOverride.webSearch) : webSearchOn.value;
    const turnModel = (ctxOverride?.model || activeModel.value);
    // 上传附件同理按轮记录：重新生成/消歧重发都沿用原轮附件——附件文本只进当轮模型输入、
    // 不落线程，重新生成不重传的话模型会**完全丢失附件内容**（审查 P1，2026-07-15 修复：
    // 原「普通重新生成不重传附件」的语义就是这个丢失路径）
    const turnSubagentName = opts?.subagentName || (reuseTurn ? lastTurnSubagentName : '');
    if (!reuseTurn) lastTurnSubagentName = opts?.subagentName || '';
    const turnAttachments = (reuseTurn ? lastTurnAttachments : opts?.attachments || [])
      .filter((item) => !isComposerReferenceKind(item.kind));
    if (!reuseTurn) {
      lastTurnAttachments = turnAttachments;
      lastTurnHadAttachments.value = lastTurnAttachments.length > 0;
    }
    const referenceCards = buildComposerBubbleAttachments({
      skills: turnSkills,
      knowledge: turnKnowledge,
      subagent: turnSubagentName ? { name: turnSubagentName } : null,
      webSearch: turnWebSearch,
    });
    const outgoingAttachments: TurnAttachment[] = [
      ...turnAttachments,
      ...referenceCards.map((item) => ({
        filename: item.filename,
        text: '',
        kind: item.kind,
      })),
    ];
    const requestAttachments = campusTurn
      ? outgoingAttachments.filter((item) => item.kind === 'image')
      : outgoingAttachments;

    const requestKey = requestFingerprint({
      threadId: runThreadId,
      content,
      regenerate: Boolean(opts?.regenerate),
      truncateFromMessageId: opts?.truncateFromMessageId || null,
      subagentId: presentationTurn ? null : opts?.subagentId || null,
      assistantPreset: turnAssistantPreset || null,
      workspaceFolderId: turnWorkFolderId || null,
      ...(opts?.interviewInput ? { interviewInput: opts.interviewInput } : {}),
      planMode: turnPlanProfile,
      model: turnModel,
      skillIds: turnSkills.map((item) => item.id),
      knowledgeIds: turnKnowledge.map((item) => item.id),
      fileIds: turnFiles.map((item) => item.id),
      threadIds: turnThreads.map((item) => item.id),
      webSearch: turnWebSearch,
      attachments: requestAttachments.map((item) => ({
        filename: item.filename,
        fileId: item.file_id || '',
        sha256: item.sha256 || '',
        kind: item.kind,
        text: requestFingerprint(item.text || ''),
        imageLength: item.image_url?.length || 0,
      })),
      queueItemId: opts?.queueDispatch?.itemId || null,
    });
    // 可靠握手幂等键（N-02）：只有服务端明确开始/终结后才丢弃。首帧前断线且反查未知时，
    // 同输入重试复用此键，由 PG 唯一约束裁掉第二个执行。
    const clientRequestId = pendingClientRequests.get(requestKey) || newClientRequestId();
    pendingClientRequests.set(requestKey, clientRequestId);
    if (opts?.draftRequestKey) {
      pendingRequestByDraft.set(opts.draftRequestKey, {
        fingerprint: requestKey,
        requestId: clientRequestId,
      });
    }
    while (pendingClientRequests.size > 20) {
      const oldest = pendingClientRequests.keys().next().value;
      if (!oldest) break;
      pendingClientRequests.delete(oldest);
    }
    activeClientRequestId = clientRequestId;

    function clearPendingRequestIdentity() {
      pendingClientRequests.delete(requestKey);
      const draftKey = opts?.draftRequestKey;
      const pending = draftKey ? pendingRequestByDraft.get(draftKey) : undefined;
      if (draftKey && pending?.requestId === clientRequestId) pendingRequestByDraft.delete(draftKey);
    }

    async function lookupAcceptedRun(attempts = 3): Promise<
      | { kind: 'found'; run_id: string; thread_id: string }
      | { kind: 'not_found' }
      | { kind: 'unknown'; error: unknown }
    > {
      let lastError: unknown;
      for (let attempt = 0; attempt < attempts; attempt += 1) {
        if (attempt > 0) await new Promise((resolve) => window.setTimeout(resolve, 600));
        try {
          const result = await getRunByClientRequest(clientRequestId);
          if (result.kind === 'found') return result;
        } catch (error) {
          lastError = error;
        }
      }
      return lastError ? { kind: 'unknown', error: lastError } : { kind: 'not_found' };
    }

    function adoptAcceptedRun(discovered: { run_id: string; thread_id: string }) {
      runId = discovered.run_id;
      runThreadId = discovered.thread_id;
      clearPendingRequestIdentity();
      if (viewVersion === viewToken && (!currentThreadId.value || currentThreadId.value === runThreadId)) {
        currentThreadId.value = runThreadId;
      }
      setActiveRun(runThreadId, runId, 'created', { model: turnModel });
      updateAssistant((target) => { target.runId = runId; target.runStatus = 'created'; });
      if (turnPlanProfile) planProfileRuns.add(runId);
      if (turnResearchProfile) researchProfileRuns.add(runId);
      typewriter.stop();
      handedOff = true;
      // 交接前摘掉 POST 流的 segment controller，避免插话落到已收尾的闭包
      if (ownedSegmentController && runSegmentControllers.get(runId) === ownedSegmentController) {
        runSegmentControllers.delete(runId);
      }
      void subscribeVisibleRun(runThreadId, runId, {
        afterSequence: 0,
        initialContent: '',
        reuseMessageId: assistantTargetId,
      });
    }

    try {
      const answer = await createAgentChatCompletion({
        message: content,
        thread_id: currentThreadId.value || undefined,
        workspace_folder_id: turnWorkFolderId,
        model: campusTurn ? undefined : turnModel,
        skill_ids: campusTurn ? undefined : turnSkills.map((skill) => skill.id),
        selected_skills: campusTurn ? undefined : turnSkills,
        assistant_preset: turnAssistantPreset,
        interview_input: opts?.interviewInput,
        knowledge_ids: campusTurn || !turnKnowledge.length
          ? undefined
          : turnKnowledge.map((k) => k.id),
        selected_knowledge: campusTurn || !turnKnowledge.length ? undefined : turnKnowledge,
        file_ids: campusTurn || !turnFiles.length
          ? undefined
          : turnFiles.map((f) => f.id),
        thread_ids: campusTurn || !turnThreads.length
          ? undefined
          : turnThreads.map((t) => t.id),
        // @ 选中的智能体与 R5 消歧都走同一条显式委托链路：主对话携带 subagent_id，
        // agent-api 在执行前实时复核发布态和用户权限。
        subagent_id: presentationTurn ? undefined : opts?.subagentId,
        web_search: campusTurn ? false : turnWebSearch,
        attachments: requestAttachments.length ? requestAttachments : undefined,
        truncate_from_message_id: opts?.truncateFromMessageId,
        agent_mode: campusTurn ? 'standard' : turnResearchProfile ? 'research' : turnPlanProfile ? 'plan' : 'standard',
        queue_item_id: opts?.queueDispatch?.itemId,
        queue_lease_token: opts?.queueDispatch?.leaseToken,
        resume_source_run_id: opts?.resumeSourceRunId,
        client_request_id: clientRequestId,
        stream: true,
        regenerate: opts?.regenerate,
        signal: controller.signal,
        onRunStarted: (payload) => {
          runId = payload.run_id;
          runThreadId = payload.thread_id || runThreadId;
          clearPendingRequestIdentity();
          if (runId) runControllers.set(runId, controller);
          if (runThreadId && viewVersion === viewToken && (!currentThreadId.value || currentThreadId.value === runThreadId)) {
            currentThreadId.value = runThreadId;
          }
          setActiveRun(runThreadId, runId, payload.status || 'running', { model: payload.model || turnModel });
          if (turnPlanProfile && runId) planProfileRuns.add(runId);
          if (turnResearchProfile && runId) researchProfileRuns.add(runId);
          // V3 批次6：run.started 带 agent_mode=plan 即点亮任务模式按钮（以后端为准，
          // 不等首个 task.* 事件；auto 路由与手动开启从此同一时刻同一形态）
          if (payload.agent_mode === 'plan' && runId) registerPlanProfileRun(runThreadId, runId);
          if (payload.agent_mode === 'research' && runId) registerResearchProfileRun(runThreadId, runId);
          updateAssistant((target) => {
            target.agentMode = payload.agent_mode;
            target.runId = runId;
            target.runStatus = payload.status || 'running';
            markRunStarted(target, payload.timestamp);
            // v3.0 resume_meta 仍可能随 run.started 到达，续做在后端内部完成。
            // 2026-08-09：用户不需要看到「已从上次进度继续」注记，不再写 resumeNote。
            void payload.resume_meta;
          });
          installSegmentController();
        },
        onRunCompleted: (payload) => {
          terminalOutcome = 'completed';
          updateAssistant((target) => markRunCompleted(target, payload.timestamp));
          settleExecutionForRun(runId, 'completed');
        },
        onRunPartial: (payload) => {
          terminalOutcome = 'partial';
          updateAssistant((target) => markRunPartial(target, payload.timestamp));
          settleExecutionForRun(runId, 'failed');
        },
        onRunCancelled: (payload) => {
          terminalOutcome = 'cancelled';
          updateAssistant((target) => markRunCancelled(target, payload.timestamp));
          settleExecutionForRun(runId, 'failed');
        },
        onThreadId: (threadId) => {
          runThreadId = threadId || runThreadId;
          if (viewVersion === viewToken && !currentThreadId.value) currentThreadId.value = threadId;
        },
        onMessageId: (id) => {
          updateAssistant((target) => {
            target.dbId = id;
          });
        },
        onUserMessageId: (id) => {
          // 给本轮刚 push 的用户消息补 dbId（F2）：编辑该消息重发时以此为截断点
          if (runThreadId && currentThreadId.value !== runThreadId) return;
          const lastUser = opts?.userMessageLocalId
            ? chatMessages.value.find((m) => m.id === opts.userMessageLocalId)
            : [...chatMessages.value].reverse().find((m) => m.role === 'user');
          if (lastUser && !lastUser.dbId) {
            lastUser.dbId = id;
            // 图片缩略图入内存缓存：previewUrl（data URL）有意不落库，loadThread 重建
            // 消息时按 dbId 回填——会话内切走再切回，图片卡不退化成无图卡
            if (lastUser.attachments?.length) cacheAttachmentPreviews(id, lastUser.attachments);
          }
        },
        onInputApplied: ({ inputId }) => {
          // 引导真正被当前轮吸收（不中断）。提交时的提示只代表「已受理」，这里才是「已生效」。
          // 记下来供收尾时对账：本轮结束仍未生效的引导要如实告诉用户，不能让它悄无声息地没了。
          if (inputId) appliedInstructionIds.add(inputId);
          // 生效不写任何 UI 回执（Codex 对齐 2026-07-26）：确认由模型自己在后续叙述与
          // 最终回答的对账里给出（「你中途补充 X 之后，我从第 N 步起已按新要求执行」）。
          // 这里只记 id 供收尾对账——真正没生效的才需要如实提示。
        },
        onInputRejected: ({ inputId, reason }) => {
          const segment = [...chatMessages.value].reverse().find(
            (item) =>
              item.role === 'assistant'
              && item.runId === runId
              && item.executionSegmentInputId === inputId,
          );
          if (segment) segment.preamble = `未能应用这条追加要求：${reason}`;
        },
        onContextUsage: (usage) => {
          if (!runThreadId || currentThreadId.value === runThreadId) {
            // 计量行基线只认 actual（2026-07-26 真机修复）：调用前的 estimate 与调用后的
            // actual 描述同一份 prompt，差值＝本地估算误差而非上下文增长。此前不加区分，
            // 纯问答（回答「2」一个字）会把 4.8k 估算误差显示成「本轮产出」。旧事件无
            // kind＝按 actual 处理，历史消息口径不变。
            if (usage.kind !== 'estimate') {
              updateAssistant((target) => {
                if (target.turnBaselineTokens == null) target.turnBaselineTokens = usage.tokens;
                target.liveContextTokens = usage.tokens;
              });
            }
          }
        },
        onInteractive: (payload) => {
          // 消歧/HITL 卡出卡瞬间即挂起（对齐 subscribe/resume 通道）：
          // 此前只设 suspendedTurn，activeRun 仍是 running → chatLoading 一直 true，
          // MessageList 里 :submitting="loading && interactive" 把整卡禁用，用户点不了；
          // 要等 POST 流 finally 收尾才「突然又能点」——真机间歇「卡点不了」根因。
          suspendedTurn = true;
          if (runThreadId && runId) setActiveRun(runThreadId, runId, 'waiting_user');
          updateAssistant((target) => {
            target.interactive = payload;
          });
        },
        onCitations: (sources) => {
          updateAssistant((target) => {
            target.citations = sources;
          });
        },
        onRouteSelected: (route) => {
          updateAssistant((target) => {
            target.routedAgent = route.name || '智能体';
          });
        },
        onClarification: (payload) => {
          updateAssistant((target) => {
            target.clarification = payload.options;
            // 原轮一次性上下文（skill/上传附件）快照挂到卡片消息上：出卡后用户可能又发了
            // 别的消息（lastTurn* 被覆盖），点选旧卡时按快照精确复用该轮的，而非“最近一轮”的
            target.clarificationContext = { skills: turnSkills, attachments: turnAttachments || [] };
          });
        },
        onApproval: (payload) => {
          // 审批挂起与 input.required 同语义（对齐）：本轮以审批卡收尾，正文可以为空，
          // 不套「模型未返回内容」兜底，也不当真正终态派发队列。
          // 同步置 waiting_user，避免卡已出但 loading 仍 true 导致按钮整段禁用。
          suspendedTurn = true;
          if (runThreadId && runId) setActiveRun(runThreadId, runId, 'waiting_user');
          updateAssistant((target) => {
            target.approval = payload;
          });
        },
        onRecommendation: (items) => {
          if (!items.length) return;
          updateAssistant((target) => {
            target.externalRecs = items;
          });
        },
        onRecommendAgents: (payload) => {
          const apps = findAppsByIds(payload.ids, payload.reasons);
          if (!apps.length) return;
          updateAssistant((target) => {
            // 智能体广场推荐卡（含外部智能体）；只跳转，不进入 @/委派链路。
            target.recommendedAgents = apps;
            target.recommendationIntent = payload.intent;
          });
        },
        onCompaction: (payload) => {
          updateAssistant((target) => applyCompaction(target, payload));
        },
        onCompacted: (note) => {
          updateAssistant((target) => {
            applyCompaction(target, { status: 'completed' });
            if (note && note !== 'Context compacted') target.compactedNote = note;
          });
        },
        onError: (message) => {
          // 用户主动停止会先本地 abort，再让后端 cancel 回推 run.failed（“已停止生成”）——
          // 那是取消而非错误，不标红；未 abort 的真失败照常提示。
          if (controller.signal.aborted) return;
          let absorbed = false;
          updateAssistant((target) => {
            if (absorbFailedRunIfUserClarification(target, markRunCompleted)) {
              absorbed = true;
              return;
            }
            const softBodyRe = /^(收到[。.!！]?|好的[，,。.!！]?|我正在处理你的请求[。.!！]?|正在处理[…\.。.!！]*)$/;
            const nextActionRe = /^(接下来会[：:].{0,80})$/;
            const softBody = String(target.content || '').trim();
            const isSoftBody = !softBody || softBodyRe.test(softBody) || nextActionRe.test(softBody);
            // 机械确认腔不算交付——否则「我正在处理…」会挡住 preamble 清理，叠成双气泡。
            const delivered = Boolean(
              (!isSoftBody && softBody)
              || target.generatedFiles?.length
              || target.agentSteps?.some((s) => s.kind === 'tool' || s.kind === 'subagent')
              || target.toolSteps?.length
              || target.subagentCalls?.length
            );
            // 无实质交付时清掉乐观/机械过程语，避免与错误横幅叠成双气泡。
            if (!delivered) {
              target.preamble = '';
              target.preambleIsInitialProgress = false;
              if (isSoftBody) target.content = '';
            }
            target.error = message;
            markRunFailed(target);
          });
          if (absorbed) {
            terminalOutcome = 'completed';
            settleExecutionForRun(runId, 'completed');
            return;
          }
          hadError = true;
          terminalOutcome = 'failed';
          settleExecutionForRun(runId, 'failed');
        },
        onPlanUpdate: (event) => {
          updateAssistant((target) => applyPlanUpdate(target, event));
        },
        onTaskPlan: (event) => {
          if (recordRunPlan(runId, event)) {
            updateAssistant((target) => applyTaskPlan(target, event));
          }
        },
        onSequenceGap: () => {
          void reloadRunPlanSnapshot(runId);
        },
        onCapabilityLoaded: (names) => {
          updateAssistant((target) => applyCapabilityLoaded(target, names));
        },
        onArtifactSaved: (payload) => {
          if (!routeArtifactEventToRun(
            chatMessages.value,
            runId,
            assistantTargetId,
            payload.files,
            (target) => applyArtifactSaved(target as ChatMessage, payload),
          )) {
            updateAssistant((target) => applyArtifactSaved(target, payload));
          }
        },
        onResearchProgress: (payload) => {
          updateAssistant((target) => applyResearchProgress(target, payload));
        },
        onToolEvent: (ev: ToolStepEvent) => {
          touchStreamActivity();
          // 切段后工具终态优先收口前段仍 running 的同名步骤，避免假完成 + 新段重复
          if (!routeToolEventToRun(chatMessages.value, runId, assistantTargetId, ev, (t) => applyToolEvent(t as ChatMessage, ev))) {
            updateAssistant((target) => applyToolEvent(target, ev));
          }
        },
        onSubagentStep: (ev) => updateAssistant((target) => applySubagentStep(target, ev)),
        onAttachmentsStatus: (items) => {
          updateAssistant((target) => applyAttachmentsStatus(target, items));
        },
        onReasoningDelta: (delta, fullReasoning) => {
          touchStreamActivity();
          if (!runThreadId || currentThreadId.value === runThreadId) {
            reasoningStream.push(delta, fullReasoning);
          }
        },
        onReasoningCompleted: (payload) => {
          if (!runThreadId || currentThreadId.value === runThreadId) {
            return reasoningStream.complete(payload);
          }
        },
        onReasoningConnectionEnd: () => {
          if (!runThreadId || currentThreadId.value === runThreadId) {
            return reasoningStream.complete().then(() => updateAssistant((target) => {
              clearTransientReasoning(target);
            }));
          }
        },
        onDelta: (_delta, fullContent) => {
          touchStreamActivity();
          streamContent = fullContent; // 断流交接播种用：原始累积正文（与解析器口径一致，不剔推荐标记）
          if (runId) trackRunCursor(runId, assistantTargetId, { content: fullContent });
          if (!runThreadId || currentThreadId.value === runThreadId) {
            updateAssistant(revealAssistantOutput);
            paintAssistantStreamText(
              updateAssistant,
              typewriter,
              stripRecommendMark(fullContent.slice(segmentContentOffset)),
              'delta',
              assistantTargetId,
            );
          }
        },
        onMessageCompleted: (fullContent) => {
          streamContent = fullContent;
          if (runId) trackRunCursor(runId, assistantTargetId, { content: fullContent });
          if (!runThreadId || currentThreadId.value === runThreadId) {
            paintAssistantStreamText(
              updateAssistant,
              typewriter,
              stripRecommendMark(fullContent.slice(segmentContentOffset)),
              'complete',
              assistantTargetId,
            );
          }
        },
        onCommentary: (text, strippedContent, kind) => {
          touchStreamActivity();
          streamContent = strippedContent;
          if (runId) trackRunCursor(runId, assistantTargetId, { content: strippedContent });
          // 刚流出的那段其实是开场白/衔接语：正文缩回剔除后的全文，说明文本挪到
          // 开场白位（执行时间线上方）或时间线 note 步骤
          if (!runThreadId || currentThreadId.value === runThreadId) {
            return applyCommentaryAndHidePlanBody(
              updateAssistant,
              typewriter,
              commentaryStream,
              text,
              strippedContent,
              kind,
              segmentContentOffset,
            );
          }
        },
        onModelConnection: (payload) => {
          updateAssistant((target) => applyModelConnection(target, payload));
        },
        onRunPhase: (phase) => {
          updateAssistant((target) => { target.runStatus = phase; });
        },
        onSequence: (seq) => {
          lastSeq = seq;
          if (runId) trackRunCursor(runId, assistantTargetId, { seq });
        },
        onStreamEnd: (info) => {
          sawTerminal = info.sawTerminal;
          if (!runThreadId || currentThreadId.value === runThreadId) {
            updateAssistant(clearTransientReasoning);
          }
        },
      });

      // 并发同键请求的输家会收到静默 [DONE]，首帧前连接也可能在 Run 创建后结束。
      // 二者都不能当成“空成功”：先按幂等键找赢家；找不到则恢复草稿，并保留同一个
      // client_request_id 供重试，避免新 UUID 绕过服务端唯一约束。
      if (!runId && !sawTerminal && !hadError && !controller.signal.aborted) {
        const lookup = await lookupAcceptedRun();
        if (lookup.kind === 'found') {
          adoptAcceptedRun(lookup);
        } else {
          rejectedBeforeStart = true;
          preservePreflightFailure();
        }
      }

      // 启动前被拒（2026-07-26 并发审计）：后端拒绝并发第二个 Run 时**不是 HTTP 409**，
      // 而是发 SSE run.failed 且**刻意不发 run.started**（chat_service 的 R0 分支）。
      // 前端据此收到 hadError=true 但 runId 恒为空——上面的分支要求 !hadError，正好漏掉它，
      // 于是 runAssistantTurn 返回 true＝「已受理」，三处回滚全部不执行：
      //   ① sendChat 不退回输入框，用户气泡留在本地且无 dbId（刷新即消失）；
      //   ② regenerateLast / resendEditedMessage 裁掉的 removedTail 永不恢复＝旧回答丢失。
      // 判据安全：后端所有拒绝分支都保证不发 run.started，正是它们注释里写明的设计。
      if (!runId && hadError && !controller.signal.aborted && !rejectedBeforeStart) {
        rejectedBeforeStart = true;
        preservePreflightFailure();
      }

      // 裸 EOF（传输正常结束但未见业务终态/挂起、非主动停止）＝断流而非完成：
      // 按已收游标交接订阅通道续订，本条半截气泡原地复用（不删除重建、不闪空白），
      // 绝不渲染「模型未返回内容」。hadError/suspendedTurn 双保险兜住旧协议路径。
      if (!handedOff && !rejectedBeforeStart) {
        handedOff = handOffBareEofToSubscription({
          sawTerminal,
          hadError,
          suspended: suspendedTurn,
          aborted: controller.signal.aborted,
          runId,
          runThreadId,
          afterSequence: lastSeq,
          initialContent: streamContent,
          reuseMessageId: assistantTargetId,
          contentOffset: segmentContentOffset,
          stopTypewriter: () => typewriter.stop(),
          releaseSegmentController: ownedSegmentController,
        });
      }
      if (!handedOff && !rejectedBeforeStart) {
        const ids = parseRecommendIds(answer);
        // 空正文兜底文案：仅「确认收到业务终态且非挂起非错误」的真空回复才提示——HITL 挂起
        // （消歧卡/表单/审批卡）以卡片收尾、断流走上方交接，正文为空都是正常形态；
        // 错误由 message-error 标红承载。
        // 插话后的末段若从未收到新增量（offset 已到全文末尾），空正文是正常形态——
        // 不能冒出「模型未返回内容」（否则插话两侧会各有一句像回复的话）。
        const slicedAnswer = stripRecommendMark(answer.slice(segmentContentOffset));
        let capturedPlan = false;
        updateAssistant((target) => {
          capturedPlan = ingestPlanReportText(target, slicedAnswer);
        });
        const currentSeg = chatMessages.value.find((m) => m.id === assistantTargetId);
        const emptySegmentOk = Boolean(
          capturedPlan
          || currentSeg?.planReport
          || (
            currentSeg?.executionSegmentIndex
            && currentSeg.executionSegmentIndex > 0
            && !String(currentSeg.content || '').trim()
            && !currentSeg.agentSteps?.some((s) => s.kind === 'tool' || s.kind === 'subagent')
            && !currentSeg.toolSteps?.length
          )
        );
        const cleanAnswer = capturedPlan
          ? ''
          : (slicedAnswer || (suspendedTurn || hadError || !sawTerminal || emptySegmentOk ? '' : '模型未返回内容'));
        await typewriter.finish(cleanAnswer);
        if (cleanAnswer && cleanAnswer !== '模型未返回内容') {
          updateAssistant((target) => {
            // 有实质终答正文：标 final（过程旁白已在 note/preamble）
            if (!target.narrativeKind || target.narrativeKind === 'commentary' || target.narrativeKind === 'ack') {
              target.narrativeKind = 'final';
            }
          });
        }
        // 流结束兜底折叠：无正文（出错/中止）时思考面板也不该停在“思考中”展开态
        updateAssistant(revealAssistantOutput);
        if (ids.length) {
          const matched = findRecommendedApps(answer);
          if (matched.length) {
            updateAssistant((target) => {
              target.recommendedAgents = matched;
            });
          }
        }
        // 收到 run.failed/error 事件时不算成功完成——避免误触发“回复已完成”提示。
        completedSuccessfully = terminalOutcome === 'completed';
        if (hadError && !cleanAnswer) {
          options.showError(chatMessages.value.find((m) => m.id === assistantTargetId)?.error || '生成失败');
        }
      }
    } catch (error) {
      const aborted =
        (error as { name?: string })?.name === 'AbortError' || controller.signal.aborted;
      if (aborted) {
        // 用户停止/打断：就地冻结在当前可见位置并截断，绝不把已缓冲的后续内容再吐出来
        // ——否则模型快、整段早已缓冲时，观感就是“照常输出到完、根本没被打断”（对齐 Claude 的截断式停止）。
        typewriter.stop();
        // 空壳判定收窄（审计项 14）：只有「什么都没产出」的消息才删——思考/工具步骤/子智能体
        // chip/卡片都算实质内容，删了会出现「停止后执行过程当场消失、重进又恢复」的闪失。
        const current = chatMessages.value.find((item) => item.id === assistantTargetId);
        const hasSubstance = Boolean(current && (
          current.content || current.preamble || current.planReport
          || current.agentSteps?.length || current.toolSteps?.length
          || current.generatedFiles?.length || current.subagentCalls?.length
          || current.interactive
        ));
        if (current && !hasSubstance) {
          chatMessages.value = chatMessages.value.filter((item) => item.id !== assistantTargetId);
        }
      } else if (runId && runThreadId) {
        // 传输层断流但 Run 已在后端创建（N-01）：与裸 EOF 走同一交接——原消息原地续写、
        // 按已收游标续订（afterSequence + 正文播种 + 复用同一条消息）。不删除重建、不从 0
        // 全量回放：半截回答不闪空，tool/subagent 事件也不会重复灌进执行时间线。
        typewriter.stop();
        handedOff = true;
        // 本条流已断（见上），交接前注销自己的控制器——注销在 finally、晚于此处，而
        // subscribeVisibleRun 的「已有活流不重订」闸会把这条合法交接一并挡掉
        runControllers.delete(runId);
        // 同步摘除 POST 流的 segment controller（与 handOffBareEofToSubscription 同口径）
        if (ownedSegmentController && runSegmentControllers.get(runId) === ownedSegmentController) {
          runSegmentControllers.delete(runId);
        }
        void subscribeVisibleRun(runThreadId, runId, {
          afterSequence: lastSeq,
          initialContent: streamContent,
          reuseMessageId: assistantTargetId,
          contentOffset: segmentContentOffset,
        });
      } else {
        // 首帧前断线（N-02）：POST 失败 ≠ 服务端没受理——run.started 之前的断连窗口里
        // Run 可能已建成并在后台执行。凭幂等键反查（短重试兜 create_run 的落库时延）：
        //  · 查到 → 按已建 Run 订阅续接（after=0 全量回放进本条气泡），不恢复草稿、
        //    不引导用户重发第二轮（那会创建重复 Run）；
        //  · 查不到/HTTP 4xx 业务拒绝 → 确实没建成，按原样恢复草稿允许重发。
        const httpStatus = (error as { status?: number })?.status;
        const lookup = (!httpStatus || httpStatus >= 500)
          ? await lookupAcceptedRun()
          : { kind: 'not_found' as const };
        if (lookup.kind === 'found') {
          adoptAcceptedRun(lookup);
        } else {
          // 明确 4xx 表示服务端拒绝，本次 id 可以作废；网络/5xx/反查 404 均保留同键，
          // 因为旧 POST 仍可能稍后落库，下一次重试必须受同一幂等键保护。
          if (httpStatus && httpStatus < 500) clearPendingRequestIdentity();
          rejectedBeforeStart = true;
          const rejectedMessage = httpStatus && httpStatus < 500 && error instanceof Error ? error.message : undefined;
          preservePreflightFailure(rejectedMessage);
          if (turnAssistantPreset === 'interview' && opts?.interviewInput && currentThreadId.value === runThreadId) {
            interviewInputRejected?.(error);
          }
          // 启动前失败不消费 Skill（审计项 12）：本轮根本没发出去，一次性 Skill 还回选择区，
          // 重试仍然带上（按 id 与用户此后新选的合并去重）
          if (!reuseTurn && !ctxOverride && turnSkills.length) {
            const existingIds = new Set(selectedSkills.value.map((s) => s.id));
            const restoreSkills = turnSkills.filter((s) => !existingIds.has(s.id));
            if (restoreSkills.length) selectedSkills.value = [...restoreSkills, ...selectedSkills.value];
          }
          const concurrentInterviewRun = turnAssistantPreset === 'interview' && httpStatus === 409
            && error instanceof Error && /仍在执行|排队或先停止/.test(error.message);
          if (!concurrentInterviewRun) options.showError(lookup.kind === 'unknown' ? lookup.error : error);
        }
      }
    } finally {
      clearInterval(streamIdleTimer);
      if (abortController === controller) abortController = null;
      if (activeTypewriter === typewriter) activeTypewriter = null;
      if (activeCommentaryStream === commentaryStream) activeCommentaryStream = null;
      if (activeReasoningStream === reasoningStream) activeReasoningStream = null;
      commentaryStream.stop();
      reasoningStream.stop();
      if (activeClientRequestId === clientRequestId) activeClientRequestId = '';
      if (runId || sawTerminal) clearPendingRequestIdentity();
      // 归属校验（F3 撞键）：切回会话后订阅流会以同一 runId 注册控制器，
      // 老发送流收尾时不能把别人的控制器删掉——否则「停止」的本地断流失效
      if (runId && runControllers.get(runId) === controller) runControllers.delete(runId);
      if (runId && ownedSegmentController && runSegmentControllers.get(runId) === ownedSegmentController) {
        runSegmentControllers.delete(runId);
      }
      // 切会话断连（本地 abort、Run 仍在后台生成）不清 activeRuns：历史列表「运行中」
      // 状态点与切回后的订阅恢复都依赖它；用户显式停止的清理由 stopChat 自己完成；
      // 断流交接（handedOff）同理——Run 状态交由订阅通道裁决，这里不许抢先清
      const detachedOnly = (controller.signal.aborted || handedOff) && !completedSuccessfully;
      if (runThreadId && !detachedOnly) {
        if (suspendedTurn && runId) {
          // 消歧卡/审批卡/通用 HITL（ask_user_choice 等）：与任务卡同级挂起，
          // 绝不能 clearActiveRun——否则 loading 虽碰巧变 false，但刷新恢复、
          // hasSuspendedRun、挂起态 free-text 续接链路会丢 activeRun。
          setActiveRun(runThreadId, runId, 'waiting_user');
        } else {
          clearActiveRun(runThreadId, runId, completedSuccessfully);
          // 真正终态（非挂起、非断流交接）：任务模式归位 + 派发下一条排队消息（§A/§B）
          onRunSettled(runThreadId, runId, terminalOutcome);
        }
      } else if (!runThreadId) {
        refreshRunUi(completedSuccessfully);
      }
      loadThreads();
    }
    return !rejectedBeforeStart;
  }

  /** @param opts.invertFollowUp 本条取反跟进行为（⇧⌘Enter）：queue↔steer，只影响这一条
   *  @param opts.forceSteer 无视排队模式，把当前草稿注入当前 Run */
  async function sendChat(opts?: { invertFollowUp?: boolean; forceSteer?: boolean; interviewInput?: InterviewInput }) {
    if (uploadingWorkFolder.value) {
      options.showNotice('工作文件夹的素材正在上传，完成后即可发送');
      return;
    }
    const interviewTurn = assistantPreset.value === 'interview';
    if (interviewTurn && (chatLoading.value || stopInFlight)) {
      options.showNotice('正在处理本轮面试，请稍候再作答');
      return;
    }
    const interviewInput = interviewTurn ? opts?.interviewInput || interviewInputProvider?.(chatInput.value) : undefined;
    if (interviewTurn && !interviewInput) {
      options.showNotice('请先完成面试设置，或等待面试状态加载后再作答');
      return;
    }
    const forceSteer = Boolean(opts?.forceSteer);
    const sendPolicy = getBuiltinUiPolicy(assistantPreset.value);
    if (sendPolicy?.hideSkillSelector) selectedSkills.value = [];
    if (sendPolicy?.hideSubagent) selectedSubagent.value = undefined;
    if (sendPolicy?.hideKnowledge) selectedKnowledgeList.value = [];
    if (sendPolicy?.hideFiles) {
      selectedFileList.value = [];
      pendingAttachments.value = interviewInput?.action === 'start' ? pendingAttachments.value : sendPolicy.imageOnlyUpload
        ? pendingAttachments.value.filter((item) => item.kind === 'image')
        : [];
    }
    if (sendPolicy?.hideThreads) selectedThreadList.value = [];
    if (sendPolicy?.hideWebSearchToggle) webSearchOn.value = false;
    if (sendPolicy?.hidePlanMode) planMode.value = false;
    if (sendPolicy?.hideResearch) researchProfile.value = false;
    const followUpIntent: 'steer' | 'queue' = forceSteer
      ? 'steer'
      : resolveFollowUpIntent(
          followUpMode.value,
          opts?.invertFollowUp,
        );
    const content = chatInput.value.trim();
    // 任务模式开着但没描述任务：拦一下，不建图（§A）
    if (planMode.value && !content) {
      options.showNotice('请描述要完成的任务');
      return;
    }
    if (!content && !hasComposerResources()) return;

    // 附件还在上传/解析中：先别发——否则文档拿不到解析文本会「空手」送出。
    // 保留已输入内容与占位卡，提示用户稍候（转圈撤掉即可再发）。
    // **必须早于下面的计划卡作废**：这里 return 时消息根本没发出去，若先把计划卡打成
    // superseded 并 clearActiveRun，用户就会看到一张失去「开始执行/编辑/跳过」按钮的
    // 废卡、而后端 Run 仍停在 waiting_confirmation——只能刷新页面才能救回。
    if (pendingAttachments.value.some((a) => a.uploading)) {
      options.showNotice('文件正在处理中，请稍候…');
      return;
    }
    if (pendingAttachments.value.some((a) => a.needsReupload)) {
      options.showNotice('刷新后附件内容已失效，请移除并重新上传后再发送');
      return;
    }
    // 中断后队列被暂停、用户却又发了新消息：先问清那几条排队消息怎么办（Codex 同款闸）。
    // 必须早于任何状态改写——用户点「取消」时草稿要原样留在输入框。
    // 条件先同步判一次：绝大多数发送不该为这道闸多吃一个 await（会改变既有时序）。
    if (queuePaused.value && messageQueue.value.length) {
      if (!(await confirmPausedQueueSubmit())) return;
    }

    // 运行中回车默认入队；点「调整方向」或 ⇧⌘Enter 取反才注入当前 Run。
    const active = currentThreadId.value ? activeRuns.value[currentThreadId.value] : undefined;
    const activeStatus = active?.status;
    const replaceIntent = !forceSteer && REPLACE_INTENT_RE.test(content);
    // 用户明确说“暂停”时就是请求取消当前 Run，不伪造可恢复的节点暂停。
    if (!forceSteer && active?.id && isGeneratingRunStatus(activeStatus) && PAUSE_INTENT_RE.test(content)
        && !pendingAttachments.value.length) {
      chatInput.value = '';
      await stopChatByUser();
      return;
    }
    // 显式取「排队」意图（跟进行为=排队，或 ⇧⌘Enter 单条取反）优先于关键词猜测：
    // 用户按了明确的键，就不该再让「新任务/顺便」这类词表来改判。
    // 显式选了委托目标时不能降格为普通“引导”：活动 Run 的工具目录已经冻结，
    // instruct 接口也没有 subagent_id。排入下一轮，才能让 /chat 做实时权限校验并明确委托。
    if (!forceSteer && active?.id && isGeneratingRunStatus(activeStatus) && selectedSubagent.value) {
      await queueCurrentMessage();
      return;
    }
    if (!forceSteer && active?.id && isGeneratingRunStatus(activeStatus) && followUpIntent === 'queue') {
      await queueCurrentMessage();
      return;
    }
    if (!forceSteer && active?.id && isGeneratingRunStatus(activeStatus) && NEW_TASK_INTENT_RE.test(content)) {
      await queueCurrentMessage();
      return;
    }
    if (active?.id && isGeneratingRunStatus(activeStatus) && replaceIntent) {
      await stopChat();
      const stillActive = currentThreadId.value
        ? activeRuns.value[currentThreadId.value]?.id === active.id
        : false;
      if (stillActive) return;
    }
    if (!forceSteer && active?.id && isGeneratingRunStatus(activeStatus) && !replaceIntent
        && CANCEL_INTENT_RE.test(content)
        && !pendingAttachments.value.length) {
      chatInput.value = '';
      await stopChatByUser();
      return;
    }
    if (active?.id && isGeneratingRunStatus(activeStatus) && !replaceIntent) {
      await submitActiveRunInput();
      return;
    }

    // 生产：生成中但 activeRun 尚未登记（run.started 前窗口）时，
    // 默认入队；⌘Enter / forceSteer 才短等 active 后注入当前 Run。
    if ((chatLoading.value || stopInFlight) && !replaceIntent) {
      if (followUpIntent === 'queue') {
        await queueCurrentMessage();
        return;
      }
      const tidBoot = currentThreadId.value;
      for (let i = 0; i < 30; i += 1) {
        const boot = tidBoot ? activeRuns.value[tidBoot] : undefined;
        if (boot?.id && isGeneratingRunStatus(boot.status)) {
          await submitActiveRunInput();
          return;
        }
        if (!chatLoading.value && !stopInFlight) break;
        await new Promise((r) => setTimeout(r, 100));
      }
      const boot2 = tidBoot ? activeRuns.value[tidBoot] : undefined;
      if (boot2?.id && isGeneratingRunStatus(boot2.status)) {
        await submitActiveRunInput();
        return;
      }
      if (chatLoading.value || stopInFlight) {
        if (tidBoot) {
          await queueCurrentMessage();
          return;
        }
        options.showNotice('上一条正在启动，请稍候再发送');
        return;
      }
    }

    // 计划审查卡挂起时，输入框发送=对该卡的自由修订（卡内补充框或底栏回车），
    // 走 resume 而不是新开一轮；批准词仍由后端 choice_approves_plan 判定。
    // 「取消」不拦截：走 /chat 让后端 _is_cancel_intent 放弃任务。陈旧卡（Run 已不在
    // 本线程 waiting）也不拦截，避免 submitResume 早退把用户打的字吞掉。
    const pendingPlanCard = [...chatMessages.value].reverse().find((item) => (
      item.interactive?.kind === 'plan_confirmation'
      || Boolean(item.interactive?.revision_gate)
    ));
    const planRunId = String(pendingPlanCard?.interactive?.run_id || '');
    const liveActive = currentThreadId.value ? activeRuns.value[currentThreadId.value] : undefined;
    const planRunIsWaiting = Boolean(
      planRunId
      && liveActive?.id === planRunId
      && isWaitingForUserStatus(liveActive?.status)
    );
    if (
      pendingPlanCard
      && content
      && !pendingAttachments.value.length
      && !CANCEL_INTENT_RE.test(content)
      && planRunIsWaiting
    ) {
      const draft = chatInput.value;
      chatInput.value = '';
      let accepted = false;
      try {
        accepted = await submitResume(pendingPlanCard.id, content);
      } catch {
        accepted = false;
      }
      if (!accepted && !chatInput.value) chatInput.value = draft;
      return;
    }

    // Profile 是用户按下发送那一刻的 TurnContext 事实。后面还会等停止收尾、
    // 智能体目录回源等异步操作；若到真正发请求时再读 ref，旧 Run 的延迟终态
    // 可以在窗口内把 planMode 拨回 false，导致胶囊明明开着却落库为 standard。
    const submittedPlanMode = planMode.value;
    const unfinishedResearchRunId = lastUnfinishedResearchRunId();
    const submittedResearchProfile = researchProfile.value || Boolean(unfinishedResearchRunId);

    // 图片带 image_url（原图 data URL）供后端多模态直传 + preview_url（压缩缩略图）随
    // attachments_json 落库供历史回放；文档只带解析文本；status/note（解析置信度）随附件
    // 回传——后端据此下发降级提示并持久化附件元数据
    const queuedAttachments = [...pendingAttachments.value];
    const delegatedSubagent = sendPolicy?.hideSubagent ? undefined : selectedSubagent.value;
    const atts: TurnAttachment[] = queuedAttachments.map((a) => ({
      filename: a.filename,
      text: a.text,
      file_id: a.file_id,
      sha256: a.sha256,
      kind: a.kind,
      image_url: a.previewUrl,
      preview_url: a.thumbUrl,
      status: a.status,
      note: a.note || '',
    }));
    // 消息气泡展示用的附件（图片缩略图 / 文档卡片），不含解析文本
    const displayAtts = queuedAttachments.map((a) => ({
      filename: a.filename,
      kind: a.kind,
      previewUrl: a.previewUrl,
      status: a.status,
      note: a.note,
      file_id: a.file_id,
    }));
    // 「最近的对话」引用也要立刻出卡（2026-07-28 真机走查发现）：服务端会把它写进
    // attachments_json，刷新后气泡上就有这张「对话记录」卡——本地不补的话，发送当时看不见、
    // 刷新之后凭空多出来一张，用户会以为系统偷偷加了东西。文件名口径必须与服务端
    // thread_reference._attachment_filename 一致，否则同一条消息刷新前后卡片名会变。
    const sentFiles = limitFileSelection(selectedFileList.value);
    const sentKnowledge = [...selectedKnowledgeList.value];
    const sentSkills = sendPolicy?.hideSkillSelector ? [] : [...selectedSkills.value];
    const sentThreads = [...selectedThreadList.value];
    const originThreadId = currentThreadId.value;
    const originComposerKey = composerKey();
    const sentDraftStoreKey = draftStoreKey();
    // 先清空输入与选中资源，让输入框即时腾空；选中项已经快照进气泡与本轮请求。
    pendingAttachments.value = [];
    selectedSubagent.value = undefined;
    selectedFileList.value = [];
    selectedKnowledgeList.value = [];
    selectedSkills.value = [];
    selectedThreadList.value = [];
    chatInput.value = '';
    // 发送动作只消费“按下发送那一刻”的草稿。这里立即移除旧快照；之后用户在模型生成中
    // 输入的下一条会形成新记录，旧轮收尾绝不能再按同一个 thread key 无条件删除它。
    conversationDrafts.delete(originComposerKey);
    void deleteDraftRecord(sentDraftStoreKey);
    removeDraftEntryLocal(sentDraftStoreKey);

    // 防御性等待：仅显式 stop 收尾时等待。
    // 禁止因 chatLoading 调用 stopChat——会把用户刚发出去、尚在启动的任务杀掉。
    for (let attempt = 0; stopInFlight && attempt < 5; attempt += 1) {
      await stopInFlight;
    }
    if (chatLoading.value) {
      // 仍在生成：不应创建第二 Run；上方 boot 窗口应已分流。这里再兜一层排队。
      if (currentThreadId.value) {
        // 输入框已被清空，无法 queueCurrentMessage；恢复最小安全提示
        options.showNotice('上一条仍在进行，请稍候或使用排队/引导');
        if (!chatInput.value) chatInput.value = content;
        if (!pendingAttachments.value.length && queuedAttachments.length) {
          pendingAttachments.value = [...queuedAttachments];
        }
        if (!selectedFileList.value.length && sentFiles.length) selectedFileList.value = sentFiles;
        if (!selectedKnowledgeList.value.length && sentKnowledge.length) selectedKnowledgeList.value = sentKnowledge;
        if (!selectedSkills.value.length && sentSkills.length) selectedSkills.value = sentSkills;
        if (!selectedThreadList.value.length && sentThreads.length) selectedThreadList.value = sentThreads;
        if (!selectedSubagent.value && delegatedSubagent) selectedSubagent.value = delegatedSubagent;
        return;
      }
      refreshRunUi(false);
    }

    // 归属校验（审计项 3）：停止收敛的网络窗口里用户可能已切会话——这条消息属于原会话/
    // 原草稿，存回原容器等用户回去再发，绝不发进此刻正看着的另一个会话。
    if (currentThreadId.value !== originThreadId) {
      const prior = conversationDrafts.get(originComposerKey);
      conversationDrafts.set(originComposerKey, {
        ...(prior || { input: '', attachments: [] }),
        input: prior?.input?.trim() ? `${prior.input.replace(/\s+$/, '')}\n${content}` : content,
        attachments: [...(prior?.attachments || []), ...queuedAttachments],
        files: sentFiles,
        knowledge: sentKnowledge,
        skills: sentSkills,
        threads: sentThreads,
      });
      return;
    }

    if (!options.appList.value.length) {
      await options.reloadApps();
      // reloadApps 也是异步窗口：同款归属校验（审计项 3）
      if (currentThreadId.value !== originThreadId) {
        const prior = conversationDrafts.get(originComposerKey);
        conversationDrafts.set(originComposerKey, {
          ...(prior || { input: '', attachments: [] }),
          input: prior?.input?.trim() ? `${prior.input.replace(/\s+$/, '')}\n${content}` : content,
          attachments: [...(prior?.attachments || []), ...queuedAttachments],
          files: sentFiles,
          knowledge: sentKnowledge,
          skills: sentSkills,
          threads: sentThreads,
        });
        return;
      }
    }

    // 顺序与服务端一致：上传附件在前，我的文件 / 对话 / 知识库 / Skill 随后。
    // 选中项发送即消费，气泡上留下对应图标，输入框回到空白。
    const bubbleAtts = composerBubbleAttachments({
      uploads: displayAtts,
      files: sentFiles,
      threads: sentThreads,
      knowledge: sentKnowledge,
      skills: sentSkills,
      subagent: delegatedSubagent,
      webSearch: webSearchOn.value,
    });
    const userMessage: ChatMessage = {
      id: nextLocalId(),
      role: 'user',
      content,
      attachments: bubbleAtts.length ? bubbleAtts : undefined,
      turnSkills: [...sentSkills],
    };
    chatMessages.value.push(userMessage);
    const accepted = await runAssistantTurn(content, {
      interviewInput: interviewInput || undefined,
      attachments: atts.length ? atts : undefined,
      subagentId: delegatedSubagent?.id,
      subagentName: delegatedSubagent?.name,
      planMode: submittedPlanMode,
      researchProfile: submittedResearchProfile,
      resumeSourceRunId: unfinishedResearchRunId,
      draftRequestKey: sentDraftStoreKey,
      userMessageLocalId: userMessage.id,
      contextOverride: {
        skills: sentSkills,
        knowledge: sentKnowledge,
        files: sentFiles,
        threads: sentThreads,
        webSearch: webSearchOn.value,
        model: activeModel.value,
      },
    });
    if (!accepted) {
      // 请求未被服务端接收时，用户的本地气泡与助手失败提示一起保留。此前这里回滚
      // 用户气泡，会把页面退回欢迎页；用户只能猜测消息是否丢失。两者都不入库，
      // 后续重试仍会创建正式消息，草稿与幂等键照旧恢复。
      if (!chatInput.value) chatInput.value = content;
      if (!selectedSubagent.value && delegatedSubagent) selectedSubagent.value = delegatedSubagent;
      const existing = new Set(pendingAttachments.value.map((attachment) => attachment.uid));
      const restore = queuedAttachments.filter((attachment) => !existing.has(attachment.uid));
      if (restore.length) pendingAttachments.value = [...restore, ...pendingAttachments.value];
      if (!selectedFileList.value.length && sentFiles.length) selectedFileList.value = sentFiles;
      if (!selectedKnowledgeList.value.length && sentKnowledge.length) selectedKnowledgeList.value = sentKnowledge;
      if (!selectedSkills.value.length && sentSkills.length) selectedSkills.value = sentSkills;
      if (!selectedThreadList.value.length && sentThreads.length) selectedThreadList.value = sentThreads;
      syncUploadingFlag();
    }
  }

  function readImagePreview(file: File): Promise<string | undefined> {
    if (!file.type.startsWith('image/')) return Promise.resolve(undefined);
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : undefined);
      reader.onerror = () => resolve(undefined);
      reader.readAsDataURL(file);
    });
  }

  // 落库缩略图（图片持久化修复）：原图 data URL 动辄数 MB 不能进 DB，canvas 降采样成
  // ≤640px JPEG（典型 30–80KB），随消息作为 preview_url 存进 attachments_json——
  // 刷新/隔天回到会话，图片卡不再退化成文件名卡。与后端 _PREVIEW_URL_MAX_CHARS 对齐：
  // 超 200k 字符（异常巨图）放弃缩略图，只保元数据卡，不拦发送。
  const THUMB_MAX_EDGE = 640;
  function makeImageThumbnail(dataUrl: string): Promise<string | undefined> {
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        try {
          const scale = Math.min(1, THUMB_MAX_EDGE / Math.max(img.naturalWidth || 1, img.naturalHeight || 1));
          const width = Math.max(1, Math.round((img.naturalWidth || 1) * scale));
          const height = Math.max(1, Math.round((img.naturalHeight || 1) * scale));
          const canvas = document.createElement('canvas');
          canvas.width = width;
          canvas.height = height;
          const ctx = canvas.getContext('2d');
          if (!ctx) return resolve(undefined);
          // JPEG 无透明通道：PNG 透明区域先铺白底，不然导出成黑块
          ctx.fillStyle = '#fff';
          ctx.fillRect(0, 0, width, height);
          ctx.drawImage(img, 0, 0, width, height);
          const thumb = canvas.toDataURL('image/jpeg', 0.8);
          resolve(thumb.length <= 200_000 ? thumb : undefined);
        } catch {
          resolve(undefined);
        }
      };
      img.onerror = () => resolve(undefined);
      img.src = dataUrl;
    });
  }

  const MAX_ATTACHMENTS = 10;
  const MAX_FILE_MB = 15; // 与后端 /chat/upload 的 15MB 硬上限一致（超出后端返回 413）

  async function mirrorAttachmentToWorkspace(file: File) {
    const threadId = currentThreadId.value;
    if (!threadId) return;
    try {
      await uploadWorkspaceFile(threadId, file);
      window.dispatchEvent(new CustomEvent('center-workspace-changed', { detail: { threadId } }));
    } catch {
      // 输入框附件仍可用；发送时后端会再拷进工作区
    }
  }

  const syncUploadingFlag = () => {
    uploadingFile.value = pendingAttachments.value.some((a) => a.uploading);
  };

  async function uploadFile(file: File) {
    if (getBuiltinUiPolicy(assistantPreset.value)?.imageOnlyUpload && !isChatImageFile(file)) {
      options.showError('校园百事通只支持添加图片；文档、知识库和我的文件请在主对话中使用');
      return;
    }
    const formatError = chatUploadFileError(file);
    if (formatError) {
      options.showError(formatError);
      return;
    }
    // 客户端护栏：先挡住超量/超大文件，避免无谓的整包上传打到后端
    if (pendingAttachments.value.length >= MAX_ATTACHMENTS) {
      options.showError(`最多添加 ${MAX_ATTACHMENTS} 个附件`);
      return;
    }
    if (file.size > MAX_FILE_MB * 1024 * 1024) {
      options.showError(`「${file.name}」超过 ${MAX_FILE_MB}MB 上限，请压缩后再上传`);
      return;
    }
    // 先本地读预览（图片缩略图秒出；非图片为 undefined，走文件轮廓卡），再**立即**入列占位卡
    // ——不再等后端 OCR（数秒）才显示，观感即时。占位卡带 uploading=true → 卡上盖转圈。
    const originKey = composerKey();
    const previewUrl = await readImagePreview(file);
    const thumbUrl = previewUrl ? await makeImageThumbnail(previewUrl) : undefined;
    const uid = nextLocalId();
    const isImage = !!previewUrl;
    // 归属校验（审计项 7）：预处理是异步的，期间用户可能已切会话——占位卡写回原会话的
    // 草稿容器，不注入当前 composer；上传完成回填本就按 uid 跨容器定位。
    const targetList = composerKey() === originKey
      ? pendingAttachments.value
      : draftAttachmentsFor(originKey);
    // 并发上限复检（审计项 31）：多文件并行上传各自都过了入口检查——占位卡入列前再查一次，
    // 不让并行预处理突破 10 个上限
    if (targetList.length >= MAX_ATTACHMENTS) {
      options.showError(`最多添加 ${MAX_ATTACHMENTS} 个附件`);
      return;
    }
    targetList.push({
      uid,
      filename: file.name,
      kind: isImage ? 'image' : 'file',
      text: '',
      chars: 0,
      truncated: false,
      previewUrl,
      thumbUrl,
      uploading: true,
    });
    syncUploadingFlag();
    try {
      // 后台上传+解析：完成后就地回填对应占位卡（保留本地预览与 uid），撤掉转圈。
      // rawFile 留在内存里：解析失败/不完整时「重试」可用同一句柄重新上传（不序列化）。
      // 回填按 uid 跨「当前 composer + 会话草稿」定位——上传期间用户切了会话，占位卡
      // 已随草稿暂存，不跨容器找就会留一张永远转圈的卡。
      const uploaded = await uploadChatFile(file, activeModel.value);
      await mirrorAttachmentToWorkspace(file);
      const container = findAttachmentContainer(uid);
      const idx = container ? container.findIndex((a) => a.uid === uid) : -1;
      if (container && idx !== -1) {
        container[idx] = {
          ...uploaded,
          uid,
          previewUrl: previewUrl ?? uploaded.previewUrl,
          thumbUrl: thumbUrl ?? uploaded.thumbUrl,
          uploading: false,
          rawFile: file,
        };
        if (uploaded.status === 'failed') {
          options.showNotice(`「${file.name}」解析失败（${uploaded.note || '未提取出内容'}），可点击附件卡重试`);
        }
      }
    } catch (error) {
      // 失败即移除占位卡（不留一个永远转不出来的圈）+ 提示
      removeAttachmentByUid(uid);
      options.showError(error);
    } finally {
      syncUploadingFlag();
    }
  }

  /** 按 uid 从其所在容器移除附件（当前 composer 用重赋值触发响应；草稿数组就地 splice）。 */
  function removeAttachmentByUid(uid?: number) {
    if (uid == null) return;
    if (pendingAttachments.value.some((a) => a.uid === uid)) {
      pendingAttachments.value = pendingAttachments.value.filter((a) => a.uid !== uid);
      return;
    }
    for (const draft of conversationDrafts.values()) {
      const idx = draft.attachments.findIndex((a) => a.uid === uid);
      if (idx !== -1) {
        draft.attachments.splice(idx, 1);
        return;
      }
    }
  }

  /** 解析失败/不完整的附件重试：用留存的 File 句柄重新走上传+解析，就地回填同一张卡。
      完成回填同 uploadFile：按 uid 跨容器定位（重试期间用户可能切了会话）。 */
  async function retryAttachment(index: number) {
    const att = pendingAttachments.value[index];
    if (!att || att.uploading) return;
    if (!att.rawFile) {
      options.showNotice('刷新后无法读取原文件，请移除该附件并重新上传');
      return;
    }
    const uid = att.uid;
    pendingAttachments.value[index] = { ...att, uploading: true };
    uploadingFile.value = true;
    try {
      const uploaded = await uploadChatFile(att.rawFile, activeModel.value);
      await mirrorAttachmentToWorkspace(att.rawFile);
      const container = findAttachmentContainer(uid);
      const idx = container ? container.findIndex((a) => a.uid === uid) : -1;
      if (container && idx !== -1) {
        container[idx] = {
          ...uploaded,
          uid,
          previewUrl: att.previewUrl ?? uploaded.previewUrl,
          thumbUrl: att.thumbUrl ?? uploaded.thumbUrl,
          uploading: false,
          rawFile: att.rawFile,
        };
        if (uploaded.status !== 'failed') options.showNotice(`「${att.filename}」已重新解析`);
      }
    } catch (error) {
      const container = findAttachmentContainer(uid);
      const idx = container ? container.findIndex((a) => a.uid === uid) : -1;
      if (container && idx !== -1) container[idx] = { ...att, uploading: false };
      options.showError(error);
    } finally {
      syncUploadingFlag();
    }
  }

  function removeAttachment(index: number) {
    pendingAttachments.value.splice(index, 1);
    // 移除的可能是上传中的卡：重算标志，别让纸夹按钮/发送禁用卡在 spinner
    syncUploadingFlag();
  }

  /** Stop the current thread's background generation; already-streamed text is kept in place. */
  /** 用户点「停止」：先暂停队列派发，再走正常停止流程。
   *  与 sendChat 内部的打断-重发（那里也调 stopChat）区分开——那是「发新消息」，不该暂停队列。 */
  async function stopChatByUser() {
    if (messageQueue.value.length) setQueuePaused(currentThreadId.value, true);
    return stopChat();
  }

  // pauseActiveRun / continuePausedRun 已删（2026-07-27）：后端「安全边界暂停」链随 graph
  // Runtime 一起删除——is_pause_requested 全仓零消费者，pause 只写一个没人读的标志位。
  // 用户点停止看到「将在当前步骤安全收尾后暂停」、按钮锁 60 秒，Run 却一路跑到底。
  // 主循环没有节点级检查点，「可恢复的暂停」这个概念本身已不存在，停止即真停止。

  // 队列暂停期间又发新消息（Codex composer.pausedQueueSubmit 对齐 2026-07-26）：
  // 暂停中的队列是用户「改主意了」的产物，直接发新消息会让那几条排队消息处境不明——
  // 要么稍后突然连发、要么被静默丢掉。Codex 在这里挡一道，让用户明确决定清不清。
  const pausedQueuePrompt = ref<{ count: number } | null>(null);
  let resolvePausedQueue: ((action: 'send' | 'clear' | 'cancel') => void) | null = null;
  function answerPausedQueuePrompt(action: 'send' | 'clear' | 'cancel') {
    pausedQueuePrompt.value = null;
    const resolve = resolvePausedQueue;
    resolvePausedQueue = null;
    resolve?.(action);
  }
  /** 返回 true=继续发送；false=用户取消本次发送（草稿原样留在输入框） */
  async function confirmPausedQueueSubmit(): Promise<boolean> {
    const count = messageQueue.value.length;
    if (!queuePaused.value || !count) return true;
    // 已有一个待答弹窗（连击回车）：不叠第二个，本次发送直接放弃
    if (pausedQueuePrompt.value) {
      options.showNotice('请先处理已打开的待发送队列确认框');
      return false;
    }
    pausedQueuePrompt.value = { count };
    const action = await new Promise<'send' | 'clear' | 'cancel'>((resolve) => {
      resolvePausedQueue = resolve;
    });
    if (action === 'cancel') return false;
    if (action === 'clear') {
      // 逐条删（服务端队列是权威源）；删不掉的如实留下，不假装清空了
      const ids = messageQueue.value.map((entry) => entry.id);
      let failed = 0;
      for (const id of ids) {
        try {
          await deleteChatQueueItem(id);
        } catch (error) {
          failed += 1;
          console.warn('Failed to clear paused queue item:', error);
        }
      }
      messageQueue.value = [];
      // 服务端队列是权威源：删完回读一次，删失败的会重新出现在候车区（不假装清空了）
      await loadMessageQueue(currentThreadId.value || '');
      if (failed) options.showNotice(`有 ${failed} 条排队消息未能删除，仍留在待发送队列里`);
    }
    // 清空与保留都解除暂停：用户已经就这批消息做过决定，不该再挂着「已暂停」横幅
    setQueuePaused(currentThreadId.value || '', false);
    return true;
  }

  /** 队列暂停后用户点「继续」：解除暂停并立刻尝试派发队首。 */
  function resumeQueue() {
    const threadId = currentThreadId.value;
    setQueuePaused(threadId, false);
    if (threadId) void dispatchNextQueuedItem(threadId);
  }

  async function stopChat() {
    // 已有一次停止流程正在等待后端取消确认：复用同一个 promise，而不是重新执行一遍（会重复
    // 发 cancelChatRun）；sendChat 的打断互斥也依赖这个引用在整个停止流程期间保持非空（P0-4）。
    if (stopInFlight) return stopInFlight;
    const task = (async () => {
      const threadId = currentThreadId.value;
      const active = threadId ? activeRuns.value[threadId] : undefined;
      // 立即停打字机：模型整段早已缓冲、流已结束但还在逐字动画时，controller.abort 已无效，
      // 必须显式停这个打字机，否则旧回复会继续吐字（发新消息打断/点停止都要即时定格）。
      activeTypewriter?.stop();
      activeCommentaryStream?.stop();
      activeReasoningStream?.stop();
      // 执行卡「已停止」视觉延后到服务端确认（第三批 P1-3）：点停止的即时反馈只保留
      // 按钮态/断流/停打字机；卡片状态等 cancel 真正确认——失败/pending 时旧任务仍在跑，
      // 提前定格会出现「已停止」却继续吐内容的自相矛盾。先捕获目标消息（确认回来时
      // 用户可能已切会话、消息列表已换，届时不乱标）。
      const cancelTarget = [...chatMessages.value].reverse().find((m) => m.role === 'assistant');
      const markCancelledLocally = () => {
        if (cancelTarget && !cancelTarget.runCompletedAt) markRunCancelled(cancelTarget);
      };
      if (active?.id) {
        // 停止失败/pending 恢复用（P0-3）：先快照已收游标——clearActiveRun 会把游标一并清掉
        const cursor = runStreamCursors.get(active.id);
        // 先本地断流（立即停住打字机），再通知后端取消——取消请求的网络往返不该拖慢停止手感
        runControllers.get(active.id)?.abort();
        runControllers.delete(active.id);
        clearActiveRun(threadId, active.id);
        // 登记停止请求（在 clearActiveRun 之后加，clearActiveRun 会顺手清这个集合）：
        // pending/失败恢复的订阅收到 run.failed(已停止生成) 时据此按「已停止」收尾不标红
        stopRequestedRuns.add(active.id);
        // 停止中禁用态（第四批 2a）：pending 期间保持，直到订阅侧终态落定（clearActiveRun）解除
        stoppingRunId.value = active.id;
        stoppingThreadId.value = threadId || '';
        /** pending/失败共用恢复：activeRun 复位 + 按已收游标重订阅（等真正收尾帧再终态收尾） */
        const restoreRunAndResubscribe = (status: string) => {
          finishedRunIds.delete(active.id); // 撤销 clearActiveRun 的「已结束」登记，loadThreads 仍可恢复它
          setActiveRun(threadId, active.id, status);
          void subscribeVisibleRun(
            threadId,
            active.id,
            cursor
              ? {
              afterSequence: cursor.seq,
              initialContent: cursor.content,
              reuseMessageId: cursor.messageId,
              contentOffset: cursor.contentOffset,
            }
              : undefined,
          );
        };
        try {
          // 服务端确认语义（第三批契约）：status=cancelled＝pump 已收敛落库，才按「已取消」收尾
          const res = await cancelChatRun(active.id);
          if (res?.status === 'pending') {
            // P0-2（第三批）：停止已发出但 5s 内执行未收敛——旧任务可能仍在跑。不终态收尾：
            // 不清 activeRun（恢复）、不派发队列、执行卡不标「已停止」；恢复订阅等 pump 真正
            // 收敛后的 run.failed(已停止生成) 帧到达，由订阅通道按「已停止」视觉正常终态收尾。
            restoreRunAndResubscribe(active.status || 'running');
            options.showNotice('任务正在停止…');
            return;
          }
          if (res?.status === 'completed' || res?.status === 'failed') {
            // 终态先到（审计项 22）：Run 在停止请求前已自行结束——按真实终态定格视觉，
            // 不再把 completed/failed 冒充成「已停止」（用户会误以为回答没完成/被打断）
            stopRequestedRuns.delete(active.id);
            clearStoppingState(active.id);
            if (cancelTarget && !cancelTarget.runCompletedAt) {
              if (res.status === 'failed') markRunFailed(cancelTarget);
              else markRunCompleted(cancelTarget);
            }
            onRunSettled(threadId, active.id, res.status);
            return;
          }
        } catch {
          // P0-3：停止请求没送达/失败——旧 Run 很可能仍在服务器跑，不能本地强判已取消
          // （此时派发下一条会与它冲突，被后端 R0 拒绝）。权威终态接口仲裁一次（旧后端
          // 404 时自动退回线程级活动 Run 判断）：
          // 已终态（终态先到）→ 按真实终态定格视觉、照常收尾，停止失败结果忽略；
          // 仍在跑/等用户（或状态查不到=网络不通，保守按仍在跑）→ 恢复 activeRun + 游标重订阅。
          const verdict = await arbitrateRunTerminalState(threadId, active.id);
          if (verdict.kind === 'ended') {
            stopRequestedRuns.delete(active.id);
            clearStoppingState(active.id);
            if (cancelTarget && !cancelTarget.runCompletedAt) {
              if (verdict.outcome === 'failed') markRunFailed(cancelTarget);
              else if (verdict.outcome === 'cancelled') markRunCancelled(cancelTarget);
              else if (verdict.outcome === 'completed') markRunCompleted(cancelTarget);
              // outcome=null（旧后端只知道「已结束」）：不猜终态，保持现状等刷新回放
            }
            onRunSettled(threadId, active.id, verdict.outcome); // 终态先到：按终态收尾，忽略停止失败
            return;
          }
          // 停止请求没送达（第五批项 2①）：撤销「用户已请求停止」标记——保留它会把之后
          // 任何真实 run.failed（模型/工具错误）误染成「已停止」视觉，把错误吞掉
          stopRequestedRuns.delete(active.id);
          restoreRunAndResubscribe(
            verdict.kind === 'waiting' ? verdict.status : (active.status || 'running'),
          );
          clearStoppingState(active.id); // 恢复失败解除禁用：用户可再点停止重试
          options.showNotice('停止请求未送达，任务仍在进行');
          return;
        }
        // 停止＝取消整个 Run 且服务端已确认收敛，是真正的终态：现在才定格执行卡「已停止」，
        // 再任务模式归位 + 派发下一条排队消息（§A/§B）
        stopRequestedRuns.delete(active.id);
        clearStoppingState(active.id);
        markCancelledLocally();
        onRunSettled(threadId, active.id, 'cancelled');
        return;
      }
      if (abortController) {
        // 幽灵 Run 清扫（审计项 8）：run.started 未到就点停——后端可能已建 Run 并在后台
        // 继续。凭本轮幂等键反查（短重试兜 create_run 落库时延），查到就补 cancel。
        // fire-and-forget：清扫失败无害（Run 可能确实没建成/已自行结束）。
        const ghostRequestId = activeClientRequestId;
        abortController.abort();
        markCancelledLocally();
        // run.started 尚未到达（activeRuns 还没登记）就被停止：runAssistantTurn 的 finally
        // 走 detachedOnly 分支不会重算 chatLoading，这里必须自己收——否则 sendChat 的
        // 打断等待循环会对着恒真的 chatLoading 无限空转，微任务链饿死事件循环（P0-4 残余）
        refreshRunUi(false);
        if (ghostRequestId) {
          void (async () => {
            for (let attempt = 0; attempt < 3; attempt += 1) {
              if (attempt > 0) await new Promise((resolve) => window.setTimeout(resolve, 700));
              let found;
              try {
                found = await getRunByClientRequest(ghostRequestId);
              } catch {
                continue; // 查询服务暂不可用：下一次重试，不制造未处理 Promise rejection
              }
              if (found.kind === 'found') {
                try {
                  await cancelChatRun(found.run_id);
                } catch {
                  // Run 可能已自行结束/已被取消：忽略
                }
                return;
              }
            }
          })();
        }
      }
    })();
    // 同步部分（abort/markCancelledLocally）已在上面 IIFE 调用时跑完，UI 已即时定格；
    // stopInFlight 只在 await cancelChatRun 的网络窗口内保持非空，供 sendChat 排队等待。
    stopInFlight = task.finally(() => {
      stopInFlight = null;
    });
    return stopInFlight;
  }

  /** HITL：提交挂起子智能体的表单/选项，续接执行（§10.4）。
   *  返回 true=请求已被接手（令牌已消费或流已开）；false=早退或回滚，调用方应还原草稿。 */
  async function submitResume(messageId: number, resumeValue: unknown): Promise<boolean> {
    const paused = chatMessages.value.find((item) => item.id === messageId);
    const runId = paused?.interactive?.run_id;
    const resumeId = paused?.interactive?.resume_id;
    const active = currentThreadId.value ? activeRuns.value[currentThreadId.value] : undefined;
    if (!runId || (chatLoading.value && active?.id !== runId)) return false;
    const viewToken = viewVersion;
    let runThreadId = currentThreadId.value;
    let stillWaiting = false;
    let completedSuccessfully = false;
    let hadError = false;
    // 断流交接（整合路线图 #2）：传输层断流时 Run 仍在后端续跑，不许误判终态
    let handedOff = false;
    // 语义收口（P0-2，对齐 runAssistantTurn）：resume 流的 [DONE]/EOF 同样只是传输结束，
    // 未见业务终态/挂起的裸 EOF 按已收游标交接订阅通道续写原消息，不能按旧语义收尾清 activeRun。
    let sawTerminal = false;
    let terminalOutcome: 'completed' | 'partial' | 'failed' | 'cancelled' | null = null;
    let lastSeq = 0;
    let streamContent = '';
    // 乐观隐藏失败回滚（第四批项 1/第五批修正）：resume 令牌未被消费时按提交前快照恢复
    // 卡片与挂起态，用户可原地重试。requestAccepted 由 input.accepted 帧（令牌 CAS 消费
    // 成功的权威信号）置位——2xx 响应头到达≠令牌已消费（令牌在后台生成器里才 CAS），
    // 「响应头已到、令牌未消费」窗口断连仍必须回滚；lastSeq>0 双保险（任何带 sequence
    // 的业务帧必然晚于 CAS）。
    let requestAccepted = false;
    let rolledBack = false;
    const interactiveSnapshot = paused?.interactive || null;
    const priorRunStatus = runThreadId ? activeRuns.value[runThreadId]?.status : undefined;
    // 消歧提问卡（ask_user）：续接写回原消息——一轮=一条消息，执行时间线/计时跨挂起连续，
    // 不留「只剩执行头的空壳」。子智能体 HITL 保持既有两条消息形态（chip 跨消息收尾逻辑依赖它）。
    // 计划确认除外：对齐 Codex——计划卡留下，用户气泡（执行/补充/跳过）另起，助手再开一轮。
    const isPlanConfirm = interactiveSnapshot?.kind === 'plan_confirmation'
      || Boolean(interactiveSnapshot?.revision_gate);
    const mergeIntoPaused = Boolean(interactiveSnapshot?.ask_user) && !isPlanConfirm;
    const planResumeText = isPlanConfirm ? visiblePlanResumeText(resumeValue) : '';
    const unlockPlanExecution = isPlanConfirm && PLAN_EXECUTE_RESUME_RE.test(planResumeText);
    if (unlockPlanExecution) {
      planMode.value = false;
      if (runId) {
        unlockedPlanRuns.add(runId);
        planProfileRuns.delete(runId);
      }
    }
    // 提交答案瞬间收口「正在请求补充信息…」：旧后端 suspend 不发 tool.completed，
    // 若不在这里收口，答完后该行会一直闪到整轮结束。
    if ((mergeIntoPaused || isPlanConfirm) && paused) settleHitlToolSteps(paused as any);
    if (paused) paused.interactive = null;
    let planUserMessageId: number | null = null;
    if (planResumeText) {
      const planUserMessage: ChatMessage = {
        id: nextLocalId(),
        role: 'user',
        content: planResumeText,
      };
      planUserMessageId = planUserMessage.id;
      chatMessages.value.push(planUserMessage);
    }

    // waiting Run 可能仍有一条可见会话订阅占着：它在 input.required 后若连接尚未退出，
    // resumeChatTurn 又会为同一 Run 再开一条事件订阅。两条订阅分别写原消息和续接消息，
    // 真机表现就是预检正文/选择卡各出现两份，终态后旧卡还能继续点。提交恢复命令前先
    // 关闭本会话对该 Run 的旧观察流；后端 Run 不受 abort 影响，新的 resume 订阅会从
    // POST 返回的权威 after 游标继续接收。
    detachRunObserversBeforeResume(runId);

    chatLoading.value = true;
    clearChatTaskTimer();
    chatTaskState.value = 'running';
    // 续接占位即刻可见（2026-07-22 真机：提交撞上服务闪断时页面空白十几秒）：
    // 本地先种 runStartedAt，服务端 run.started 到达后再校准。
    const contMessage: ChatMessage =
      mergeIntoPaused && paused ? paused : {
        id: nextLocalId(), role: 'assistant', content: '',
        runId,
        runStartedAt: Date.now(),
        agentMode: paused?.agentMode,
        planExecutionUnlocked: unlockPlanExecution || undefined,
      };
    contMessage.runId = runId;
    ensureRunningThought(contMessage);
    let continuationTargetId = contMessage.id;
    let segmentContentOffset = 0;
    let typewriter = createTypewriter(continuationTargetId);
    let commentaryStream: CommentaryStream;
    let reasoningStream: ReasoningStream;
    // 同 runAssistantTurn：登记为当前活跃打字机，停止时才能立刻停住「缓冲已收完、
    // 动画还在逐字播放」的续接文字（不登记则点停止后残余文字继续打完）
    activeTypewriter = typewriter;
    if (contMessage !== paused) chatMessages.value.push(contMessage);
    const controller = new AbortController();
    abortController = controller;
    if (runThreadId) setActiveRun(runThreadId, runId, 'running');
    runControllers.set(runId, controller);

    const updateContinuation = (fn: (message: ChatMessage) => void) => {
      if (runThreadId && currentThreadId.value !== runThreadId) return;
      const target = chatMessages.value.find((item) => item.id === continuationTargetId);
      if (target) fn(target);
    };
    commentaryStream = createCommentaryStream(updateContinuation);
    reasoningStream = createReasoningStream(updateContinuation);
    activeCommentaryStream = commentaryStream;
    activeReasoningStream = reasoningStream;
    const resumeSegmentController: RunSegmentController = {
      split: () => {
        typewriter.stop();
        commentaryStream.stop();
        reasoningStream.stop();
        const previous = chatMessages.value.find((item) => item.id === continuationTargetId);
        const endedAt = Date.now();
        if (previous) {
          previous.executionSegmentEndedAt = endedAt;
          previous.executionSegmentEndSequence = lastSeq || undefined;
          // 不再给前一段落终态时长：插话不结束这一轮（Codex 契约=整轮一个执行头）。
          // 时长由本轮末段在收尾时写，首段的头按整轮口径显示。
          sealSegmentSteps(previous);
        }
        const nextSegment: ChatMessage = {
          id: nextLocalId(),
          role: 'assistant',
          content: '',
          runId,
          executionSegmentIndex: (previous?.executionSegmentIndex ?? 0) + 1,
          executionSegmentStartSequence: lastSeq ? lastSeq + 1 : undefined,
          // 继承整轮起点：本轮末段的 runDurationMs 因此覆盖「插话前+插话后」的完整时长，
          // 首段的执行头照它显示「工作过程 · 整轮时长」，不会被插话截成两段计时。
          runStartedAt: previous?.runStartedAt || endedAt,
          agentMode: previous?.agentMode,
          planExecutionUnlocked: previous?.planExecutionUnlocked,
        };
        // 切点吸附块边界 + 前一段就地定稿（同 runAssistantTurn 的 split）
        const cut = segmentSplitOffset(streamContent, segmentContentOffset);
        const sealedContent = stripRecommendMark(streamContent.slice(segmentContentOffset, cut));
        if (previous) {
          const idx = chatMessages.value.findIndex((item) => item.id === previous.id);
          if (idx >= 0) {
            chatMessages.value[idx] = { ...chatMessages.value[idx], content: sealedContent };
          }
        }
        chatMessages.value.push(nextSegment);
        continuationTargetId = nextSegment.id;
        segmentContentOffset = cut;
        typewriter = createTypewriter(continuationTargetId);
        commentaryStream = createCommentaryStream(updateContinuation);
        reasoningStream = createReasoningStream(updateContinuation);
        activeTypewriter = typewriter;
        activeCommentaryStream = commentaryStream;
        activeReasoningStream = reasoningStream;
        trackRunCursor(runId, continuationTargetId, { contentOffset: segmentContentOffset });
        return continuationTargetId;
      },
    };
    runSegmentControllers.set(runId, resumeSegmentController);

    try {
      const answer = await resumeChatTurn({
        run_id: runId,
        resume_value: resumeValue,
        kind: interactiveSnapshot?.kind === 'plan_confirmation'
          || Boolean(interactiveSnapshot?.revision_gate)
          ? 'plan_confirmation'
          : 'clarification',
        resume_id: resumeId,
        signal: controller.signal,
        onRunStarted: (payload) => {
          runThreadId = payload.thread_id || runThreadId;
          if (runThreadId && viewVersion === viewToken && currentThreadId.value === runThreadId) {
            currentThreadId.value = runThreadId;
          }
          setActiveRun(runThreadId, payload.run_id || runId, payload.status || 'running', {
            model: payload.model || activeRuns.value[runThreadId]?.model,
          });
          if (payload.agent_mode === 'plan' && (payload.run_id || runId) && !unlockPlanExecution) {
            registerPlanProfileRun(runThreadId, payload.run_id || runId); // 后端权威点灯（批次6）
          }
          if (payload.agent_mode === 'research' && (payload.run_id || runId)) {
            registerResearchProfileRun(runThreadId, payload.run_id || runId);
          }
          updateContinuation((target) => {
            target.agentMode = payload.agent_mode;
            markRunStarted(target, payload.timestamp);
          });
        },
        onRunCompleted: (payload) => {
          terminalOutcome = 'completed';
          updateContinuation((target) => markRunCompleted(target, payload.timestamp));
          settleExecutionForRun(runId, 'completed');
        },
        onRunPartial: (payload) => {
          terminalOutcome = 'partial';
          updateContinuation((target) => markRunPartial(target, payload.timestamp));
          settleExecutionForRun(runId, 'failed');
        },
        onRunCancelled: (payload) => {
          terminalOutcome = 'cancelled';
          updateContinuation((target) => markRunCancelled(target, payload.timestamp));
          settleExecutionForRun(runId, 'failed');
        },
        onThreadId: (threadId) => {
          runThreadId = threadId || runThreadId;
        },
        onInputAccepted: () => {
          // 令牌 CAS 已消费（权威信号，第五批修正）：此后中断走裸 EOF 交接，不回滚卡片。
          // 不再用 onStreamStart（2xx 响应头）置位——响应头到达时令牌可能还没消费
          requestAccepted = true;
        },
        onSequence: (seq) => {
          lastSeq = seq;
          trackRunCursor(runId, continuationTargetId, { seq });
        },
        onStreamEnd: (info) => {
          sawTerminal = info.sawTerminal;
          if (!runThreadId || currentThreadId.value === runThreadId) {
            updateContinuation(clearTransientReasoning);
          }
        },
        onReasoningDelta: (delta, fullReasoning) => {
          if (!runThreadId || currentThreadId.value === runThreadId) {
            reasoningStream.push(delta, fullReasoning);
          }
        },
        onReasoningCompleted: (payload) => {
          if (!runThreadId || currentThreadId.value === runThreadId) {
            return reasoningStream.complete(payload);
          }
        },
        onReasoningConnectionEnd: () => {
          if (!runThreadId || currentThreadId.value === runThreadId) {
            return reasoningStream.complete().then(() => updateContinuation((target) => {
              clearTransientReasoning(target);
            }));
          }
        },
        onDelta: (_delta, fullContent) => {
          streamContent = fullContent; // 断流交接播种用（同 runAssistantTurn）
          trackRunCursor(runId, continuationTargetId, { content: fullContent });
          if (!runThreadId || currentThreadId.value === runThreadId) {
            updateContinuation(revealAssistantOutput);
            paintAssistantStreamText(
              updateContinuation,
              typewriter,
              stripRecommendMark(fullContent.slice(segmentContentOffset)),
              'delta',
              continuationTargetId,
            );
          }
        },
        onMessageCompleted: (fullContent) => {
          streamContent = fullContent;
          trackRunCursor(runId, continuationTargetId, { content: fullContent });
          if (!runThreadId || currentThreadId.value === runThreadId) {
            paintAssistantStreamText(
              updateContinuation,
              typewriter,
              stripRecommendMark(fullContent.slice(segmentContentOffset)),
              'complete',
              continuationTargetId,
            );
          }
        },
        onCommentary: (text, strippedContent, kind) => {
          streamContent = strippedContent;
          trackRunCursor(runId, continuationTargetId, { content: strippedContent });
          if (!runThreadId || currentThreadId.value === runThreadId) {
            return applyCommentaryAndHidePlanBody(
              updateContinuation,
              typewriter,
              commentaryStream,
              text,
              strippedContent,
              kind,
              segmentContentOffset,
            );
          }
        },
        onModelConnection: (payload) => {
          updateContinuation((target) => applyModelConnection(target, payload));
        },
        onRunPhase: (phase) => {
          updateContinuation((target) => { target.runStatus = phase; });
        },
        onMessageId: (id) => {
          updateContinuation((target) => {
            target.dbId = id;
          });
        },
        onInputApplied: ({ inputId }) => {
          if (inputId) appliedInstructionIds.add(inputId);
          // 生效不写任何 UI 回执（Codex 对齐 2026-07-26）：确认由模型自己在后续叙述与
          // 最终回答的对账里给出（「你中途补充 X 之后，我从第 N 步起已按新要求执行」）。
          // 这里只记 id 供收尾对账——真正没生效的才需要如实提示。
        },
        onInputRejected: ({ inputId, reason }) => {
          const segment = [...chatMessages.value].reverse().find(
            (item) =>
              item.role === 'assistant'
              && item.runId === runId
              && item.executionSegmentInputId === inputId,
          );
          if (segment) segment.preamble = `未能应用这条追加要求：${reason}`;
        },
        onInteractive: (payload) => {
          stillWaiting = true;
          updateContinuation((target) => {
            target.interactive = payload;
          });
          if (runThreadId) setActiveRun(runThreadId, runId, 'waiting_user');
        },
        onApproval: (payload) => {
          // 审批挂起与 input.required 同语义（P0-2 补齐，对齐订阅通道）：resume 续接段
          // 也可能出审批卡——置等待态收尾，不当终态、不触发裸 EOF 交接
          stillWaiting = true;
          updateContinuation((target) => {
            target.approval = payload;
          });
          if (runThreadId) setActiveRun(runThreadId, runId, 'waiting_user');
        },
        onCitations: (sources) => {
          updateContinuation((target) => {
            target.citations = sources;
          });
        },
        onPlanUpdate: (event) => {
          updateContinuation((target) => applyPlanUpdate(target, event));
        },
        onTaskPlan: (event) => {
          if (recordRunPlan(runId, event)) {
            updateContinuation((target) => applyTaskPlan(target, event));
          }
        },
        onSequenceGap: () => {
          void reloadRunPlanSnapshot(runId);
        },
        onCapabilityLoaded: (names) => {
          updateContinuation((target) => applyCapabilityLoaded(target, names));
        },
        onArtifactSaved: (payload) => {
          if (!routeArtifactEventToRun(
            chatMessages.value,
            runId,
            continuationTargetId,
            payload.files,
            (target) => applyArtifactSaved(target as ChatMessage, payload),
          )) {
            updateContinuation((target) => applyArtifactSaved(target, payload));
          }
        },
        onResearchProgress: (payload) => {
          updateContinuation((target) => applyResearchProgress(target, payload));
        },
        onToolEvent: (ev) => {
          // 切段后工具终态优先收口前段仍 running 的同名步骤（同 runAssistantTurn）
          if (!routeToolEventToRun(chatMessages.value, runId, continuationTargetId, ev, (t) => applyToolEvent(t as ChatMessage, ev))) {
            updateContinuation((target) => applyToolEvent(target, ev));
          }
        },
        onAttachmentsStatus: (items) => {
          updateContinuation((target) => applyAttachmentsStatus(target, items));
        },
        onCompaction: (payload) => {
          updateContinuation((target) => applyCompaction(target, payload));
        },
        onCompacted: (note) => {
          updateContinuation((target) => {
            applyCompaction(target, { status: 'completed' });
            if (note && note !== 'Context compacted') target.compactedNote = note;
          });
        },
        onSubagentStep: (ev) => {
          // 挂起时 started chip 落在原消息上（「执行中…」）；续接轮的终态事件优先收尾它，
          // 找不到才落续接消息——避免原 chip 永远转圈 + 两条消息各挂一个重复 chip
          if (ev.phase !== 'started' && paused?.subagentCalls) {
            const name = ev.name || '子智能体';
            const item = [...paused.subagentCalls]
              .reverse()
              .find((s) => s.status === 'running' && (!ev.name || s.name === name));
            if (item) {
              item.status = ev.phase === 'failed' ? 'failed' : 'completed';
              return;
            }
          }
          updateContinuation((target) => applySubagentStep(target, ev));
        },
        onError: (message) => {
          // 主动停止的 cancel 回推不标红（同 runAssistantTurn）
          if (controller.signal.aborted) return;
          let absorbed = false;
          updateContinuation((target) => {
            if (absorbFailedRunIfUserClarification(target, markRunCompleted)) {
              absorbed = true;
              return;
            }
            target.error = message;
            markRunFailed(target);
          });
          if (absorbed) {
            terminalOutcome = 'completed';
            settleExecutionForRun(runId, 'completed');
            return;
          }
          hadError = true;
          terminalOutcome = 'failed';
          settleExecutionForRun(runId, 'failed');
        },
      });
      // 裸 EOF（P0-2，同 runAssistantTurn）：resume 流传输正常结束但未见业务终态/挂起时，
      // 按已收游标交接订阅通道续写原消息——不清 activeRun、不按旧语义收尾
      handedOff = handOffBareEofToSubscription({
        sawTerminal,
        hadError,
        suspended: stillWaiting,
        aborted: controller.signal.aborted,
        runId,
        runThreadId,
        afterSequence: lastSeq,
        initialContent: streamContent,
        reuseMessageId: continuationTargetId,
        contentOffset: segmentContentOffset,
        stopTypewriter: () => typewriter.stop(),
        releaseSegmentController: resumeSegmentController,
      });
      if (!handedOff) {
        const slicedAnswer = stripRecommendMark(answer.slice(segmentContentOffset));
        let capturedPlan = false;
        updateContinuation((target) => {
          capturedPlan = ingestPlanReportText(target, slicedAnswer);
        });
        const cleanAnswer = capturedPlan ? '' : slicedAnswer;
        await typewriter.finish(cleanAnswer);
        // 收到 run.failed/error 时不算成功；无正文时把错误浮到 toast，避免续接失败静默无反馈
        completedSuccessfully = terminalOutcome === 'completed';
        if (hadError && !cleanAnswer) {
          options.showError(
            chatMessages.value.find((m) => m.id === continuationTargetId)?.error || '续接失败',
          );
        }
      }
    } catch (error) {
      const aborted = (error as { name?: string })?.name === 'AbortError' || controller.signal.aborted;
      // 空壳清理只针对本次新 push 的续接占位——合并写回原消息（ask_user）时不删：
      // 原消息挂着整轮执行轨迹，删了整个回合凭空消失
      const removeIfEmpty = () => {
        if (contMessage === paused) return;
        const current = chatMessages.value.find((item) => item.id === continuationTargetId);
        // 空壳判定与 runAssistantTurn 同口径（审计项 14）：有思考/工具步骤/卡片就不删
        const hasSubstance = Boolean(current && (
          current.content || current.preamble || current.planReport
          || current.agentSteps?.length || current.toolSteps?.length
          || current.generatedFiles?.length || current.subagentCalls?.length
          || current.interactive
        ));
        if (current && !hasSubstance) {
          chatMessages.value = chatMessages.value.filter((item) => item.id !== continuationTargetId);
        }
      };
      // 令牌未消费判定：input.accepted 未到且无任何业务帧——
      // fetch 抛错/HTTP 4xx/「2xx 响应头已到但 accepted 从未到达」的窗口断连，甚至
      // 提交后立即点停止（N-10②）——resume 令牌都仍有效、Run 仍挂起。按快照恢复卡片，
      // 用户可原地重试；令牌已消费的中断不走这里，送去下方裸 EOF 交接。
      const tokenUnconsumed = !requestAccepted && lastSeq === 0 && Boolean(paused && interactiveSnapshot);
      if (tokenUnconsumed && paused && interactiveSnapshot) {
        typewriter.stop();
        removeIfEmpty();
        rolledBack = true;
        paused.interactive = interactiveSnapshot;
        if (planUserMessageId != null) {
          chatMessages.value = chatMessages.value.filter((item) => item.id !== planUserMessageId);
        }
        if (aborted) {
          // 停止/切会话打断（N-10②）：只恢复卡片视觉——activeRun 状态由 stopChat 的取消
          // 流程（成功=终态、失败=仲裁恢复）或 loadThread 全量恢复自己收敛，这里不回写
          // 挂起态，避免与取消流程互相顶
        } else if (runThreadId) {
          if (priorRunStatus) {
            setActiveRun(runThreadId, runId, priorRunStatus);
          } else {
            // 提交前本就没有登记 activeRun（旧式 input.required POST 流收尾清过）：撤销入口处
            // 的 running 登记；不走 clearActiveRun——那会把仍在等待的 Run 记进 finishedRunIds，
            // 挡住之后 loadThreads 按服务端状态恢复
            const current = activeRuns.value[runThreadId];
            if (current?.id === runId) {
              const next = { ...activeRuns.value };
              delete next[runThreadId];
              activeRuns.value = next;
            }
            refreshRunUi();
          }
        }
        // 人话提示（2026-07-22）：原始 fetch 报错用户看不懂——说清「发生了什么+卡为什么回来+怎么办」
        if (!aborted) options.showError('提交没有送达（网络或服务闪断），已恢复选项卡，请重新提交');
      } else if (aborted) {
        // 就地冻结截断（同 runAssistantTurn）——不再把缓冲的后续内容播出来。
        typewriter.stop();
        removeIfEmpty();
      } else if (runThreadId) {
        // 传输层断流但 Run 在后端续跑（N-05）：与裸 EOF 走同一交接——不删续接气泡（合并
        // 写回原消息的 ask_user 场景更不能删），按已收游标续订 + 正文播种 + 复用同一条消息。
        // mergeIntoPaused 场景此前只提示「稍后刷新」完全不启动恢复订阅，一并收口。
        typewriter.stop();
        handedOff = true;
        // 本条流已断（见上），交接前注销自己的控制器——注销在 finally、晚于此处，而
        // subscribeVisibleRun 的「已有活流不重订」闸会把这条合法交接一并挡掉
        runControllers.delete(runId);
        if (runSegmentControllers.get(runId) === resumeSegmentController) {
          runSegmentControllers.delete(runId);
        }
        void subscribeVisibleRun(runThreadId, runId, {
          afterSequence: lastSeq,
          initialContent: streamContent,
          reuseMessageId: continuationTargetId,
          contentOffset: segmentContentOffset,
        });
      } else {
        typewriter.stop();
        removeIfEmpty();
        options.showError(error);
      }
    } finally {
      if (abortController === controller) abortController = null;
      if (activeTypewriter === typewriter) activeTypewriter = null; // 同 runAssistantTurn：收尾释放引用
      if (activeCommentaryStream === commentaryStream) activeCommentaryStream = null;
      if (activeReasoningStream === reasoningStream) activeReasoningStream = null;
      commentaryStream.stop();
      reasoningStream.stop();
      if (runControllers.get(runId) === controller) runControllers.delete(runId);
      if (runSegmentControllers.get(runId) === resumeSegmentController) {
        runSegmentControllers.delete(runId);
      }
      if (runThreadId) {
        if (stillWaiting) setActiveRun(runThreadId, runId, 'waiting_user');
        // 断流交接（handedOff）：Run 状态交由订阅通道裁决；发送失败回滚（rolledBack）：
        // 挂起态已按快照恢复——两者都不许在这里抢先清 activeRun/派发队列
        else if (!handedOff && !rolledBack) {
          clearActiveRun(runThreadId, runId, completedSuccessfully);
          onRunSettled(runThreadId, runId, terminalOutcome); // 真正终态：任务模式归位 + 派发下一条排队消息
        }
      } else {
        refreshRunUi(completedSuccessfully);
      }
      loadThreads();
    }
    return !rolledBack;
  }

  /**
   * Regenerate the last assistant reply: drop everything after the last user
   * message and re-run the turn. `regenerate` tells the backend to overwrite
   * the last assistant row instead of appending a duplicate user message.
   */
  /** 当前会话是否有等待用户输入/确认的活动 Run；自动恢复不属于 HITL。
   *  深扫修复(2026-07-20):挂起态 chatLoading=false 后,编辑重发/重新生成入口被解禁,
   *  点击会本地裁剪掉挂起卡且失败不回滚——在入口处按挂起态拦截,提示走卡片或「取消」。 */
  function hasSuspendedRun(): boolean {
    const active = currentThreadId.value ? activeRuns.value[currentThreadId.value] : undefined;
    return isWaitingForUserStatus(active?.status);
  }

  async function regenerateLast(model?: string) {
    if (assistantPreset.value === 'interview') {
      options.showNotice('请使用面试评价中的“重新作答”，原始回答和评分会保留');
      return;
    }
    if (chatLoading.value) return;
    // 打断互斥（2026-07-26 并发审计）：stopChat 是「先同步 clearActiveRun（chatLoading 立刻
    // 变 false）、再 await cancelChatRun」，所以点停止后的那一两秒里 chatLoading 已是 false
    // 但取消还没收敛——此时本入口会与 cancel 并发发起新 Run，撞后端 R0。sendChat 早有这道
    // 等待环（见 stopInFlight 的排队循环），这三个入口漏了。
    if (stopInFlight) await stopInFlight;
    if (chatLoading.value || stopInFlight) return;

    if (hasSuspendedRun()) {
      options.showNotice('当前任务在等待你的确认，请先处理上方卡片，或回复「取消」放弃后再重试');
      return;
    }
    const lastUser = [...chatMessages.value].reverse().find((item) => item.role === 'user');
    if (!lastUser) return;
    // 换模型重答：切到所选模型（同步更新选择器展示），再以同一问题重跑
    if (model && model !== activeModel.value) await handleModelChange(model);
    const idx = chatMessages.value.findIndex((item) => item.id === lastUser.id);
    // 启动失败回滚（审计项 18）：先留住被本地裁掉的旧回答，启动被拒（4xx/确认未建 Run）
    // 时恢复——不能点一次「重新生成」就把旧历史平白裁没
    const removedTail = chatMessages.value.slice(idx + 1);
    chatMessages.value = chatMessages.value.slice(0, idx + 1);
    const accepted = await runAssistantTurn(String(lastUser.content), { regenerate: true });
    if (!accepted && removedTail.length) {
      chatMessages.value = [...chatMessages.value, ...removedTail];
    }
  }

  /**
   * 重新编辑并重发某条自己发送的消息：丢弃该消息及其之后的全部消息，
   * 以编辑后的文本作为一次新的发送。文件附件不重复注入，但原消息显式选中的 Skill
   * 必须按消息快照恢复，不能读已被清空的 composer 选择。
   */
  async function resendEditedMessage(messageId: number, newContent: string) {
    if (assistantPreset.value === 'interview') {
      options.showNotice('面试记录保留原始作答，请在面试评价中选择“重新作答”');
      return;
    }
    if (chatLoading.value) return;
    // 打断互斥（2026-07-26 并发审计）：stopChat 是「先同步 clearActiveRun（chatLoading 立刻
    // 变 false）、再 await cancelChatRun」，所以点停止后的那一两秒里 chatLoading 已是 false
    // 但取消还没收敛——此时本入口会与 cancel 并发发起新 Run，撞后端 R0。sendChat 早有这道
    // 等待环（见 stopInFlight 的排队循环），这三个入口漏了。
    if (stopInFlight) await stopInFlight;
    if (chatLoading.value || stopInFlight) return;

    if (hasSuspendedRun()) {
      options.showNotice('当前任务在等待你的确认，请先处理上方卡片，或回复「取消」放弃后再编辑重发');
      return;
    }
    const idx = chatMessages.value.findIndex((item) => item.id === messageId && item.role === 'user');
    if (idx === -1) return;
    const trimmed = newContent.trim();
    if (!trimmed) return;
    const original = chatMessages.value[idx];
    let originalSkills = messageTurnSkills(
      original.turnSkills,
      original.attachments,
      mentionSkills.value,
    );
    // 修复上线前的历史只持久了 Skill 名称：必要时拉一次当前权威目录，
    // 只在名称唯一命中时恢复。同名歧义不猜，服务端仍会按 id 重验 ACL/启用状态。
    if (!originalSkills.length && hasSkillReference(original.attachments) && !mentionSkillsLoaded) {
      await loadMentionSkills();
      originalSkills = messageTurnSkills(
        original.turnSkills,
        original.attachments,
        mentionSkills.value,
      );
    }
    // 截断点（F2 线性覆盖）：优先被编辑消息自身的库 id；本地新发消息若未回填 dbId，
    // 退而取其后首条有库 id 的消息（后端删除 >= 该点，与本地 slice 尽量对齐）
    const truncateFrom = original.dbId
      ?? chatMessages.value.slice(idx).find((m) => m.dbId)?.dbId;
    // 启动失败回滚（审计项 18）：留住被裁掉的原消息与其后历史，启动被拒时整体恢复
    const removedTail = chatMessages.value.slice(idx);
    chatMessages.value = chatMessages.value.slice(0, idx);
    // 文件卡不再挂回（解析正文没有重传）；Skill 卡则与本次真实重发的
    // selected_skills 对齐，避免界面显示与请求事实不一致。
    const editedSkillCards = composerBubbleAttachments({ skills: originalSkills });
    const newUserMessage: ChatMessage = {
      id: nextLocalId(),
      role: 'user',
      content: trimmed,
      attachments: editedSkillCards.length ? editedSkillCards : undefined,
      turnSkills: [...originalSkills],
    };
    chatMessages.value.push(newUserMessage);
    const accepted = await runAssistantTurn(trimmed, {
      truncateFromMessageId: truncateFrom,
      userMessageLocalId: newUserMessage.id,
      skillOverride: originalSkills,
    });
    if (!accepted) {
      chatMessages.value = [
        ...chatMessages.value.filter((m) => m.id !== newUserMessage.id),
        ...removedTail,
      ];
    }
  }

  // 把某页返回里的活跃 Run 合入 activeRuns（仅按本页出现的会话增删，其它页会话不动）
  function mergeActiveRuns(list: ThreadItem[]) {
    const nextRuns = { ...activeRuns.value };
    for (const thread of list) {
      if (
        thread.active_run?.id &&
        isActiveRunStatus(thread.active_run.status) &&
        !finishedRunIds.has(thread.active_run.id)
      ) {
        nextRuns[thread.id] = thread.active_run;
      } else if (nextRuns[thread.id]) {
        // 本端还有活流就不许删（2026-07-26 并发审计）：loadThreads 在 runAssistantTurn /
        // submitResume / subscribeVisibleRun 的 finally 里都是 fire-and-forget，队列自动派发
        // 又恰好在同一 tick 开下一个 Run——「列表查询在服务端先于新 Run 落库、响应却晚于
        // run.started 到达」时，这个快照会把正在流式的 Run 直接删掉：chatLoading 突然变
        // false（停止按钮消失、发送键复活），用户再发就撞 R0。
        if (runControllers.has(nextRuns[thread.id].id)) continue;
        delete nextRuns[thread.id];
      }
    }
    activeRuns.value = nextRuns;
  }

  // 会话列表请求序号（审计项 30）：连发两次加载/搜索时，旧响应不得覆盖新响应
  let threadsLoadSeq = 0;

  // 首页加载/刷新：始终回到第一页（发消息后活跃会话本就冒泡到顶）
  async function loadThreads(search?: string) {
    const seq = ++threadsLoadSeq;
    threadsLoading.value = true;
    try {
      const list = await getThreads(search ?? threadSearch.value, THREADS_PAGE_SIZE, 0, threadScope);
      if (seq !== threadsLoadSeq) return; // 已有更新的请求发出：本响应过期，丢弃
      threadList.value = list;
      threadHasMore.value = list.length >= THREADS_PAGE_SIZE;
      mergeActiveRuns(list);
      refreshRunUi();
    } catch (error) {
      if (seq !== threadsLoadSeq) return;
      threadList.value = [];
      threadHasMore.value = false;
      console.warn('Failed to load conversation history:', error);
    } finally {
      threadsLoading.value = false;
    }
  }

  /** 进入主对话只预加载历史列表，不自动打开上次或最近会话。
   *  从其他板块、刷新、子智能体运行页返回时都停在欢迎页；打开会话必须用户点历史。 */
  async function restoreCurrentThread() {
    const restoreToken = viewVersion;
    try {
      await loadThreads();
      if (viewVersion !== restoreToken) return;
    } finally {
      restoringLatestThread.value = false;
    }
  }

  // 翻页追加下一批（按当前已加载数作为 offset，去重后叠加）
  async function loadMoreThreads() {
    if (threadsLoading.value || !threadHasMore.value) return;
    const seq = ++threadsLoadSeq;
    threadsLoading.value = true;
    try {
      const list = await getThreads(threadSearch.value, THREADS_PAGE_SIZE, threadList.value.length, threadScope);
      if (seq !== threadsLoadSeq) return; // 期间有新的加载/搜索：本页追加已过期（审计项 30）
      const existing = new Set(threadList.value.map((t) => t.id));
      const fresh = list.filter((t) => !existing.has(t.id));
      threadList.value = [...threadList.value, ...fresh];
      threadHasMore.value = list.length >= THREADS_PAGE_SIZE;
      mergeActiveRuns(list);
      refreshRunUi();
    } catch (error) {
      options.showError(error);
    } finally {
      threadsLoading.value = false;
    }
  }

  // 搜索防抖：避免每敲一个字就打一次后端
  function searchThreads() {
    if (searchTimer) window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      searchTimer = null;
      loadThreads(threadSearch.value);
    }, 280);
  }

  async function togglePin(threadId: string) {
    const target = threadList.value.find((thread) => thread.id === threadId);
    try {
      await pinThread(threadId, !target?.pinned, threadScope);
      await loadThreads();
    } catch (error) {
      options.showError(error);
    }
  }

  /** 用权威 Run/Plan 快照恢复计划面板；前端不保存第二份计划状态。 */
  function restoreHarnessRunSnapshot(
    run: ActiveRun,
    executionTrace?: ExecutionTracePayload,
  ): ChatMessage {
    let target = [...chatMessages.value].reverse().find(
      (message) => message.role === 'assistant' && message.runId === run.id,
    );
    if (!target) {
      target = {
        id: nextLocalId(),
        role: 'assistant',
        content: '',
        ...(executionTrace ? restoreHistoryTrace(executionTrace) : {}),
        runId: run.id,
      };
      chatMessages.value.push(target);
    } else if (executionTrace) {
      // 活动 Run 快照是权威展示基线：已有助手锚点也要用它补齐从起点到
      // event_cursor 的全部步骤与 startedAt。只保留锚点而不合并快照，会让续订尾段
      // 变成页面上唯一可见的时间线，表现为“旧步骤和计时一起消失”。
      Object.assign(target, restoreHistoryTrace(executionTrace));
    }
    target.runStatus = run.status;
    const waitingForUser = isWaitingForUserStatus(run.status || run.phase);
    if (!waitingForUser && run.status !== 'created') {
      ensureRunningThought(target);
    }

    if (run.plan && typeof run.plan === 'object') {
      applyAuthoritativeRunPlan(run.id, {
        ...(run.plan as Record<string, any>),
        goal_contract: run.goal_contract,
        approved_plan_version: run.approved_plan_version,
      }, target);
    }

    if (waitingForUser && !target.interactive) {
      target.content ||= '这个任务正在等你补充或确认，直接回复就能继续。';
    }
    return target;
  }

  /** 仲裁发现 Run 等待输入时，重拉权威快照，不回放旧卡片协议。 */
  async function restoreWaitingInputFromServer(threadId: string, runId: string) {
    if (currentThreadId.value !== threadId) return;
    const hasPendingInput = chatMessages.value.some(
      (message) => message.interactive && (!message.interactive.run_id || message.interactive.run_id === runId),
    );
    if (hasPendingInput) return;
    try {
      const serverRun = await getThreadActiveRun(threadId, threadScope);
      if (!serverRun?.id || serverRun.id !== runId) return;
      if (currentThreadId.value !== threadId) return;
      restoreHarnessRunSnapshot(serverRun);
    } catch {
      // 快照暂时不可用时保持挂起态，下次重连继续拉取。
    }
  }

  function ensureVisibleAssistantPlaceholder(): number {
    const last = chatMessages.value[chatMessages.value.length - 1];
    if (last?.role === 'assistant' && !last.content) {
      ensureRunningThought(last);
      return last.id;
    }
    const placeholder: ChatMessage = { id: nextLocalId(), role: 'assistant', content: '' };
    ensureRunningThought(placeholder);
    chatMessages.value.push(placeholder);
    return placeholder.id;
  }

  async function subscribeVisibleRun(
    threadId: string,
    runId: string,
    /** POST 流裸断交接（断流≠完成）：按已收游标续订 + 原地复用发送流已渲染的半截气泡 */
    resume?: {
      afterSequence?: number;
      initialContent?: string;
      reuseMessageId?: number;
      contentOffset?: number;
    },
  ) {
    if (!threadId || !runId || visibleRunSubscriptions.has(runId)) return;
    // 去重必须覆盖「前台 POST 发送流」（2026-07-26 并发审计）：POST 流只登记进
    // runControllers、从不进 visibleRunSubscriptions，于是生成中在历史抽屉里点**当前这条
    // 会话**（关抽屉的自然动作）会为同一个 Run 再开一条订阅，并 runControllers.set 覆盖
    // 掉 POST 流的控制器——此后点停止只 abort 了订阅，POST 流继续吐字＝**停止按钮失效**；
    // 同时两条流双写同一条消息（两个流式绘制器互相抢进度），
    // 工具步骤在时间线里出现两遍，订阅先到终态还会提前派发队列。
    if (runControllers.has(runId)) return;
    // 归属校验（审计项 4）：本函数只往「当前可见会话」写占位与正文。调用方（如停止确认
    // 窗口后的恢复订阅）拿着旧 threadId 时直接放弃——Run 留在 activeRuns，切回该会话时
    // loadThread 会重新订阅，不会向用户此刻正看着的另一个会话插入助手气泡。
    if (currentThreadId.value !== threadId) return;
    const controller = new AbortController();
    visibleRunSubscriptions.set(runId, controller);
    runControllers.set(runId, controller);
    // 登记为当前可见订阅，供 loadThread/resetChat 切走时单独中止（防孤儿连接 + 切回可重订阅）
    visibleSubController = controller;
    visibleSubRunId = runId;
    // 交接复用：优先写回发送流已渲染的那条助手消息（不删除重建、不闪空白）；
    // 消息已不在（如其间切走会话被重载）时退回「末条空正文助手消息复用/新建占位」逻辑。
    const reused = resume?.reuseMessageId != null
      ? chatMessages.value.find((item) => item.id === resume.reuseMessageId && item.role === 'assistant')
      : undefined;
    let assistantId = reused ? reused.id : ensureVisibleAssistantPlaceholder();
    let stillWaiting = false;
    let hadError = false;
    // 停止收敛帧（第三批 P0-2）：本 Run 已发出停止请求时，pump 收敛回推的 run.failed
    // ＝取消收尾而非真失败——按「已停止」视觉定格、不标红、收尾不算成功完成。
    let cancelledByStop = false;
    // 任务模式合法挂起（P0）：需求卡=waiting_user / 计划卡=waiting_confirmation。跨重连保持；
    // 推进事件（提交/确认单/决策/进入执行）到达即解除——after=0 全量回放整轮历史时，
    // 旧挂起卡不会残留成「仍在等用户」。
    // 业务终态跟踪（断流≠完成）：一次连接正常 EOF 但没见过 run.completed / run.failed /
    // error / 挂起卡时只是传输结束——不能收尾清 activeRun，要经服务端仲裁后续订或回放。
    let sawTerminal = false;
    let terminalOutcome: 'completed' | 'partial' | 'failed' | 'cancelled' | null = null;
    const updateTarget = (fn: (message: ChatMessage) => void) => {
      if (currentThreadId.value !== threadId) return;
      const target = chatMessages.value.find((item) => item.id === assistantId);
      if (target) fn(target);
    };
    // 重订阅与首次发送共用同一流式绘制器：已经显示的前缀原地保留，
    // 新的 answer delta 继续平滑追加，不因断线交接闪空或从头重打。
    let segmentContentOffset = Math.max(0, Number(resume?.contentOffset) || 0);
    // Run 级订阅游标（第二轮评审 P1）：重连必须 after=lastSeq 续传而非全量回放——
    // 正文靠整段覆盖近似幂等，但 tool.started/completed、subagent 事件重放会重复
    // 灌进 reducer（时间线/工作卡重复）。lastContent 同步持有已收正文，续传时播种
    // 解析器累积器（message.delta 只发增量）。POST 流交接进来时以交接现场播种。
    let lastSeq = resume?.afterSequence || 0;
    let lastContent = resume?.initialContent || '';
    let typewriter = createTypewriter(assistantId);
    let commentaryStream = createCommentaryStream(updateTarget);
    let reasoningStream = createReasoningStream(updateTarget);
    activeTypewriter = typewriter;
    activeCommentaryStream = commentaryStream;
    activeReasoningStream = reasoningStream;
    if (lastContent) {
      typewriter.push(stripRecommendMark(lastContent.slice(segmentContentOffset)));
    }
    const segmentController: RunSegmentController = {
      split: () => {
        typewriter.stop();
        commentaryStream.stop();
        reasoningStream.stop();
        const previous = chatMessages.value.find((item) => item.id === assistantId);
        const endedAt = Date.now();
        if (previous) {
          previous.executionSegmentEndedAt = endedAt;
          previous.executionSegmentEndSequence = lastSeq || undefined;
          // 不再给前一段落终态时长：插话不结束这一轮（Codex 契约=整轮一个执行头）。
          // 时长由本轮末段在收尾时写，首段的头按整轮口径显示。
          sealSegmentSteps(previous);
        }
        const nextSegment: ChatMessage = {
          id: nextLocalId(),
          role: 'assistant',
          content: '',
          runId,
          executionSegmentIndex: (previous?.executionSegmentIndex ?? 0) + 1,
          executionSegmentStartSequence: lastSeq ? lastSeq + 1 : undefined,
          // 继承整轮起点：本轮末段的 runDurationMs 因此覆盖「插话前+插话后」的完整时长，
          // 首段的执行头照它显示「工作过程 · 整轮时长」，不会被插话截成两段计时。
          runStartedAt: previous?.runStartedAt || endedAt,
          agentMode: previous?.agentMode,
          planExecutionUnlocked: previous?.planExecutionUnlocked,
        };
        // 切点吸附块边界（订阅路径直接整段覆盖、无打字机积压，但硬切同样会劈开表格）
        const cut = segmentSplitOffset(lastContent, segmentContentOffset);
        const sealedContent = stripRecommendMark(lastContent.slice(segmentContentOffset, cut));
        if (previous) {
          // 已标 endedAt，直写定稿（updateChatMessageContent 对封存段拒写）
          const idx = chatMessages.value.findIndex((item) => item.id === previous.id);
          if (idx >= 0) {
            chatMessages.value[idx] = { ...chatMessages.value[idx], content: sealedContent };
          }
        }
        chatMessages.value.push(nextSegment);
        assistantId = nextSegment.id;
        segmentContentOffset = cut;
        typewriter = createTypewriter(assistantId);
        commentaryStream = createCommentaryStream(updateTarget);
        reasoningStream = createReasoningStream(updateTarget);
        activeTypewriter = typewriter;
        activeCommentaryStream = commentaryStream;
        activeReasoningStream = reasoningStream;
        typewriter.push(stripRecommendMark(lastContent.slice(segmentContentOffset)));
        trackRunCursor(runId, assistantId, { contentOffset: segmentContentOffset });
        return assistantId;
      },
    };
    runSegmentControllers.set(runId, segmentController);
    /** 收尾公共出口：挂起态回写 waiting_*（等用户输入/确认），否则清 activeRun 并派发终态。 */
    const settleRun = (
      succeeded: boolean,
      outcome: RunTerminalOutcome = terminalOutcome,
    ) => {
      if (stillWaiting) setActiveRun(threadId, runId, 'waiting_user');
      else {
        clearActiveRun(threadId, runId, succeeded);
        onRunSettled(threadId, runId, outcome); // 真正终态：任务模式归位 + 派发下一条排队消息
      }
    };
    const subscribeOnce = async (): Promise<'settled' | 'bare-eof'> => {
      sawTerminal = false;
      const answer = await subscribeChatRun(runId, {
        signal: controller.signal,
        after: lastSeq || undefined,
        initialContent: lastSeq ? lastContent : undefined,
        onSequence: (seq) => {
          lastSeq = seq;
          trackRunCursor(runId, assistantId, { seq });
        },
        onStreamEnd: (info) => {
          sawTerminal = info.sawTerminal;
          updateTarget(clearTransientReasoning);
        },
        onThreadId: (nextThreadId) => {
          if (nextThreadId && currentThreadId.value === threadId) currentThreadId.value = nextThreadId;
        },
        onContextUsage: (usage) => {
          if (currentThreadId.value === threadId) {
            // 计量行基线只认 actual（2026-07-26 真机修复）：调用前的 estimate 与调用后的
            // actual 描述同一份 prompt，差值＝本地估算误差而非上下文增长。此前不加区分，
            // 纯问答（回答「2」一个字）会把 4.8k 估算误差显示成「本轮产出」。旧事件无
            // kind＝按 actual 处理，历史消息口径不变。
            if (usage.kind !== 'estimate') {
              updateTarget((target) => {
                if (target.turnBaselineTokens == null) target.turnBaselineTokens = usage.tokens;
                target.liveContextTokens = usage.tokens;
              });
            }
          }
        },
        onMessageId: (id) => {
          updateTarget((target) => {
            target.dbId = id;
          });
        },
        onInputApplied: ({ inputId }) => {
          if (inputId) appliedInstructionIds.add(inputId);
          // 生效不写任何 UI 回执（Codex 对齐 2026-07-26）：确认由模型自己在后续叙述与
          // 最终回答的对账里给出（「你中途补充 X 之后，我从第 N 步起已按新要求执行」）。
          // 这里只记 id 供收尾对账——真正没生效的才需要如实提示。
        },
        onInputRejected: ({ inputId, reason }) => {
          const segment = [...chatMessages.value].reverse().find(
            (item) => item.executionSegmentInputId === inputId,
          );
          if (segment) segment.preamble = `未能应用这条追加要求：${reason}`;
        },
        onRunStarted: (payload) => {
          stillWaiting = false;
          setActiveRun(threadId, payload.run_id || runId, payload.status || 'running', {
            model: payload.model || activeRuns.value[threadId]?.model,
          });
          if (payload.agent_mode === 'plan' && runId) {
            registerPlanProfileRun(threadId, runId); // 恢复流同样以后端 agent_mode 为准（批次6）
          }
          if (payload.agent_mode === 'research' && runId) {
            registerResearchProfileRun(threadId, runId);
          }
          updateTarget((target) => {
            target.agentMode = payload.agent_mode;
            markRunStarted(target, payload.timestamp);
          });
        },
        onRunCompleted: (payload) => {
          stillWaiting = false;
          terminalOutcome = 'completed';
          updateTarget((target) => markRunCompleted(target, payload.timestamp));
          settleExecutionForRun(runId, 'completed');
        },
        onRunPartial: (payload) => {
          stillWaiting = false;
          terminalOutcome = 'partial';
          updateTarget((target) => markRunPartial(target, payload.timestamp));
          settleExecutionForRun(runId, 'failed');
        },
        onRunCancelled: (payload) => {
          stillWaiting = false;
          terminalOutcome = 'cancelled';
          updateTarget((target) => markRunCancelled(target, payload.timestamp));
          settleExecutionForRun(runId, 'failed');
        },
        onInputAccepted: () => {
          // 段边界（切回乱序修复）：回放越过一次挂起→续跑边界，此前累积的正文/思考属于
          // 已挂起段（活流里在上一个气泡、Run 完成后也不落库）——清气泡与续传播种游标，
          // 本气泡只呈现当前段；执行时间线/卡片仍按整 Run 回放，不受影响。
          lastContent = '';
          segmentContentOffset = 0;
          stillWaiting = false;
          setActiveRun(threadId, runId, 'running');
          trackRunCursor(runId, assistantId, { content: '' });
          typewriter.reset('');
          updateTarget((target) => {
            target.interactive = null;
            target.approval = undefined;
            // 续接是新执行段；旧段的上下文用量不能计入这段的增长。
            target.turnBaselineTokens = undefined;
            target.liveContextTokens = undefined;
          });
        },
        onInteractive: (payload) => {
          stillWaiting = true;
          updateTarget((target) => {
            target.interactive = payload;
          });
          setActiveRun(threadId, runId, 'waiting_user');
        },
        onCitations: (sources) => {
          updateTarget((target) => {
            target.citations = sources;
          });
        },
        onRouteSelected: (route) => {
          updateTarget((target) => {
            target.routedAgent = route.name || '智能体';
          });
        },
        onClarification: (payload) => {
          updateTarget((target) => {
            target.clarification = payload.options;
            // 重订阅路径本地无原轮快照，从事件回放重建原轮上下文（P2a）：点选重发才能带上
            // 原轮技能/附件。附件为文本 ref（无 image_url），重发到子智能体走文本注入，够用。
            target.clarificationContext = clarificationContextFromEvent(payload);
          });
        },
        onApproval: (payload) => {
          // 审批挂起与 input.required 同语义（对齐）：置等待态，收尾时回写 waiting_user
          // 而非清 activeRun——审批卡等用户点头，不是终态
          stillWaiting = true;
          updateTarget((target) => {
            target.approval = payload;
          });
          setActiveRun(threadId, runId, 'waiting_user');
        },
        onError: (message) => {
          // 主动停止/切会话断流的 cancel 回推不标红（同 runAssistantTurn）
          if (controller.signal.aborted) return;
          if (stopRequestedRuns.has(runId) && String(message || '').includes('已停止生成')) {
            // 停止已请求（cancel pending 后恢复的订阅）且帧文案确为取消收尾（"已停止生成"）：
            // 执行卡定格「已停止」，不标红（第三批 P0-2/P1-3）。文案双保险（第五批项 2③）：
            // 停止等待期间到达的**真实失败**（模型/工具错误，别的文案）不得被误染成已停止，
            // 落到下方照常标红，错误不吞。
            cancelledByStop = true;
            stillWaiting = false;
            terminalOutcome = 'cancelled';
            updateTarget((target) => markRunCancelled(target));
            return;
          }
          stillWaiting = false;
          let absorbed = false;
          updateTarget((target) => {
            if (absorbFailedRunIfUserClarification(target, markRunCompleted)) {
              absorbed = true;
              return;
            }
            target.error = message;
            markRunFailed(target);
          });
          if (absorbed) {
            terminalOutcome = 'completed';
            settleExecutionForRun(runId, 'completed');
            return;
          }
          hadError = true;
          terminalOutcome = 'failed';
          settleExecutionForRun(runId, 'failed');
        },
        onPlanUpdate: (event) => {
          updateTarget((target) => applyPlanUpdate(target, event));
        },
        onTaskPlan: (event) => {
          if (recordRunPlan(runId, event)) {
            updateTarget((target) => applyTaskPlan(target, event));
          }
        },
        onSequenceGap: () => {
          void reloadRunPlanSnapshot(runId);
        },
        onCapabilityLoaded: (names) => {
          updateTarget((target) => applyCapabilityLoaded(target, names));
        },
        onArtifactSaved: (payload) => {
          if (!routeArtifactEventToRun(
            chatMessages.value,
            runId,
            assistantId,
            payload.files,
            (target) => applyArtifactSaved(target as ChatMessage, payload),
          )) {
            updateTarget((target) => applyArtifactSaved(target, payload));
          }
        },
        onResearchProgress: (payload) => {
          updateTarget((target) => applyResearchProgress(target, payload));
        },
        onToolEvent: (ev) => {
          if (!routeToolEventToRun(chatMessages.value, runId, assistantId, ev, (t) => applyToolEvent(t as ChatMessage, ev))) {
            updateTarget((target) => applyToolEvent(target, ev));
          }
        },
        onSubagentStep: (ev) => updateTarget((target) => applySubagentStep(target, ev)),
        onAttachmentsStatus: (items) => {
          updateTarget((target) => applyAttachmentsStatus(target, items));
        },
        onCompaction: (payload) => {
          updateTarget((target) => applyCompaction(target, payload));
        },
        onCompacted: (note) => {
          updateTarget((target) => {
            applyCompaction(target, { status: 'completed' });
            if (note && note !== 'Context compacted') target.compactedNote = note;
          });
        },
        onReasoningDelta: (delta, fullReasoning) => {
          reasoningStream.push(delta, fullReasoning);
        },
        onReasoningCompleted: (payload) => {
          return reasoningStream.complete(payload);
        },
        onReasoningConnectionEnd: () => {
          return reasoningStream.complete().then(() => updateTarget((target) => {
            clearTransientReasoning(target);
          }));
        },
        onDelta: (_delta, fullContent) => {
          lastContent = fullContent;
          trackRunCursor(runId, assistantId, { content: fullContent });
          updateTarget(revealAssistantOutput);
          paintAssistantStreamText(
            updateTarget,
            typewriter,
            stripRecommendMark(fullContent.slice(segmentContentOffset)),
            'delta',
            assistantId,
          );
        },
        onMessageCompleted: (fullContent) => {
          lastContent = fullContent;
          trackRunCursor(runId, assistantId, { content: fullContent });
          paintAssistantStreamText(
            updateTarget,
            typewriter,
            stripRecommendMark(fullContent.slice(segmentContentOffset)),
            'complete',
            assistantId,
          );
        },
        onCommentary: (text, strippedContent, kind) => {
          lastContent = strippedContent;
          trackRunCursor(runId, assistantId, { content: strippedContent });
          return applyCommentaryAndHidePlanBody(
            updateTarget,
            typewriter,
            commentaryStream,
            text,
            strippedContent,
            kind,
            segmentContentOffset,
          );
        },
        onModelConnection: (payload) => {
          updateTarget((target) => applyModelConnection(target, payload));
        },
        onRunPhase: (phase) => {
          updateTarget((target) => { target.runStatus = phase; });
        },
      });
      // 裸 EOF（传输正常结束但未见业务终态/挂起）：不收尾、不清 activeRun、不覆写正文——
      // 已渲染内容原样保留，交由外层向服务端仲裁后续订或回放。
      if (!sawTerminal && !stillWaiting && !hadError) return 'bare-eof';
      const slicedAnswer = stripRecommendMark(answer.slice(segmentContentOffset));
      let capturedPlan = false;
      updateTarget((target) => {
        capturedPlan = ingestPlanReportText(target, slicedAnswer);
      });
      await typewriter.finish(capturedPlan ? '' : slicedAnswer);
      // 流结束兜底折叠（无正文时思考面板不停留在展开态）
      updateTarget(revealAssistantOutput);
      const ids = parseRecommendIds(answer);
      if (ids.length) {
        const matched = findRecommendedApps(answer);
        if (matched.length) {
          updateTarget((target) => {
            target.recommendedAgents = matched;
          });
        }
      }
      settleRun(terminalOutcome === 'completed' && !hadError && !cancelledByStop);
      return 'settled';
    };
    // 断流自愈（整合路线图 #2）：传输层断流 ≠ Run 失败。非 abort 网络错误退避重连——
    // 重连用 after=lastSeq 游标续传 + initialContent 播种正文（首次订阅 lastSeq=0 全量回放
    // 进全新占位消息，同样无重复）；正常 EOF 但未见业务终态（裸 EOF）同样不算完成：先向
    // 服务端仲裁 Run 真实状态——仍在跑就按游标续订，已终态就最后回放一次拿收尾事件。
    // 整个过程不清空已渲染内容；只有确认后端已不在跑才收尾，绝不因网络问题标失败/清 activeRun。
    const RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 15000];
    let reconnects = 0;
    let terminalReplayed = false;
    // 权威仲裁到的真实终态（第三批 P0-1）：终态回放拿不到收尾帧时按它补视觉收尾
    let arbitratedOutcome: 'completed' | 'partial' | 'failed' | 'cancelled' | null = null;
    let arbitratedError: string | undefined;
    /** 重连退避（公共）：封顶退避后持续重试，直到终态、用户停止或切走；等待中被中止→false。
     *  离线（navigator.onLine=false）时不烧额度：挂在 online 事件上，网络一恢复立刻按
     *  游标重订并重置额度（N-03）。agent-api 单独停服不会触发 browser online，故不能
     *  在有限次数后永久放弃。 */
    const delayBeforeReconnect = async () => {
      if (typeof navigator !== 'undefined' && navigator.onLine === false) {
        const backOnline = await waitForOnline(controller.signal, 120_000);
        if (controller.signal.aborted) return false;
        if (backOnline) {
          reconnects = 0;
          return true;
        }
      }
      const wait = RECONNECT_DELAYS[Math.min(reconnects, RECONNECT_DELAYS.length - 1)];
      reconnects += 1;
      // 抖动：多标签/多订阅同时断线时不齐步重连打伙冲击后端
      await new Promise((resolve) => window.setTimeout(resolve, wait + Math.floor(Math.random() * 300)));
      return !controller.signal.aborted;
    };
    /** 终态回放依然裸 EOF（收尾帧缺失/回放窗口已过）时，按仲裁接口的真实终态补视觉收尾：
     *  completed→完成、failed→失败标红、cancelled→已停止；旧后端仲裁不到具体终态则保持现状 */
    const applyArbitratedOutcome = () => {
      if (!arbitratedOutcome) return;
      updateTarget((target) => {
        if (arbitratedOutcome === 'failed') {
          if (absorbFailedRunIfUserClarification(target, markRunCompleted)) {
            hadError = false;
            return;
          }
          if (arbitratedError && !target.error) target.error = arbitratedError;
          markRunFailed(target);
        } else if (arbitratedOutcome === 'cancelled') {
          markRunCancelled(target);
        } else if (arbitratedOutcome === 'partial') {
          markRunPartial(target);
        } else {
          markRunCompleted(target);
        }
      });
      settleExecutionForRun(runId, arbitratedOutcome === 'completed' ? 'completed' : 'failed');
    };
    try {
      for (;;) {
        const seqBefore = lastSeq;
        try {
          const outcome = await subscribeOnce();
          if (outcome === 'settled') return;
          // ---- 裸 EOF：传输结束但未见业务终态 ----
          // 中止竞态（EOF 与 abort 同帧）：同 abort 路径保留 activeRun 供切回续接
          if (controller.signal.aborted) return;
          // 有进展就恢复重连额度：长回答被中间层反复掐断也能续完，不会耗尽额度后弃接
          if (lastSeq > seqBefore) reconnects = 0;
          if (terminalReplayed) {
            // 终态回放依然裸 EOF（终态事件缺失/回放窗口已过）：以已收内容如实收尾，不无限
            // 循环；有权威终态就按它定格视觉（completed/failed/cancelled，第三批 P0-1）
            updateTarget(revealAssistantOutput);
            applyArbitratedOutcome();
            settleRun(!hadError && arbitratedOutcome === 'completed', arbitratedOutcome);
            return;
          }
          const verdict = await arbitrateRunTerminalState(threadId, runId);
          if (controller.signal.aborted) return;
          if (verdict.kind === 'waiting') {
            // 权威接口说 Run 在等用户（挂起帧缺失/回放窗口已过）：按挂起态收尾恢复，
            // 并立即拉活动 Run 快照恢复需求卡/计划卡（N-07）——不能只回写状态留死锁
            setActiveRun(threadId, runId, verdict.status);
            void restoreWaitingInputFromServer(threadId, runId);
            return;
          }
          if (verdict.kind === 'ended') {
            terminalReplayed = true; // 后端已终态：按游标最后回放一次拿收尾事件/最终回答
            arbitratedOutcome = verdict.outcome;
            arbitratedError = verdict.error;
            continue;
          }
          // 后端确认仍在跑（或查不到）：按 after=lastSeq 续订，不清已渲染内容
          if (await delayBeforeReconnect()) continue;
          if (controller.signal.aborted) return;
          options.showNotice('网络连接不稳定，任务仍在后台运行，稍后刷新即可继续查看');
          return;
        } catch (error) {
          const aborted = (error as { name?: string })?.name === 'AbortError' || controller.signal.aborted;
          // 切走/停止导致的中止：后台 Run 仍在跑，**保留 activeRun**（供切回重订阅、历史「运行中」点），
          // 不清、不记 finished；用户显式停止的清理由 stopChat 自己完成。
          if (aborted) return;
          // 有进展同样恢复重连额度（N-04）：真实网络异常（connection reset）与裸 EOF 同权——
          // 抖动网络每次连上都能收到新事件时，不该累计几次异常就永久放弃
          if (lastSeq > seqBefore) reconnects = 0;
          if (await delayBeforeReconnect()) continue;
          if (controller.signal.aborted) return;
          if (!terminalReplayed) {
            const verdict = await arbitrateRunTerminalState(threadId, runId);
            if (controller.signal.aborted) return;
            if (verdict.kind === 'waiting') {
              // 权威接口说 Run 在等用户：按挂起态收尾恢复 + 补卡（同上方裸 EOF 分支，N-07）
              setActiveRun(threadId, runId, verdict.status);
              void restoreWaitingInputFromServer(threadId, runId);
              return;
            }
            if (verdict.kind === 'running') {
              // 后端确认仍在跑（或状态都查不到=网络不通）：保留 activeRun 供刷新/切回续接，
              // 软提示即可——这不是 Run 的失败，不标红、不清 activeRun
              options.showNotice('网络连接不稳定，任务仍在后台运行，稍后刷新即可继续查看');
              return;
            }
            // 后端已不在跑（终态）：事件日志还在，最后再按游标续传一次拿最终回答
            terminalReplayed = true;
            arbitratedOutcome = verdict.outcome;
            arbitratedError = verdict.error;
            continue;
          }
          // 终态回放也失败：如实提示（正文已在后端落库，刷新可见），不伪造失败态——
          // 有权威终态时按它定格视觉，避免卡片停在进行中
          applyArbitratedOutcome();
          options.showNotice('本次回复已在后台完成，刷新或重新进入会话即可查看');
          settleRun(false, arbitratedOutcome);
          return;
        }
      }
    } finally {
      typewriter.stop();
      commentaryStream.stop();
      reasoningStream.stop();
      if (activeTypewriter === typewriter) activeTypewriter = null;
      if (activeCommentaryStream === commentaryStream) activeCommentaryStream = null;
      if (activeReasoningStream === reasoningStream) activeReasoningStream = null;
      if (visibleSubController === controller) {
        visibleSubController = null;
        visibleSubRunId = '';
      }
      if (visibleRunSubscriptions.get(runId) === controller) visibleRunSubscriptions.delete(runId);
      if (runControllers.get(runId) === controller) runControllers.delete(runId);
      if (runSegmentControllers.get(runId) === segmentController) runSegmentControllers.delete(runId);
      // 停止中禁用态兜底解除（第四批 2a）：pending 等收敛的订阅退出（终态/重连额度耗尽/
      // 切会话中止）后不再让停止按钮卡在禁用态——用户可重试停止
      clearStoppingState(runId);
      loadThreads();
    }
  }

  async function loadThread(threadId: string, silent = false): Promise<boolean> {
    // 已在该会话：只收口抽屉、回到对话板块，不重载——历史抽屉里点当前高亮会话是很自然的
    // “关抽屉”动作，走完整重载会断流重订阅、还静默清空知识库/文件/子智能体选择
    if (threadId && threadId === currentThreadId.value) {
      options.activeSection.value = 'chat';
      historyOpen.value = false;
      // 点当前会话也是一次「手动恢复」入口（N-03）：活动 Run 的订阅已死（重连额度耗尽/
      // 离线放弃）时按游标重订，不再让用户只能刷新页面。仅生成中状态需要；挂起态订阅
      // 会全量回放旧卡片事件，交由 loadThread/仲裁链路恢复，这里不碰。
      const active = activeRuns.value[threadId];
      if (active?.id && isGeneratingRunStatus(active.status) && !visibleRunSubscriptions.has(active.id)) {
        const cursor = runStreamCursors.get(active.id);
        void subscribeVisibleRun(
          threadId,
          active.id,
          cursor
            ? {
              afterSequence: cursor.seq,
              initialContent: cursor.content,
              reuseMessageId: cursor.messageId,
              contentOffset: cursor.contentOffset,
            }
            : undefined,
        );
      }
      return true;
    }
    // 陈旧性校验（同 runAssistantTurn）+ 最后点击者获胜（审计项 5）：入口处即推进
    // viewVersion——快速连点 A→B 时，A 的慢响应回来会发现令牌已过期而放弃；旧实现在
    // await 之后才推进，谁先返回谁获胜，用户最后点的 B 反而可能被 A 覆盖。
    viewVersion += 1;
    const viewToken = viewVersion;
    try {
      // 离开当前会话：断前台发送流 + **单独中止可见订阅流**（后者不是 abortController，
      // 不中止会留孤儿 SSE 连接、且切回被去重挡住写向幽灵消息导致界面卡死）。Run 在后端
      // 后台继续生成，回到该会话时由 subscribeVisibleRun 重订阅接管。
      abortController?.abort();
      abortController = null;
      stopVisibleSubscription();
      // 离开前暂存 composer 草稿（输入文字 + 待发附件）：切回原会话时恢复，
      // 上传的文档不再因为「切了下画面」默默消失（2026-07-15 用户报告）
      saveComposerDraft();
      persistComposerDraftNow(); // 落盘（D-01）：flush 防抖窗口，刷新/重启后仍可恢复
      if (!options.appList.value.length) await options.reloadApps();
      const [messages, threadSettings] = await Promise.all([
        getThreadMessages(threadId, threadScope),
        getThreadSettings(threadId, threadScope),
      ]);
      // 等待期间已有更晚的 loadThread/resetChat 生效（viewVersion 被它们推进）：本次已过期，
      // 不能再把界面覆盖回去，也不该继续往下订阅/清 activeRun（那是它们的事）。
      if (viewVersion !== viewToken) return false;
      if (fixedAssistantPreset && threadSettings.assistant_preset !== fixedAssistantPreset) {
        throw new Error('该会话不属于当前系统内置应用');
      }
      chatMessages.value = groupSupersededMessages(expandStoredExecutionSegments(messages));
      currentThreadId.value = threadId;
      assistantPreset.value = fixedAssistantPreset || threadSettings.assistant_preset;
      selectedWorkFolder.value = assistantPreset.value ? null : threadSettings.workspace_folder || null;
      activeModel.value = threadSettings.model || readDefaultModel();
      clearConversationSelections();
      restoreComposerDraft(threadId);
      // 内存草稿缺席（刷新/重启后首次进入该会话）：从本机存储回填（D-01）
      void restorePersistedThreadDraft(threadId);
      // 运行中消息队列（§B）：服务端持久化，进入会话即拉取回填（刷新/切回后仍在）
      await loadMessageQueue(threadId);
      if (currentThreadId.value !== threadId) return false;
      // 向服务端复核活动 Run，**不信任本地缓存的 activeRun 状态**：后台 Run 可能已完成落库，
      // 而本地 activeRun 因无观察者停留在 running（stale）→ 若据此重放已完成 Run，会与
      // getThreadMessages 拿到的落库回答重复渲染成两个气泡。
      const serverRun = await getThreadActiveRun(threadId, threadScope);
      // 只有当切换过程中用户没再切走时才落地订阅（复核是异步的，其间可能又切走）
      if (currentThreadId.value !== threadId) return false;
      if (serverRun?.id && isActiveRunStatus(serverRun.status)) {
        // 模型可能先给公开正文、随后才命中审批/补充边界：此时 MySQL 已有同 Run 的助手
        // 锚点，而 Runtime PG 仍权威地保持 waiting/running。历史投影和 SSE 若各建一条气泡，
        // 切走再回来就会把整段工具轨迹从头显示两遍。复用既有锚点；再从 Run State 的
        // 权威事件游标只回放末尾交互后缀，以恢复 approval/input 卡，不从第一步重新演一遍。
        setActiveRun(threadId, serverRun.id, serverRun.status, serverRun);
        // 从权威 Run 快照恢复 Profile，本地 Set 只补足创建后的短暂窗口。
        const phase = String(serverRun.phase || '');
        const planExecutionUnlocked = unlockedPlanRuns.has(serverRun.id)
          || chatMessages.value.some((item) => item.runId === serverRun.id && item.planExecutionUnlocked)
          || serverRun.agent_mode === 'standard'
          || phase === 'executing'
          || phase === 'verifying';
        if (planExecutionUnlocked) {
          unlockedPlanRuns.add(serverRun.id);
          planProfileRuns.delete(serverRun.id);
          planMode.value = false;
        } else {
          planMode.value = serverRun.agent_mode === 'plan' || planProfileRuns.has(serverRun.id);
        }
        researchProfile.value = serverRun.agent_mode === 'research' || researchProfileRuns.has(serverRun.id);
        if (getBuiltinUiPolicy(assistantPreset.value)?.hidePlanMode) planMode.value = false;
        if (getBuiltinUiPolicy(assistantPreset.value)?.hideResearch) researchProfile.value = false;
        // 活动 Run 在首条助手消息落库前，后端已把截止当前的完整执行轨迹
        // 附在本轮 run-bound 用户输入上；兼容旧数据时则返回临时助手轨迹投影。
        // 先把它恢复成当前快照，避免空占位后只展示游标尾段，或从 sequence=0
        // 把 commentary / reasoning / tool 按动画从头重演。已有助手锚点时同样用快照补齐。
        const persistedReplayTarget = findActiveRunReplayTarget(chatMessages.value, serverRun.id);
        const activeTrace = findActiveRunTraceSnapshot(messages, serverRun.id) as ExecutionTracePayload | undefined;
        const restoredTarget = restoreHarnessRunSnapshot(serverRun, activeTrace);
        const replayTarget = persistedReplayTarget || {
          messageId: restoredTarget.id,
          content: restoredTarget.content,
        };
        // 首选这份历史轨迹快照自带的同批游标，它与已恢复的步骤严格对齐；
        // 旧后端未回该字段时，再用 Run State 当前游标的小后缀兼容。
        let replayAfterSequence = Math.max(0, Number(activeTrace?.event_cursor || 0));
        let replayCursorResolved = activeTrace?.event_cursor != null;
        if (!replayCursorResolved) {
          try {
            const runState = await getRunState(serverRun.id);
            replayAfterSequence = Math.max(0, Number(runState.event_cursor || 0) - 8);
            replayCursorResolved = true;
          } catch {
            // 游标不可用时禁止退回 sequence=0 可见全量重播。下面延时重试一次；
            // 期间页面仍保留刚恢复的执行快照，不伪造“从头开始”。
          }
        }
        const subscribeFromCursor = (afterSequence: number) => subscribeVisibleRun(
          threadId,
          serverRun.id,
          {
            afterSequence,
            initialContent: replayTarget.content,
            reuseMessageId: replayTarget.messageId,
          },
        );
        if (replayCursorResolved) {
          void subscribeFromCursor(replayAfterSequence);
        } else {
          window.setTimeout(() => {
            if (viewVersion !== viewToken || currentThreadId.value !== threadId) return;
            void getRunState(serverRun.id)
              .then((runState) => {
                if (viewVersion !== viewToken || currentThreadId.value !== threadId) return;
                void subscribeFromCursor(Math.max(0, Number(runState.event_cursor || 0) - 8));
              })
              .catch(() => undefined);
          }, 1000);
        }
      } else {
        // 服务端已无活动 Run：清掉可能 stale 的本地 activeRun，停掉「后台回复中」滞留指示
        const stale = activeRuns.value[threadId];
        if (stale) clearActiveRun(threadId, stale.id);
        else refreshRunUi();
        // Older saved traces predate the error field. Recover the last failure
        // from its authoritative Run, without replaying or restarting the task.
        const oldFailure = [...messages].reverse().find((message) => message.role === 'assistant'
          && message.run_id && message.execution_trace?.status === 'failed'
          && !message.execution_trace?.error);
        if (oldFailure?.run_id) {
          void getRunState(String(oldFailure.run_id)).then((state) => {
            if (viewVersion !== viewToken || currentThreadId.value !== threadId) return;
            if (state.status !== 'failed' || !state.error) return;
            const target = chatMessages.value.find((message) => message.role === 'assistant'
              && message.runId === oldFailure.run_id);
            if (target) target.error = state.error;
          }).catch(() => undefined);
        }
        // 用户离开期间后台任务已结束、队列里还有待发消息：回到会话时立即补派发一次，
        // 不必非等下一次手动发送才触发（§B 空档兜底）
        if (messageQueue.value.length) void dispatchNextQueuedItem(threadId);
      }
      options.activeSection.value = 'chat';
      historyOpen.value = false;
      return true;
    } catch (error) {
      if (!silent) options.showError(error);
      return false;
    }
  }

  // 每个会话独立的临时选择/上下文态：新建对话或切换会话时清空，避免跨会话串味
  function clearConversationSelections() {
    selectedSkills.value = [];
    lastTurnSkills = []; // 不清会让 B 会话的「重新生成」沿用 A 会话上一轮的 skill
    lastTurnAttachments = []; // 同理：重新生成/消歧重发不能沿用别的会话的附件
    lastTurnHadAttachments.value = false;
    lastTurnThreads = []; // 同理：会话引用不能跨会话沿用
    lastTurnFiles = [];
    lastTurnKnowledge = [];
    selectedKnowledgeList.value = [];
    selectedFileList.value = [];
    selectedThreadList.value = [];
    selectedSubagent.value = undefined;
    openSubagents.value = [];
    pendingAttachments.value = [];
    webSearchOn.value = false;
    // 任务模式开关先归零，随后 loadThread 会按该会话的活动 Run 决定是否恢复（§A）；
    // 新建对话（resetChat）没有会话可恢复，保持关闭
    planMode.value = false;
    researchProfile.value = false;
    // 队列同理：先清空展示，loadThread 会重新拉取该会话自己的队列
    messageQueue.value = [];
    editingQueueId.value = '';
    editingQueueText.value = '';
  }

  function resetChat() {
    // 同 loadThread：新建对话离开当前会话，断前台发送流 + 中止可见订阅流（Run 后台继续）
    abortController?.abort();
    abortController = null;
    stopVisibleSubscription();
    saveComposerDraft(); // 原会话/原草稿的输入与附件留着，切回还在
    persistComposerDraftNow(); // 落盘（D-01）：flush 防抖窗口里的最后一次输入
    // 这一步是同步的：任何仍在 await 中的 loadThread 调用捕获的 viewToken 会在它自己的
    // await 之后立刻发现 viewVersion 已变，从而放弃把界面覆盖回旧会话（P0-5）。
    viewVersion += 1;
    chatMessages.value = [];
    chatInput.value = '';
    currentThreadId.value = '';
    selectedWorkFolder.value = null;
    assistantPreset.value = fixedAssistantPreset;
    activeModel.value = readDefaultModel();
    // 每次「新建对话」都是独立草稿实体（D-02）：分配全新 draftId，不复用/不覆盖旧匿名草稿
    // ——旧草稿仍在历史抽屉的「未发送草稿」分组里，可点回继续编辑
    currentDraftId.value = newAnonymousDraftId(threadScope);
    clearConversationSelections();
    restoreComposerDraft(currentDraftId.value); // 全新键必为空草稿
    refreshRunUi();
    return true;
  }

  function selectWorkFolder(folder: WorkFolderSelection | null) {
    if (chatLoading.value || restoringLatestThread.value || uploadingFile.value || uploadingWorkFolder.value || assistantPreset.value) return;
    if (selectedWorkFolder.value?.id !== folder?.id && (currentThreadId.value || chatMessages.value.length)) {
      resetChat();
    }
    selectedWorkFolder.value = folder ? { ...folder } : null;
  }

  function enterBuiltinAssistant(preset: AssistantPreset) {
    const assistant = getBuiltinAssistantByPreset(preset);
    if (!assistant || typeof window === 'undefined') return false;
    const opened = openAgentRunWindow(assistant.route);
    return Boolean(opened);
  }

  function enterPresentationAssistant() {
    return enterBuiltinAssistant(PRESENTATION_ASSISTANT_PRESET);
  }

  function enterCampusAssistant() {
    return enterBuiltinAssistant(CAMPUS_ASSISTANT_PRESET);
  }

  function startNewChatFromHistory() {
    resetChat();
    historyOpen.value = false;
  }

  /** 删除前等待取消收敛（第四批 2b）：轮询单 Run 权威终态 ≤ timeoutMs。收敛→true；
   *  超时/旧后端无接口→false（调用方照样放行删除，后端 delete 自身兜底）。 */
  async function waitForRunConvergence(runId: string, timeoutMs: number): Promise<boolean> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      await new Promise((resolve) => window.setTimeout(resolve, 500));
      try {
        const state = await getRunState(runId);
        if (['completed', 'failed', 'cancelled'].includes(String(state?.status))) return true;
      } catch {
        return false; // 旧后端无此接口：不空转等待，直接放行
      }
    }
    return false;
  }

  async function handleDeleteThread(threadId: string) {
    // 正在流式生成的会话：后端整段生成包在一个长事务里、锁住该会话行，直接 DELETE 会被行锁
    // 挡住挂起（前端表现为「删不掉」）。先取消它的活动 Run——cancelChatRun 会等后台任务真正
    // 收敛（事务回滚、锁释放），随后的 deleteThread 才不会卡死。取消模式对齐 stopChat。
    const runId =
      activeRuns.value[threadId]?.id ||
      threadList.value.find((t) => t.id === threadId)?.active_run?.id;
    if (runId) {
      runControllers.get(runId)?.abort(); // 立即断本地流（若正是当前可见会话）
      runControllers.delete(runId);
      clearActiveRun(threadId, runId);
      try {
        const res = await cancelChatRun(runId);
        if (res?.status === 'pending') {
          // 第四批 2b：停止已发出但执行未收敛——pending 不是「已停干净」的成功信号。
          // 短暂轮询单 Run 终态（≤3s）等收敛再删，减小 DELETE 撞行锁挂起的窗口；
          // 等不到也继续删（后端 delete 自身兜底）。
          await waitForRunConvergence(runId, 3000);
        }
      } catch {
        // 取消失败（如 Run 已自行结束）不阻断删除
      }
    }
    // 删除幂等：后端返回 404「会话不存在」= 该会话已经没了（已删/僵尸/未持久化），用户意图已达成，
    // 按删除成功同步本地列表，不弹错；只有真实错误（网络/500 等）才保留条目并提示。此前把 404
    // 也当错误弹出、不清列表，导致「删了还显示在里面」+「会话不存在」的前后端不同步。
    try {
      await deleteThread(threadId, threadScope);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      if (!msg.includes('不存在')) {
        options.showError(error);
        return;
      }
    }
    threadList.value = threadList.value.filter((thread) => thread.id !== threadId);
    clearActiveRun(threadId); // 清掉可能残留的「回复中」活动态
    // resetChat 会在离开当前会话前主动保存一次 composer；因此当前会话必须先 reset，
    // 再清理内存和 IndexedDB，否则刚删除的草稿会被 resetChat 立刻写回来。
    if (currentThreadId.value === threadId) resetChat();
    conversationDrafts.delete(threadId); // 会话没了，composer 草稿一并丢弃
    await deleteDraftRecord(`thread:${threadId}`);
    options.showNotice('对话已删除');
  }

  // 会话重命名：乐观回填 threadList 对应项标题
  async function handleRenameThread(threadId: string, title: string) {
    const clean = (title || '').trim();
    if (!clean) return;
    try {
      const saved = await renameThread(threadId, clean, threadScope);
      const target = threadList.value.find((thread) => thread.id === threadId);
      if (target) target.title = saved;
    } catch (error) {
      options.showError(error);
    }
  }

  function handleUseSkill(skill: SkillItem) {
    const builtin = getBuiltinAssistantByPreset(assistantPreset.value);
    if (builtin?.notices.useSkill) {
      options.activeSection.value = 'chat';
      options.showNotice(builtin.notices.useSkill);
      return;
    }
    // 不清 chatInput（审计项 13）：从 Skill 广场「在对话中使用」回来不该吞掉已敲的草稿
    selectedSkills.value = [skill];
    options.activeSection.value = 'chat';
    options.showNotice(`已启用 ${skill.name}`);
  }

  function handleUseKnowledge(knowledge: KnowledgeSelection) {
    const builtin = getBuiltinAssistantByPreset(assistantPreset.value);
    if (builtin?.notices.useKnowledge) {
      options.activeSection.value = 'chat';
      options.showNotice(builtin.notices.useKnowledge);
      return;
    }
    if (!selectedKnowledgeList.value.some((k) => k.id === knowledge.id)) {
      selectedKnowledgeList.value = [...selectedKnowledgeList.value, knowledge];
    }
    // 不清 chatInput（审计项 13）：同上
    options.activeSection.value = 'chat';
    options.showNotice('已启用知识库 ' + knowledge.name);
  }

  function updateSelectedKnowledge(list: KnowledgeSelection[]) {
    selectedKnowledgeList.value = list;
  }

  function updateSelectedFiles(list: UserFileSelection[]) {
    selectedFileList.value = limitFileSelection(list);
  }

  function updateSelectedThreads(list: ThreadReference[]) {
    selectedThreadList.value = list;
  }

  async function setMessageFeedback(messageId: number, value: 'up' | 'down' | null) {
    const target = chatMessages.value.find((item) => item.id === messageId);
    if (target) target.feedback = value;
    if (target?.dbId != null) {
      try {
        await submitMessageFeedback(target.dbId, value);
      } catch (error) {
        console.warn('Failed to persist feedback:', error);
      }
    }
  }

  function removeSelectedSkill(skillId: string) {
    selectedSkills.value = selectedSkills.value.filter((skill) => skill.id !== skillId);
  }

  function removeSelectedKnowledge(id: string) {
    selectedKnowledgeList.value = selectedKnowledgeList.value.filter((k) => k.id !== id);
  }

  async function loadSubagents(keyword?: string) {
    try {
      subagents.value = await getSubagents(keyword);
      subagentsLoaded = true;
    } catch (error) {
      console.warn('Failed to load subagents:', error);
      subagents.value = [];
    }
  }

  function ensureSubagentsLoaded() {
    if (getBuiltinUiPolicy(assistantPreset.value)?.hideSubagent) return;
    if (!subagentsLoaded) loadSubagents();
  }

  function selectSubagent(item: SubagentItem) {
    if (getBuiltinUiPolicy(assistantPreset.value)?.hideSubagent) return;
    selectedSubagent.value = item;
  }

  function removeSelectedSubagent() {
    selectedSubagent.value = undefined;
  }

  function openSubagent(item: SubagentItem, runKey?: string) {
    const existing = openSubagents.value.find((entry) => entry.id === item.id);
    if (existing) {
      existing.name = item.name || existing.name;
      existing.description = item.description || existing.description;
      existing.icon = item.icon || existing.icon;
      existing.runKey = runKey || existing.runKey;
      openSubagents.value = [...openSubagents.value];
      return;
    }
    if (openSubagents.value.length >= 3) {
      options.showNotice('最多同时打开 3 个子智能体过程窗口，请先关闭一个');
      return;
    }
    openSubagents.value = [...openSubagents.value, { ...item, runKey }];
  }

  function closeSubagent(id: string) {
    openSubagents.value = openSubagents.value.filter((item) => item.id !== id);
  }

  // @ 面板的 Skill 候选目录：与广场同源（/ai/skill/list 管理员已启用），首次 @ 时懒加载
  async function loadMentionSkills() {
    try {
      mentionSkills.value = await getSkills();
      mentionSkillsLoaded = true;
    } catch (error) {
      console.warn('Failed to load skills for @ picker:', error);
      mentionSkills.value = [];
    }
  }

  function ensureMentionSkillsLoaded() {
    if (getBuiltinUiPolicy(assistantPreset.value)?.hideSkillSelector) return;
    if (!mentionSkillsLoaded) loadMentionSkills();
  }

  // @ 选中 Skill：与广场「在对话中使用」同一套一次性语义（发送后随 selectedSkills 清空），
  // 仅入口不同。可 @ 多个 Skill，按 id 去重叠加。
  function selectSkillFromMention(skill: SkillItem) {
    if (getBuiltinUiPolicy(assistantPreset.value)?.hideSkillSelector) return;
    if (!selectedSkills.value.some((item) => item.id === skill.id)) {
      selectedSkills.value = [...selectedSkills.value, skill];
    }
  }

  /** 从 clarification 事件回放重建原轮一次性上下文（P2a）：技能 id 映射回 @ 面板已加载的
   *  完整技能对象（未加载则退回 id 桩，后端按 id 重校验，名称无所谓）；附件为文本 ref。 */
  function clarificationContextFromEvent(payload: Clarification): {
    skills: SkillItem[];
    attachments: Array<{ filename: string; text: string; kind?: string }>;
  } {
    const skills = (payload.skill_ids || []).map(
      (id) => mentionSkills.value.find((s) => s.id === id) || ({ id, name: '' } as SkillItem),
    );
    const attachments = (payload.attachments || []).map((a) => ({
      filename: a.filename,
      text: a.text,
      kind: a.kind,
    }));
    return { skills, attachments };
  }

  /** R5 消歧：用户在澄清卡里选定某智能体 → 以原问题带显式 subagent_id 重发主对话，
   *  后端把它当作显式 @ 委派该智能体。注意 R0 守卫对显式 subagent_id **不再豁免**（三轮
   *  评审：豁免会允许同一 Thread 并发第二个 Run）——但 waiting_clarification 不在活动集，
   *  消歧本轮已终态，重发不会被自己挡住，也不会再出第二张消歧卡。回答留在主对话，不开独立窗。 */
  async function chooseClarifiedAgent(messageId: number, option: { id: string; name: string }) {
    if (chatLoading.value) return;
    // 打断互斥（2026-07-26 并发审计）：stopChat 是「先同步 clearActiveRun（chatLoading 立刻
    // 变 false）、再 await cancelChatRun」，所以点停止后的那一两秒里 chatLoading 已是 false
    // 但取消还没收敛——此时本入口会与 cancel 并发发起新 Run，撞后端 R0。sendChat 早有这道
    // 等待环（见 stopInFlight 的排队循环），这三个入口漏了。
    if (stopInFlight) await stopInFlight;
    if (chatLoading.value || stopInFlight) return;

    const idx = chatMessages.value.findIndex((m) => m.id === messageId);
    if (idx < 0) return;
    let userText = '';
    for (let i = idx - 1; i >= 0; i -= 1) {
      if (chatMessages.value[i].role === 'user') {
        userText = String(chatMessages.value[i].content || '');
        break;
      }
    }
    const target = chatMessages.value[idx];
    // 原轮一次性上下文优先取卡片消息上的快照（出卡后又发过别的消息时 lastTurn* 已被覆盖）；
    // 快照缺失（后台重订阅收卡/刷新恢复）退回 lastTurn* 尽力而为
    const ctx = target?.clarificationContext;
    if (ctx) {
      lastTurnSkills = ctx.skills;
      lastTurnAttachments = ctx.attachments;
    }
    if (target) {
      target.clarification = undefined; // 选定后收起澄清卡
      target.clarificationContext = null;
    }
    if (!userText) return;
    const clarifiedBubble = composerBubbleAttachments({
      uploads: lastTurnAttachments.map((a) => ({
        filename: a.filename,
        kind: a.kind,
        previewUrl: a.preview_url || a.image_url,
        status: a.status,
        note: a.note,
        file_id: a.file_id,
      })),
      files: lastTurnFiles,
      threads: lastTurnThreads,
      knowledge: lastTurnKnowledge,
      skills: lastTurnSkills,
      subagent: option,
    });
    const clarifiedUserMessage: ChatMessage = {
      id: nextLocalId(),
      role: 'user',
      content: userText,
      attachments: clarifiedBubble.length ? clarifiedBubble : undefined,
    };
    chatMessages.value.push(clarifiedUserMessage);
    // reuseTurnContext：原轮的一次性 skill 与上传附件在首发时已消费清空，重发必须显式沿用
    void runAssistantTurn(userText, {
      subagentId: option.id,
      subagentName: option.name,
      reuseTurnContext: true,
      userMessageLocalId: clarifiedUserMessage.id,
    });
  }

  /** 审批敏感工具（§11）：通过/拒绝后收起卡片；通过后提示用户可让助手继续（同幂等键重试即执行） */
  async function submitApproval(messageId: number, approved: boolean) {
    const target = chatMessages.value.find((item) => item.id === messageId);
    const callId = target?.approval?.call_id;
    if (!callId) return;
    if (target) target.approval = null; // 乐观收起
    try {
      await decideGatewayApproval(callId, approved);
      options.showNotice(approved ? '已允许该操作，可让助手继续执行' : '已拒绝该操作');
    } catch (error) {
      if (target) target.approval = { call_id: callId }; // 失败回填，允许重试
      options.showError(error);
    }
  }

  function toggleWebSearch() {
    webSearchOn.value = !webSearchOn.value;
  }

  // 网络恢复自动续订（N-03）：online 一到就按游标重订当前
  // 会话仍在生成中的活动 Run——无需刷新即可继续看到后续输出。已有活订阅时幂等跳过。
  // （特性检测：单测环境的 window 桩可能没有 addEventListener）
  const handleOnlineReconnect = () => {
    const threadId = currentThreadId.value;
    const active = threadId ? activeRuns.value[threadId] : undefined;
    if (!threadId || !active?.id) return;
    if (!isGeneratingRunStatus(active.status)) return;
    if (visibleRunSubscriptions.has(active.id)) return;
    const cursor = runStreamCursors.get(active.id);
    void subscribeVisibleRun(
      threadId,
      active.id,
      cursor
        ? {
          afterSequence: cursor.seq,
          initialContent: cursor.content,
          reuseMessageId: cursor.messageId,
          contentOffset: cursor.contentOffset,
        }
        : undefined,
    );
  };
  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('online', handleOnlineReconnect);
  }

  // center 组件可能因路由/登录切换重新挂载。全局监听、timer 与流控制器必须随当前
  // effect scope 清理，否则旧实例会继续落盘旧草稿或发起幽灵订阅。
  if (getCurrentScope()) {
    onScopeDispose(() => {
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', handleDraftVisibilityChange);
      }
      if (typeof window !== 'undefined') {
        window.removeEventListener('beforeunload', handleDraftBeforeUnload);
        window.removeEventListener('online', handleOnlineReconnect);
      }
      if (draftPersistTimer != null) window.clearTimeout(draftPersistTimer);
      if (searchTimer != null) window.clearTimeout(searchTimer);
      clearChatTaskTimer();
      abortController?.abort();
      abortController = null;
      stopVisibleSubscription();
    });
  }

  function closeHistory() {
    historyOpen.value = false;
  }

  function handleHistoryKeyboard(event: KeyboardEvent) {
    if (event.key === 'Escape') closeHistory();
  }

  return {
    setInterviewInputProvider,
    sendInterviewMessage,
    activeModel,
    currentRunModel,
    handleModelChange,
    chatInput,
    chatMessages,
    chatLoading,
    restoringLatestThread,
    chatTaskState,
    activeRuns,
    isFinishedRun,
    currentRunPlan,
    threadList,
    threadsLoading,
    currentThreadId,
    currentDraftId,
    selectedWorkFolder,
    uploadingWorkFolder,
    selectWorkFolder,
    threadScope,
    assistantPreset,
    selectedSkills,
    selectedKnowledgeList,
    selectedFileList,
    selectedThreadList,
    selectedSubagent,
    openSubagents,
    subagents,
    mentionSkills,
    webSearchOn,
    pendingAttachments,
    lastTurnHadAttachments,
    uploadingFile,
    // 任务模式（A）
    planMode,
    togglePlanProfile,
    // Deep Research（+菜单显式开启）
    researchProfile,
    toggleResearchProfile,
    // 运行中消息队列（B）
    messageQueue,
    queueLoading,
    queueBusy,
    editingQueueId,
    editingQueueText,
    startQueueEdit,
    cancelQueueEdit,
    commitQueueEdit,
    removeQueueItem,
    moveQueueItemToInput,
    reorderQueueItems,
    queueCurrentMessage,
    // 中断后队列暂停（Codex 对标）：横幅 + 「继续」按钮的数据源
    queuePaused,
    resumeQueue,
    // 队列暂停中又发新消息的确认闸（Codex composer.pausedQueueSubmit）
    pausedQueuePrompt,
    answerPausedQueuePrompt,
    // 队列项派发失败态 + 重试（Codex isMessagePaused / queuedMessage.retry，逐条粒度）
    failedQueueItemId,
    retryQueueDispatch,
    // 跟进行为：运行中回车＝排队，点「调整方向」注入当前 Run
    followUpMode,
    setFollowUpMode,
    // 立即引导（C）
    submittingRunInput,
    canInstruct,
    submitActiveRunInput,
    instructQueueItem,
    historyOpen,
    threadSearch,
    threadHasMore,
    chatPlaceholder,
    // 未发送草稿（D-01/D-02）：历史抽屉「草稿」分组数据源与操作入口
    draftEntries,
    openDraft,
    discardDraft,
    sendChat,
    stopChat,
    // UI 的停止按钮走这个（会暂停队列派发）；stopChat 保留给内部打断-重发复用
    stopChatByUser,
    // 停止流程进行中（第四批 2a）：停止按钮禁用「停止中…」态数据源
    stopping,
    regenerateLast,
    resendEditedMessage,
    hasSuspendedRun,
    submitResume,
    loadThreads,
    restoreCurrentThread,
    loadMoreThreads,
    searchThreads,
    togglePin,
    loadThread,
    resetChat,
    enterPresentationAssistant,
    enterCampusAssistant,
    enterBuiltinAssistant,
    startNewChatFromHistory,
    handleDeleteThread,
    handleRenameThread,
    handleUseSkill,
    handleUseKnowledge,
    updateSelectedKnowledge,
    updateSelectedFiles,
    updateSelectedThreads,
    setMessageFeedback,
    loadSubagents,
    ensureSubagentsLoaded,
    selectSubagent,
    removeSelectedSubagent,
    openSubagent,
    closeSubagent,
    ensureMentionSkillsLoaded,
    selectSkillFromMention,
    chooseClarifiedAgent,
    submitApproval,
    toggleWebSearch,
    uploadFile,
    retryAttachment,
    removeAttachment,
    removeSelectedSkill,
    removeSelectedKnowledge,
    closeHistory,
    handleHistoryKeyboard,
    clearChatTaskTimer,
  };
}
