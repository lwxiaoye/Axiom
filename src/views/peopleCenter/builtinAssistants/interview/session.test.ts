import { effectScope, nextTick, ref } from 'vue';
import { getInterviewSession } from './api';
import { useInterviewSession } from './useInterviewSession';
import { createInterviewAnswerInput, createInterviewComposerInput, interviewDimensionDisplay } from './session';
import { validateInterviewConfig, validateInterviewDocument, validateInterviewMaterial } from './setup';
import type { InterviewSnapshot } from './types';

jest.mock('./api', () => ({ getInterviewSession: jest.fn() }));
const loadSession = getInterviewSession as jest.MockedFunction<typeof getInterviewSession>;

function snapshot(threadId = 'thread-a', version = 3): InterviewSnapshot {
  const summary = { average: null, assessed_count: 0, assisted_average: null, assisted_count: 0 };
  return {
    id: 'session-a', thread_id: threadId, version, status: 'active',
    config: { job_title: '前端实习生', jd_text: '需要 Vue 开发经验', resume_file_id: 'resume-a', question_count: 5, level: 'intern', pressure_level: 'normal' },
    pressure_level: 'normal', profile: null,
    current_question: { id: 'q1', type: 'professional', text: '你如何排查页面卡顿？', competency: '性能分析', source_refs: [{ kind: 'jd', quote: '需要 Vue 开发经验' }], difficulty: 'medium' },
    progress: { answered: 0, total: 5, skipped: 0, followups: 0, current_number: 1 },
    turns: [], review: null, materials: [],
    score_summary: { expression: { ...summary }, logic: { ...summary }, professional: { ...summary } },
  };
}

async function settle() { await Promise.resolve(); await nextTick(); await Promise.resolve(); }

describe('interview submission and material boundaries', () => {
  it('freezes the displayed question and version before later state changes', () => {
    const state = snapshot();
    const input = createInterviewAnswerInput(state);
    state.version = 9;
    state.current_question!.id = 'q9';
    expect(input).toEqual({ action: 'answer', expected_version: 3, question_id: 'q1' });
    expect(Object.isFrozen(input)).toBe(true);
  });

  it.each(['not_started', 'preparing', 'paused', 'completed'] as const)('does not authorize an answer while %s', (status) => {
    expect(createInterviewAnswerInput({ ...snapshot(), status })).toBeNull();
  });

  it('does not display missing evidence as a zero score', () => {
    expect(interviewDimensionDisplay('not_assessed', null)).toBe('未考察');
    expect(interviewDimensionDisplay('insufficient_evidence', null)).toBe('证据不足');
    expect(interviewDimensionDisplay('unanswered', null)).toBe('未作答');
    expect(interviewDimensionDisplay('scored', null)).toBe('待评价');
    expect(interviewDimensionDisplay('scored', 4)).toBe('80 / 100');
    expect(interviewDimensionDisplay('scored', 4, 100)).toBe('4 / 100');
    expect(interviewDimensionDisplay('scored', 0, 100)).toBe('0 / 100');
    expect(interviewDimensionDisplay('scored', 101, 100)).toBe('待评价');
    expect(interviewDimensionDisplay('scored', NaN, 100)).toBe('待评价');
  });

  it('recognizes explicit text controls without reinterpreting words inside answers', () => {
    expect(createInterviewComposerInput(snapshot(), '请给我一个提示，不要直接给完整答案。')?.action).toBe('hint');
    expect(createInterviewComposerInput(snapshot(), '这道题我不知道，先跳过。')?.action).toBe('skip');
    expect(createInterviewComposerInput(snapshot(), '压力有点大，请降低强度，换成正常问法')?.pressure_level).toBe('gentle');
    expect(createInterviewComposerInput(snapshot(), '我会给用户一个提示，让他降低强度，而不是跳过校验。')?.action).toBe('answer');
  });

  it('requires durable readable resume material and supported documents', () => {
    expect(validateInterviewDocument({ name: 'resume.exe', size: 8 })).toBeTruthy();
    expect(validateInterviewDocument({ name: 'resume.PDF', size: 8 })).toBe('');
    expect(validateInterviewDocument({ name: 'resume.md', size: 8 })).toBe('');
    expect(validateInterviewDocument({ name: 'resume.txt', size: 8 })).toBe('');
    expect(validateInterviewMaterial({ filename: 'resume.pdf', kind: 'document', text: '简历', chars: 2, truncated: false })).toBeTruthy();
    expect(validateInterviewMaterial({ file_id: 'file-a', filename: 'resume.pdf', kind: 'document', text: '', chars: 0, truncated: false, status: 'failed' })).toBeTruthy();
    expect(validateInterviewConfig({ ...snapshot().config!, jd_text: '' })).toBeTruthy();
    expect(validateInterviewConfig({ ...snapshot().config!, question_count: 2 })).toBeTruthy();
  });
});

describe('interview snapshot observation', () => {
  beforeEach(() => loadSession.mockReset());

  it('ignores an older history response after switching threads', async () => {
    let resolveFirst!: (value: InterviewSnapshot) => void;
    loadSession.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }));
    loadSession.mockResolvedValueOnce(snapshot('thread-b', 8));
    const scope = effectScope();
    const currentThreadId = ref('thread-a');
    const chatLoading = ref(false);
    const controller = scope.run(() => useInterviewSession({ currentThreadId, chatLoading, sendInterviewMessage: jest.fn() }))!;
    currentThreadId.value = 'thread-b';
    await settle();
    resolveFirst(snapshot('thread-a', 1));
    await settle();
    expect(controller.snapshot.value?.thread_id).toBe('thread-b');
    expect(controller.getAnswerInput()?.expected_version).toBe(8);
    scope.stop();
  });

  it('keeps submission disabled until the saved snapshot is refreshed after a run', async () => {
    loadSession.mockResolvedValue(snapshot('thread-a', 3));
    const scope = effectScope();
    const currentThreadId = ref('thread-a');
    const chatLoading = ref(false);
    const controller = scope.run(() => useInterviewSession({ currentThreadId, chatLoading, sendInterviewMessage: jest.fn() }))!;
    await settle();
    expect(controller.getAnswerInput()?.expected_version).toBe(3);
    chatLoading.value = true;
    await settle();
    expect(controller.getAnswerInput()).toBeNull();
    loadSession.mockResolvedValueOnce(snapshot('thread-a', 4));
    chatLoading.value = false;
    await settle();
    expect(controller.getAnswerInput()?.expected_version).toBe(4);
    scope.stop();
  });

  it('drops private session data and blocks answers after a permission rejection', async () => {
    loadSession.mockResolvedValueOnce(snapshot());
    const scope = effectScope();
    const controller = scope.run(() => useInterviewSession({ currentThreadId: ref('thread-a'), chatLoading: ref(false), sendInterviewMessage: jest.fn() }))!;
    await settle();
    loadSession.mockRejectedValueOnce(Object.assign(new Error('无权访问'), { status: 403 }));
    await controller.refresh();
    expect(controller.snapshot.value).toBeNull();
    expect(controller.getAnswerInput()).toBeNull();
    expect(controller.error.value).toBe('无权访问');
    scope.stop();
  });

  it('refreshes a recovered run result when the original loading observer is already idle', async () => {
    const preparing = { ...snapshot(), status: 'preparing' as const, current_question: null };
    loadSession.mockResolvedValueOnce(preparing);
    const scope = effectScope();
    const chatLoading = ref(false);
    const settledRunKey = ref('');
    const controller = scope.run(() => useInterviewSession({
      currentThreadId: ref('thread-a'), chatLoading, settledRunKey, sendInterviewMessage: jest.fn(),
    }))!;
    await settle();
    expect(controller.getAnswerInput()).toBeNull();
    loadSession.mockResolvedValueOnce(snapshot('thread-a', 4));
    settledRunKey.value = 'recovered-run:123';
    await settle();
    expect(chatLoading.value).toBe(false);
    expect(controller.getAnswerInput()).toEqual({ action: 'answer', expected_version: 4, question_id: 'q1' });
    scope.stop();
  });

  it('ignores an in-flight run conflict while the first question is still being prepared', async () => {
    const preparing = { ...snapshot(), status: 'preparing' as const, current_question: null };
    loadSession.mockResolvedValueOnce(preparing);
    const scope = effectScope();
    const controller = scope.run(() => useInterviewSession({ currentThreadId: ref('thread-a'), chatLoading: ref(false), sendInterviewMessage: jest.fn() }))!;
    await settle();
    controller.onSubmissionRejected(Object.assign(new Error('当前会话仍在执行，请继续引导、排队或先停止当前任务。'), { status: 409 }));
    expect(controller.submissionConflict.value).toBe('');
    expect(controller.getAnswerInput()).toBeNull();
    scope.stop();
  });

  it('keeps a stale answer blocked after refresh until the updated question is reviewed', async () => {
    loadSession.mockResolvedValueOnce(snapshot());
    const scope = effectScope();
    const controller = scope.run(() => useInterviewSession({ currentThreadId: ref('thread-a'), chatLoading: ref(false), sendInterviewMessage: jest.fn() }))!;
    await settle();
    controller.onSubmissionRejected(Object.assign(new Error('面试进度已更新'), { status: 409 }));
    const next = snapshot('thread-a', 4);
    next.current_question!.id = 'q2';
    loadSession.mockResolvedValueOnce(next);
    await controller.refresh();
    expect(controller.snapshot.value?.current_question?.id).toBe('q2');
    expect(controller.getAnswerInput()).toBeNull();
    expect(controller.submissionConflict.value).toBe('面试进度已更新');
    controller.onSubmissionRejected(Object.assign(new Error('当前会话仍在执行，请继续引导、排队或先停止当前任务。'), { status: 409 }));
    expect(controller.submissionConflict.value).toBe('面试进度已更新');
    controller.acknowledgeCurrentQuestion();
    expect(controller.getAnswerInput()).toEqual({ action: 'answer', expected_version: 4, question_id: 'q2' });
    scope.stop();
  });
});
