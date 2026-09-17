import { computed, onScopeDispose, ref, shallowRef, watch, type Ref } from 'vue';
import type { UploadedFile } from '../../agentApi';
import { getInterviewSession } from './api';
import { createInterviewAnswerInput, createInterviewComposerInput, isInterviewProgressConflict } from './session';
import { interviewStartMessage, validateInterviewConfig } from './setup';
import type { InterviewAction, InterviewConfig, InterviewInput, InterviewPressureLevel, InterviewSnapshot } from './types';

type InterviewSessionOptions = {
  currentThreadId: Ref<string>;
  chatLoading: Ref<boolean>;
  settledRunKey?: Readonly<Ref<string>>;
  sendInterviewMessage: (message: string, input: InterviewInput, attachments?: UploadedFile[]) => Promise<void>;
};

export function useInterviewSession(options: InterviewSessionOptions) {
  const snapshot = shallowRef<InterviewSnapshot | null>(null);
  const refreshing = ref(false);
  const submitting = ref(false);
  const error = ref('');
  const submissionConflict = ref('');
  let revision = 0;
  let request: AbortController | null = null;
  const busy = computed(() => options.chatLoading.value || submitting.value || refreshing.value);
  const canAnswer = computed(() => !busy.value && !error.value && !submissionConflict.value && Boolean(createInterviewAnswerInput(snapshot.value)));
  const hasSession = computed(() => Boolean(snapshot.value && snapshot.value.status !== 'not_started'));
  const composerPlaceholder = computed(() => {
    if (options.chatLoading.value || submitting.value) {
      return snapshot.value?.status === 'active' ? '可以先写下下一句…' : '正在准备题目…';
    }
    if (error.value) return '请先刷新面试状态后再回答';
    if (submissionConflict.value) return '草稿已保留，请先核对当前题目';
    if (snapshot.value?.status === 'paused') return '面试已暂停';
    if (snapshot.value?.status === 'completed') return '本场已结束';
    if (snapshot.value?.status === 'active') return '回答本题…';
    return '请先确认简历与岗位，开始模拟面试';
  });

  async function refresh() {
    const threadId = options.currentThreadId.value;
    const currentRevision = ++revision;
    request?.abort();
    request = null;
    if (!threadId) {
      snapshot.value = null;
      refreshing.value = false;
      error.value = '';
      return;
    }
    const controller = new AbortController();
    request = controller;
    refreshing.value = true;
    try {
      const value = await getInterviewSession(threadId, controller.signal);
      if (currentRevision !== revision || options.currentThreadId.value !== threadId) return;
      snapshot.value = value;
      error.value = '';
    } catch (failure) {
      if (currentRevision !== revision || controller.signal.aborted) return;
      error.value = failure instanceof Error ? failure.message : '面试状态暂时无法读取，请重试';
      if ((failure as { status?: number })?.status === 403) snapshot.value = null;
    } finally {
      if (currentRevision === revision) refreshing.value = false;
    }
  }

  async function send(message: string, input: InterviewInput, attachments?: UploadedFile[]) {
    if (busy.value) return;
    submitting.value = true;
    error.value = '';
    try {
      await options.sendInterviewMessage(message, input, attachments);
    } catch (failure) {
      if (isInterviewProgressConflict(failure) || (failure as { status?: number })?.status !== 409) {
        error.value = failure instanceof Error ? failure.message : '操作未完成，请重试';
      }
    } finally {
      submitting.value = false;
      await refresh();
    }
  }

  async function start(config: InterviewConfig, attachments: UploadedFile[]) {
    const invalid = validateInterviewConfig(config);
    if (invalid) { error.value = invalid; return; }
    await send(interviewStartMessage(config), Object.freeze({ action: 'start', config: { ...config } }), attachments);
  }

  async function act(action: Exclude<InterviewAction, 'start' | 'answer'>, questionId?: string, pressureLevel?: InterviewPressureLevel) {
    const state = snapshot.value;
    if (!state || busy.value || error.value || state.status === 'not_started') return;
    const labels = { skip: '跳过当前问题。', pause: '暂停本场面试，保留当前进度。', resume: '继续本场面试。', finish: '结束本场面试，请基于实际回答生成复盘。', retry: '我想根据反馈重新回答这道题，请保留原始回答与评分。', hint: '请给我一个提示，不要直接给完整答案。' };
    const input: InterviewInput = {
      action,
      expected_version: state.version,
      ...(questionId || state.current_question?.id ? { question_id: questionId || state.current_question!.id } : {}),
      ...(pressureLevel ? { pressure_level: pressureLevel } : {}),
    };
    await send(pressureLevel ? '请降低面试压力强度，继续练习。' : labels[action], Object.freeze(input));
  }

  function getAnswerInput(message = ''): InterviewInput | null {
    return canAnswer.value ? createInterviewComposerInput(snapshot.value, message) : null;
  }

  function onSubmissionRejected(failure: unknown) {
    if (!isInterviewProgressConflict(failure) || !snapshot.value?.current_question) return;
    submissionConflict.value = failure instanceof Error ? failure.message : '面试进度已更新，本次输入未提交。';
    persistSubmissionConflict(submissionConflict.value);
  }

  function persistSubmissionConflict(message: string) {
    if (typeof window === 'undefined' || !options.currentThreadId.value) return;
    const key = `interview:submission-conflict:${options.currentThreadId.value}`;
    try {
      if (message) window.localStorage.setItem(key, message);
      else window.localStorage.removeItem(key);
    } catch { /* The in-memory guard still protects this page when storage is unavailable. */ }
  }

  function acknowledgeCurrentQuestion() {
    if (!busy.value && !error.value && snapshot.value) {
      submissionConflict.value = '';
      persistSubmissionConflict('');
    }
  }

  watch(options.currentThreadId, () => {
    snapshot.value = null;
    submissionConflict.value = '';
    if (typeof window !== 'undefined' && options.currentThreadId.value) {
      try {
        const stored = window.localStorage.getItem(`interview:submission-conflict:${options.currentThreadId.value}`) || '';
        submissionConflict.value = /仍在执行|排队或先停止|加入排队/.test(stored) ? '' : stored;
        if (stored && !submissionConflict.value) window.localStorage.removeItem(`interview:submission-conflict:${options.currentThreadId.value}`);
      } catch { /* Continue with a fresh server snapshot if browser storage is unavailable. */ }
    }
    void refresh();
  }, { immediate: true });
  watch(options.chatLoading, (running, wasRunning) => {
    if (wasRunning && !running) void refresh();
  });
  if (options.settledRunKey) {
    // A recovered Run can finish after the original loading observer has stopped.
    watch(options.settledRunKey, (key, previous) => {
      if (key && key !== previous) void refresh();
    });
  }
  onScopeDispose(() => { revision += 1; request?.abort(); });

  return { snapshot, refreshing, submitting, error, submissionConflict, busy, canAnswer, hasSession, composerPlaceholder, refresh, start, act, getAnswerInput, onSubmissionRejected, acknowledgeCurrentQuestion };
}

export type InterviewSessionController = ReturnType<typeof useInterviewSession>;
export { InterviewSessionKey } from './context';
