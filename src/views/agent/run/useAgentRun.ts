import { computed, getCurrentScope, onScopeDispose, reactive, ref, watch } from 'vue';
import { message as antMessage } from 'ant-design-vue';
import {
  appendRunMessage,
  createRunSession,
  deleteRunSession,
  getRunApp,
  getRunMessages,
  getRunSessions,
  pinRunSession,
  renameRunSession,
  resumeRunDefinition,
  submitRunMessageFeedback,
  type RunAppMeta,
  type RunMessageStatus,
  type RunSession,
} from './agentRun.api';
import { runAgentStream, type RunInteractive, type RunResult } from './agentRunStream';
import { speakBrowserTts, stopBrowserTts } from '../../workflow/shared/browserTts';
import type { GeneratedFile } from '../../peopleCenter/agentApi';
import { createSmoothStreamText } from '../../peopleCenter/composables/smoothStreamText';
import { readUserScoped, writeUserScoped } from '../../peopleCenter/utils/userScopedStorage';
import { createSessionLiveRuns } from './sessionLiveRuns';

/**
 * 独立 Agent「运行栈」核心逻辑（会话列表 + 历史 + 已发布工作流流式执行 + 客户端落库 + HITL）。
 * 从 agent/run/index.vue 的 send/handleResult/ensureSession 等抽出。
 * 现状（2026-07-14 如实标注）：仅主对话 @ 打开的子智能体悬浮窗（SubagentChatPanel）在用；
 * 我的智能体运行页（/agent/run/:appId 的 index.vue）仍保留自己的一份重复实现，尚未迁移——
 * 修 bug 时两处都要看，否则行为会漂移（此处的删除保护/失败落历史/EOF 收尾运行页未必有）。
 * 两处共用同一套 /workflow/run/* 端点（会话按 app_id 维度落库）→ 对话历史天然同步。
 * 本 composable 只管状态与流程，不含任何模板/渲染，渲染交给各自组件。
 */

export type RunUiMessage = {
  id?: number;
  role: 'user' | 'assistant';
  content: string;
  turnId?: string | null;
  status?: RunMessageStatus;
  feedback?: 'up' | 'down' | null;
  senderType?: 'human' | 'work_agent' | null;
  pending?: boolean;
  failed?: boolean;
  /** 用户消息随附的附件卡元数据；文档正文仍只进模型输入。 */
  attachments?: Array<{
    filename: string;
    kind?: string;
    previewUrl?: string;
    status?: string;
    note?: string;
    fileId?: string;
  }>;
  generatedFiles?: GeneratedFile[];
};

function createTurnId() {
  return typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `turn-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function normalizeRunStatus(status: string | undefined): Exclude<RunMessageStatus, 'unknown'> {
  if (status === 'partial' || status === 'failed' || status === 'cancelled' || status === 'interrupted') return status;
  return 'completed';
}
/** 发送时随消息带的附件：text 为 /chat/upload 解析出的文本（图片=视觉转述/OCR） */
export type RunSendAttachment = {
  filename: string;
  kind?: string;
  text: string;
  previewUrl?: string;
  status?: string;
  note?: string;
  fileId?: string;
};

export function useAgentRun(getAppId: () => string) {
  const appMeta = ref<RunAppMeta | null>(null);
  const appLoading = ref(true);
  const loadError = ref('');
  const sessions = ref<RunSession[]>([]);
  const activeSessionId = ref('');
  const messages = ref<RunUiMessage[]>([]);
  const runningNodeLabel = ref('');
  const interactive = ref<RunInteractive | null>(null);
  const formValues = reactive<Record<string, any>>({});
  const runtimeVariableValues = reactive<Record<string, any>>({});
  const live = createSessionLiveRuns<RunUiMessage, RunInteractive>(activeSessionId);
  const running = live.running;
  const runningSessionIds = live.runningIds;

  const activeSession = computed(() => sessions.value.find((s) => s.id === activeSessionId.value) || null);
  const runnable = computed(() => !!appMeta.value && appMeta.value.status === 'published');
  const notRunnableReason = computed(
    () => loadError.value || (appMeta.value && appMeta.value.status !== 'published' ? '该智能体尚未发布，无法对话。' : '智能体不可用。'),
  );
  const welcomeText = computed(() => String(appMeta.value?.welcomeText || '').trim());
  const quickQuestions = computed(() =>
    Array.isArray(appMeta.value?.quickQuestions)
      ? appMeta.value!.quickQuestions!.map((item) => String(item || '').trim()).filter(Boolean)
      : [],
  );
  const runtimeVariableItems = computed(() => (appMeta.value?.variables || []).filter((item) => item?.key));
  const interactiveFormItems = computed<any[]>(() => {
    const params = interactive.value?.params as any;
    return params?.inputForm || params?.userInputForms || [];
  });

  function createUiMessage(m: RunUiMessage) {
    return reactive<RunUiMessage>(m);
  }

  function resetRuntimeVariables() {
    Object.keys(runtimeVariableValues).forEach((key) => delete runtimeVariableValues[key]);
    runtimeVariableItems.value.forEach((item) => {
      runtimeVariableValues[item.key] = item.defaultValue ?? (
        item.type === 'multipleSelect' ? [] : item.type === 'switch' ? false : undefined
      );
    });
  }

  // 运行变量是用户填的输入，按登录用户作用域写 sessionStorage（utils/userScopedStorage）：
  // 同一标签页换账号不能把别人填过的变量回填出来；退出登录随作用域一起清。
  function runtimeStorageKey(sessionId = activeSessionId.value) {
    return `agent-run:variables:${getAppId()}:${sessionId || 'new'}`;
  }

  function sessionStore(): Storage | null {
    try {
      return typeof sessionStorage === 'undefined' ? null : sessionStorage;
    } catch {
      return null;
    }
  }

  function restoreRuntimeVariables() {
    resetRuntimeVariables();
    try {
      const saved = JSON.parse(readUserScoped(runtimeStorageKey(), sessionStore()) || '{}');
      runtimeVariableItems.value.forEach((item) => {
        if (item.type !== 'password' && Object.prototype.hasOwnProperty.call(saved, item.key)) {
          runtimeVariableValues[item.key] = saved[item.key];
        }
      });
    } catch {
      // A corrupt browser cache should never block opening a subagent.
    }
  }

  watch(runtimeVariableValues, () => {
    if (!appMeta.value) return;
    const safe: Record<string, any> = {};
    runtimeVariableItems.value.forEach((item) => {
      if (item.type !== 'password') safe[item.key] = runtimeVariableValues[item.key];
    });
    writeUserScoped(runtimeStorageKey(), JSON.stringify(safe), sessionStore());
  }, { deep: true });

  function collectRuntimeVariables(extra?: Record<string, any>) {
    const values = { ...runtimeVariableValues, ...(extra || {}) };
    const missing = runtimeVariableItems.value.find((item) => {
      const value = values[item.key];
      return item.required && (value === undefined || value === null || value === '' || (Array.isArray(value) && !value.length));
    });
    if (missing) {
      antMessage.warning(`请填写：${missing.label || missing.key}`);
      return null;
    }
    return values;
  }

  function initialInteractiveFormValue(field: { type?: string; defaultValue?: any }) {
    if (field.defaultValue !== undefined) return field.defaultValue;
    if (field.type === 'switch') return false;
    if (field.type === 'multipleSelect' || field.type === 'timeRangeSelect' || isFileFormField(field)) return [];
    return undefined;
  }

  function isFileFormField(field: { type?: string; valueType?: string; key?: string; label?: string }) {
    const type = String(field.type || '').trim();
    if (['fileSelect', 'file', 'fileInput', 'uploadFile', 'upload'].includes(type)) return true;
    const keyText = `${field.key || ''} ${field.label || ''}`.toLowerCase();
    const looksLikeFileField = keyText.includes('file') || keyText.includes('文件') || keyText.includes('附件');
    return field.valueType === 'arrayString' && looksLikeFileField;
  }

  function syncInteractiveFormValues(fields = interactiveFormItems.value) {
    Object.keys(formValues).forEach((key) => delete formValues[key]);
    fields.forEach((field) => {
      if (!field?.key) return;
      formValues[field.key] = initialInteractiveFormValue(field);
    });
  }

  async function loadApp() {
    appLoading.value = true;
    loadError.value = '';
    try {
      appMeta.value = await getRunApp(getAppId());
      restoreRuntimeVariables();
    } catch (e: any) {
      loadError.value = e?.response?.data?.detail || '无法加载该智能体或没有访问权限。';
    } finally {
      appLoading.value = false;
    }
  }

  async function loadSessions(keyword?: string) {
    try {
      sessions.value = await getRunSessions(getAppId(), keyword || undefined);
    } catch {
      sessions.value = [];
    }
  }

  async function selectSession(id: string) {
    const parked = live.get(id);
    activeSessionId.value = id;
    if (parked) {
      messages.value = parked.messages;
      runningNodeLabel.value = parked.nodeLabel;
      interactive.value = parked.interactive;
      syncInteractiveFormValues(parked.interactive ? undefined : []);
      return;
    }
    runningNodeLabel.value = '';
    interactive.value = null;
    syncInteractiveFormValues([]);
    try {
      const rows = await getRunMessages(id);
      messages.value = rows.map((r) => createUiMessage({
        id: typeof r.id === 'number' ? r.id : Number(r.id) || undefined,
        role: r.role,
        content: r.content,
        turnId: r.turnId,
        status: r.status,
        feedback: r.feedback,
        senderType: r.senderType,
        attachments: r.attachments?.length
          ? r.attachments.map((attachment) => ({
              filename: attachment.filename,
              kind: attachment.kind,
              previewUrl: attachment.preview_url,
              status: attachment.status,
              note: attachment.note,
              fileId: attachment.file_id,
            }))
          : undefined,
        generatedFiles: r.generatedFiles?.filter((file) => file?.id && file?.filename && file.deliverable !== false) || undefined,
      }));
    } catch {
      messages.value = [];
    }
  }

  function newSession() {
    live.nextView();
    activeSessionId.value = '';
    messages.value = [];
    interactive.value = null;
    runningNodeLabel.value = '';
    syncInteractiveFormValues([]);
    restoreRuntimeVariables();
  }

  async function removeSession(id: string) {
    // 正在跑的会话不允许删：后台流仍会往这个 session 落库
    if (live.get(id)) {
      antMessage.warning('该会话正在运行，请先停止再删除');
      return;
    }
    await deleteRunSession(id);
    if (id === activeSessionId.value) {
      activeSessionId.value = '';
      messages.value = [];
    }
    await loadSessions();
  }

  async function pinSession(id: string) {
    const target = sessions.value.find((session) => session.id === id);
    const next = !target?.pinned;
    await pinRunSession(id, next);
    if (target) target.pinned = next;
    sessions.value = [...sessions.value].sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)));
  }

  async function renameSession(id: string, title: string) {
    const saved = await renameRunSession(id, title);
    const nextTitle = String(saved?.title || title).trim();
    const target = sessions.value.find((session) => session.id === id);
    if (target && nextTitle) target.title = nextTitle;
  }

  async function ensureSession(firstText: string, viewAtStart: number, sessionAtStart: string): Promise<string> {
    if (sessionAtStart) return sessionAtStart;
    const s = await createRunSession(getAppId(), firstText.slice(0, 30) || '新对话');
    sessions.value.unshift(s);
    if (live.view.value === viewAtStart && !activeSessionId.value) {
      activeSessionId.value = s.id;
    }
    return s.id;
  }

  function resultText(result: RunResult) {
    return result.output || result.errorMessage || '（无输出）';
  }

  function handleResult(result: RunResult, assistantMsg: RunUiMessage, sessionId: string) {
    const parked = live.get(sessionId);
    const thread = parked?.messages || messages.value;
    if (activeSessionId.value === sessionId) runningNodeLabel.value = '';
    if (parked) parked.nodeLabel = '';
    if (result.interactive) {
      const idx = thread.indexOf(assistantMsg);
      if (idx >= 0) thread.splice(idx, 1);
      live.setInteractive(sessionId, result.interactive);
      if (activeSessionId.value === sessionId) {
        interactive.value = result.interactive;
        syncInteractiveFormValues();
      }
      return;
    }
    const text = resultText(result);
    if (assistantMsg.content !== text) assistantMsg.content = text;
    assistantMsg.pending = false;
    assistantMsg.failed = result.status === 'failed';
    const persistedStatus = normalizeRunStatus(result.status);
    assistantMsg.status = persistedStatus;
    assistantMsg.generatedFiles = result.files?.filter((file) => file?.id && file?.filename && file.deliverable !== false) || [];
    if (!assistantMsg.failed && activeSessionId.value === sessionId) {
      speakBrowserTts(text, appMeta.value?.ttsConfig);
    }
    // 失败也落历史（此前只落成功）：否则刷新/重开后会话里只剩用户问题，失败上下文全丢
    appendRunMessage(sessionId, 'assistant', text, undefined, {
      runId: result.runId,
      turnId: assistantMsg.turnId || undefined,
      status: persistedStatus,
      generatedFiles: assistantMsg.generatedFiles,
    }).then((saved) => {
      if (saved?.id) assistantMsg.id = saved.id;
    }).catch(() => {});
  }

  async function send(text: string, opts?: { attachments?: RunSendAttachment[]; variables?: Record<string, any>; replaceFromIndex?: number }) {
    const clean = (text || '').trim();
    // 新上传文件优先传受控 file_id：服务端按当前用户归属重新读取正文/OCR，
    // 不信任客户端回传的文本。只有旧历史附件没有 ID 时才兼容正文回退。
    const atts = (opts?.attachments || []).filter((a) => a && a.filename);
    const userFileIds = Array.from(new Set(atts.map((a) => String(a.fileId || '').trim()).filter(Boolean)));
    const legacyAttBlocks = atts
      .filter((a) => !a.fileId)
      .filter((a) => (a.text || '').trim())
      .map((a) => `【用户随消息发来了文件《${a.filename}》，完整内容如下——请直接基于该内容回答，不要再向用户索要文件】\n${a.text}`)
      .join('\n\n');
    const viewAtStart = live.view.value;
    const sessionAtStart = activeSessionId.value;
    if ((!clean && !legacyAttBlocks && !userFileIds.length) || live.isBusy(sessionAtStart, viewAtStart) || !runnable.value) return false;
    const runtimeVariables = collectRuntimeVariables(opts?.variables);
    if (!runtimeVariables) return false;
    stopBrowserTts();
    live.markStarting(viewAtStart);
    if (activeSessionId.value === sessionAtStart) {
      runningNodeLabel.value = '';
      interactive.value = null;
    }
    const thread = messages.value;
    const replaceFrom = opts?.replaceFromIndex;
    let truncateFromId: number | undefined;
    let removedTail: RunUiMessage[] = [];
    if (typeof replaceFrom === 'number' && replaceFrom >= 0 && replaceFrom < thread.length) {
      const from = thread[replaceFrom];
      if (from?.role !== 'user') {
        live.clearStarting(viewAtStart);
        return false;
      }
      truncateFromId = from.id;
      removedTail = thread.splice(replaceFrom);
    }

    let sessionId = '';
    try {
      sessionId = await ensureSession(clean || atts[0]?.filename || '附件', viewAtStart, sessionAtStart);
    } catch {
      if (removedTail.length) thread.splice(replaceFrom ?? thread.length, 0, ...removedTail);
      antMessage.error('创建会话失败');
      live.clearStarting(viewAtStart);
      return false;
    }
    const turnId = createTurnId();
    const userMsg = createUiMessage({
      role: 'user',
      content: clean,
      turnId,
      attachments: atts.length
        ? atts.map((a) => ({
            filename: a.filename,
            kind: a.kind,
            previewUrl: a.previewUrl,
            status: a.status,
            note: a.note,
            fileId: a.fileId,
          }))
        : undefined,
    });
    thread.push(userMsg);
    // 落库只存键入文本（附件正文只进模型输入，不污染历史；下一轮需重新附）；
    // 纯附件无文本时落一行占位，避免会话标题/历史空白
    const storeText = clean || `[附件] ${atts.map((a) => a.filename).join('、')}`;
    const storedAttachments = atts.map((a) => ({
      filename: a.filename,
      kind: a.kind,
      status: a.status,
      note: a.note,
      file_id: a.fileId,
      preview_url: a.previewUrl,
    }));
    appendRunMessage(
      sessionId,
      'user',
      storeText,
      storedAttachments.length ? storedAttachments : undefined,
      { turnId, ...(truncateFromId ? { truncateFromId } : {}) },
    )
      .then((r) => {
        if (r?.id) userMsg.id = r.id;
        const s = sessions.value.find((x) => x.id === sessionId);
        if (s && r?.title) s.title = r.title;
      })
      .catch(() => {});

    // 历史不含刚推入的当前问题（按引用排除，兼容纯附件无文本时 userMsg.content 为空的情况）
    const histories = thread
      .filter((m) => m !== userMsg && m.content && !m.pending)
      .map((m) => ({ role: m.role, content: m.content }));
    const modelInput = legacyAttBlocks ? (clean ? `${clean}\n\n${legacyAttBlocks}` : legacyAttBlocks) : clean;
    const assistantMsg = createUiMessage({ role: 'assistant', content: '', turnId, pending: true });
    thread.push(assistantMsg);

    const abort = new AbortController();
    live.start(sessionId, {
      abort,
      messages: thread,
      nodeLabel: '',
      view: viewAtStart,
      interactive: null,
    });
    live.clearStarting(viewAtStart);
    let finalResult: RunResult | null = null;
    let streamContent = '';
    const streamText = createSmoothStreamText({
      initialContent: assistantMsg.content,
      commit: (content) => { assistantMsg.content = content; },
    });
    try {
      await runAgentStream(
        {
          appId: getAppId(),
          input: modelInput,
          variables: { ...runtimeVariables, histories, ...(userFileIds.length ? { userFileIds } : {}) },
          sessionId,
        },
        {
          signal: abort.signal,
          onNode: (n) => {
            const label = n.nodeLabel || n.nodeType || '运行中';
            const parked = live.get(sessionId);
            if (parked) parked.nodeLabel = label;
            if (activeSessionId.value === sessionId) runningNodeLabel.value = label;
          },
          onDelta: (chunk) => {
            if (!chunk) return;
            assistantMsg.pending = false;
            streamContent += chunk;
            streamText.push(streamContent);
          },
          onResult: (r) => {
            finalResult = r;
          },
        },
      );
      if (finalResult) {
        if (finalResult.interactive) streamText.stop();
        else await streamText.finish(resultText(finalResult));
        handleResult(finalResult, assistantMsg, sessionId);
      } else {
        // 流正常 EOF 但始终没等到 result 事件（代理截断而 fetch 未抛错）：显式收尾为失败，
        // 否则气泡永久 pending 且不落历史
        const parked = live.get(sessionId);
        if (parked) parked.nodeLabel = '';
        if (activeSessionId.value === sessionId) runningNodeLabel.value = '';
        const interruptedText = streamContent || '连接中断，未收到运行结果，请重试';
        await streamText.finish(interruptedText);
        assistantMsg.content = interruptedText;
        assistantMsg.failed = true;
        assistantMsg.pending = false;
        assistantMsg.status = 'interrupted';
        appendRunMessage(sessionId, 'assistant', assistantMsg.content, undefined, {
          turnId,
          status: 'interrupted',
        }).then((saved) => {
          if (saved?.id) assistantMsg.id = saved.id;
        }).catch(() => {});
      }
    } catch (e: any) {
      const failedText = streamContent || (
        e?.name === 'AbortError' ? '（已停止）' : e?.message || '运行失败'
      );
      await streamText.finish(failedText);
      if (e?.name === 'AbortError') {
        assistantMsg.content = failedText;
        assistantMsg.status = 'cancelled';
        if (sessionId) appendRunMessage(sessionId, 'assistant', assistantMsg.content, undefined, {
          turnId,
          status: 'cancelled',
        }).then((saved) => {
          if (saved?.id) assistantMsg.id = saved.id;
        }).catch(() => {});
      } else {
        assistantMsg.content = failedText;
        assistantMsg.failed = true;
        assistantMsg.status = 'failed';
        // 传输层失败同样落历史，与 handleResult 的失败落库保持一致
        if (sessionId) appendRunMessage(sessionId, 'assistant', assistantMsg.content, undefined, {
          turnId,
          status: 'failed',
        }).then((saved) => {
          if (saved?.id) assistantMsg.id = saved.id;
        }).catch(() => {});
      }
      assistantMsg.pending = false;
    } finally {
      streamText.stop();
      if (!live.get(sessionId)?.interactive) live.finish(sessionId);
      if (activeSessionId.value === sessionId) runningNodeLabel.value = '';
    }
    return true;
  }

  async function submitInteractive(optionValue: string | null) {
    if (!interactive.value || running.value) return;
    const current = interactive.value;
    const sessionId = activeSessionId.value;
    if (!sessionId || live.isBusy(sessionId)) return;
    let value: any;
    if (current.type === 'userSelect') {
      value = optionValue;
    } else {
      const missing = interactiveFormItems.value.find((f) => f.required && !String(formValues[f.key] || '').trim());
      if (missing) {
        antMessage.warning(`请填写：${missing.label || missing.key}`);
        return;
      }
      value = { ...formValues };
    }

    const viewAtStart = live.view.value;
    const thread = messages.value;
    const assistantMsg = createUiMessage({ role: 'assistant', content: '', turnId: createTurnId(), pending: true });
    thread.push(assistantMsg);
    interactive.value = null;
    const abort = new AbortController();
    live.start(sessionId, {
      abort,
      messages: thread,
      nodeLabel: '',
      view: viewAtStart,
      interactive: null,
    });
    try {
      const result = (await resumeRunDefinition({ appId: getAppId(), sessionId, resumeId: current.resumeId, value })) as RunResult;
      handleResult(result, assistantMsg, sessionId);
    } catch (e: any) {
      assistantMsg.content = e?.response?.data?.detail || e?.message || '恢复执行失败';
      assistantMsg.failed = true;
      assistantMsg.pending = false;
      assistantMsg.status = 'failed';
      appendRunMessage(sessionId, 'assistant', assistantMsg.content, undefined, {
        turnId: assistantMsg.turnId || undefined,
        status: 'failed',
      }).then((saved) => {
        if (saved?.id) assistantMsg.id = saved.id;
      }).catch(() => {});
    } finally {
      live.finish(sessionId);
    }
  }

  function stop() {
    const id = activeSessionId.value;
    if (id) live.abortSession(id);
    stopBrowserTts();
  }

  async function updateMessageFeedback(id: number, value: 'up' | 'down' | null) {
    try {
      await submitRunMessageFeedback(id, value);
      const target = messages.value.find((item) => item.id === id);
      if (target) target.feedback = value;
    } catch (error: any) {
      antMessage.error(error?.message || '反馈提交失败');
    }
  }

  function stopAll() {
    live.abortAll();
    stopBrowserTts();
  }

  if (getCurrentScope()) onScopeDispose(stopAll);

  /** 打开时初始化：加载 app → 若可运行则拉会话列表并载入最近一条会话 */
  async function init() {
    await loadApp();
    if (runnable.value) {
      await loadSessions();
      if (sessions.value.length) await selectSession(sessions.value[0].id);
    }
  }

  return {
    appMeta,
    appLoading,
    loadError,
    sessions,
    activeSessionId,
    messages,
    running,
    runningSessionIds,
    runningNodeLabel,
    interactive,
    formValues,
    runtimeVariableValues,
    activeSession,
    runnable,
    notRunnableReason,
    welcomeText,
    quickQuestions,
    runtimeVariableItems,
    interactiveFormItems,
    collectRuntimeVariables,
    loadApp,
    loadSessions,
    selectSession,
    newSession,
    removeSession,
    pinSession,
    renameSession,
    send,
    submitInteractive,
    updateMessageFeedback,
    stop,
    stopAll,
    init,
  };
}
