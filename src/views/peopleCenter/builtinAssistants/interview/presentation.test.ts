import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { InterviewQuestion, InterviewSnapshot, InterviewTurn } from './types';
import { interviewCurrentQuestionLabel, interviewQuestionSteps, interviewRoomWaiting, interviewSessionMeta, isInterviewProgressConflict } from './session';
import { buildInterviewReportMarkdown, completedInterviewReport, interviewReportFilename } from './report';
import { interviewStartMessage } from './setup';

function question(id: string, parent?: string): InterviewQuestion {
  return { id, type: 'professional', text: '你如何验证这次优化？', competency: '性能验证', difficulty: 'medium', source_refs: [], ...(parent ? { parent_question_id: parent } : {}) };
}
function turn(id: string, q: InterviewQuestion, action: 'answer' | 'skip' = 'answer', assisted = false): InterviewTurn {
  return { id, run_id: 'private-run-id', question_id: q.id, question: q, answer_message_id: 101, answer: action === 'answer' ? '只觉得页面更快，没有测量。' : '', action, assisted, attempt: assisted ? 2 : 1, committed_version: 3, evaluation: {
    feedback: '需要说明如何验证结果。', strengths: ['能说明证据边界'], improvements: ['补充可复现的测量方法'],
    dimensions: {
      expression: { status: 'scored', score: 3, reason: '叙述清楚', evidence: [{ message_id: 101, quote: '没有测量' }] },
      logic: { status: 'scored', score: 2, reason: '缺少验证过程', evidence: [] },
      professional: { status: 'insufficient_evidence', score: null, reason: '没有可以评估的专业方法', evidence: [] },
    },
  } };
}
function snapshot(): InterviewSnapshot {
  const scores = { average: null, assessed_count: 0, assisted_average: null, assisted_count: 0 };
  return {
    id: 'private-session-id', thread_id: 'private-thread-id', version: 3, status: 'active',
    config: { job_title: '前端实习生', jd_text: 'private-full-jd', resume_notes: 'private-resume-notes', resume_file_id: 'private-file-id', question_count: 5, level: 'intern', pressure_level: 'normal' },
    pressure_level: 'normal', profile: null, current_question: question('q1'),
    progress: { answered: 0, total: 5, skipped: 0, followups: 0, current_number: 1 },
    turns: [], materials: [], review: null,
    score_summary: { expression: { ...scores, average: 3, assessed_count: 1, assisted_average: 4, assisted_count: 1 }, logic: { ...scores }, professional: { ...scores } },
  };
}

describe('interview practice navigation', () => {
  it('keeps follow-ups and retries in the original main-question position', () => {
    const state = snapshot();
    state.turns = [turn('a1', question('q1')), turn('followup', question('q1-followup', 'q1')), turn('skip2', question('q2'), 'skip'), turn('retry1', question('q1'), 'answer', true)];
    state.current_question = question('q1-followup', 'q1');
    state.progress.current_number = 1;
    const steps = interviewQuestionSteps(state);
    expect(steps).toHaveLength(5);
    expect(steps[0]).toMatchObject({ number: 1, questionId: 'q1', status: 'answered', isCurrent: true, turnId: 'retry1' });
    expect(steps[1]).toMatchObject({ number: 2, questionId: 'q2', status: 'skipped', isCurrent: false });
    expect(steps[2]).toMatchObject({ questionId: null, turnId: null, status: 'unasked' });
    expect(interviewCurrentQuestionLabel(state)).toBe('重答 · 第 1 题 · 追问');
  });

  it('keeps the setup form, then uses the shared chat transcript after start', () => {
    const chatTab = readFileSync(resolve(__dirname, '../../tabs/ChatTab.vue'), 'utf8');
    const setup = readFileSync(resolve(__dirname, 'InterviewSetup.vue'), 'utf8');
    expect(setup).toContain('上传或拖入简历');
    expect(setup).toContain('开始模拟面试');
    expect(setup).not.toContain('class="read-status"');
    expect(setup).not.toContain('查看读取内容');
    expect(setup).not.toContain('或上传 JD 文档');
    expect(chatTab).toContain('v-if="interviewMode && interview && !interview.hasSession.value"');
    const messageListOpen = chatTab.match(/<MessageList[\s\S]*?v-if="[^"]+"/)?.[0] || '';
    expect(messageListOpen).toContain('v-if="chatMessages.length > 0"');
    expect(messageListOpen).not.toContain('interviewMode');
    expect(chatTab).toContain(':has-conversation="chatMessages.length > 0"');
    const timeline = readFileSync(resolve(__dirname, '../../composables/executionTimeline.ts'), 'utf8');
    expect(timeline).toContain('get_interview_session:');
    expect(timeline).toContain('commit_interview_turn:');
    const messageList = readFileSync(resolve(__dirname, '../../components/MessageList.vue'), 'utf8');
    expect(messageList).toContain("from '../composables/executionIconKind'");
    expect(messageList).toContain(':kind="executionIconKind(row.step)"');
    expect(messageList).not.toContain('function executionIconKind');
    expect(chatTab).not.toContain('InterviewSessionBar');
    expect(chatTab).toContain('<InterviewMenu');
    const report = readFileSync(resolve(__dirname, 'InterviewReport.vue'), 'utf8');
    expect(report).toContain('class="report-toolbar"');
    expect(report).toContain('class="download-action"');
    expect(report).toContain('下一步练习');
    expect(report).not.toContain('class="report-links"');
    expect(report).not.toContain('justify-content: space-between');
    const panel = readFileSync(resolve(__dirname, 'InterviewPanel.vue'), 'utf8');
    expect(panel).not.toContain('class="answer-details"');
    expect(panel).not.toContain('InterviewFeedback');
    expect(panel).not.toContain('<select');
    expect(chatTab).not.toContain('@show-feedback');
  });

  it('uses one status line and treats preparing without a question as the waiting room', () => {
    const preparing = snapshot();
    preparing.status = 'preparing';
    preparing.current_question = null;
    expect(interviewSessionMeta(preparing)).toBe('正在出题 · 5 题');
    expect(interviewRoomWaiting(preparing, true)).toBe(true);
    expect(interviewSessionMeta(snapshot())).toBe('第 1 / 5 题');
    expect(interviewRoomWaiting(snapshot(), true)).toBe(false);
    expect(interviewRoomWaiting(null, true)).toBe(true);
    expect(interviewRoomWaiting(null, true, true)).toBe(false);
  });

  it('does not treat an in-flight chat run as an interview answer conflict', () => {
    expect(isInterviewProgressConflict(Object.assign(new Error('面试进度已更新'), { status: 409 }))).toBe(true);
    expect(isInterviewProgressConflict(Object.assign(new Error('当前会话仍在执行，请继续引导、排队或先停止当前任务。'), { status: 409 }))).toBe(false);
    expect(isInterviewProgressConflict(Object.assign(new Error('面试进度已更新'), { status: 500 }))).toBe(false);
  });

  it('marks a retried skipped question as answered and leaves future questions undisclosed', () => {
    const state = snapshot();
    state.turns = [turn('skip1', question('q1'), 'skip'), turn('retry1', question('q1'), 'answer', true)];
    state.current_question = question('q2');
    state.progress.current_number = 2;
    expect(interviewQuestionSteps(state).map(({ status }) => status)).toEqual(['answered', 'current', 'unasked', 'unasked', 'unasked']);
    expect(interviewQuestionSteps(state)[4]).toEqual({ number: 5, questionId: null, turnId: null, status: 'unasked', isCurrent: false });
  });

  it('puts the target job at the start of new conversation titles without losing setup choices', () => {
    const text = interviewStartMessage(snapshot().config!);
    expect(text.startsWith('前端实习生 · 模拟面试（5 题）')).toBe(true);
    expect(text).toContain('实习求职 · 标准面试');
  });
});

describe('interview review export', () => {
  function completed(): InterviewSnapshot {
    const state = snapshot();
    state.status = 'completed';
    state.current_question = null;
    state.turns = [turn('first', question('q1')), turn('assisted', question('q1'), 'answer', true)];
    state.review = { summary: '你能把调试和协作中的具体做法讲清楚，验证结果还需要补上对照条件。', strengths: ['说明了怎样定位旧响应覆盖，也解释了团队为什么先修阻断问题。'], improvements: ['补充对照条件，让排查过程可以重复验证。'], next_steps: ['写出一次完整的对照验证过程。'], covered: ['性能验证', '团队合作'], uncovered: [], score_summary: state.score_summary };
    return state;
  }

  it('exports the saved whole-interview assessment without per-question records or private material', () => {
    const report = buildInterviewReportMarkdown(completed());
    expect(report).toContain(completed().review!.summary);
    expect(report).toContain('团队为什么先修阻断问题');
    expect(report).toContain('## 做得好的地方');
    expect(report).toContain('## 需要提升的地方');
    expect(report).not.toContain('逐答记录');
    expect(report).not.toContain('## 维度评分');
    expect(report).not.toContain(completed().turns[0].answer);
    expect(report).not.toContain(completed().turns[0].question.text);
    expect(report).not.toContain('private-');
    expect(report).not.toContain('101');
  });

  it('treats model report markup as literal text', () => {
    const state = completed();
    state.review!.summary = '![hidden](https://example.test/tracker)\n<script>alert(1)</script>';
    const report = buildInterviewReportMarkdown(state);
    expect(report).not.toContain('![hidden](');
    expect(report).not.toContain('<script>');
    expect(report).toContain('&lt;script&gt;');
  });

  it('uses only the server aggregate and keeps coached performance separate', () => {
    const state = completed();
    state.score_scale = 100;
    state.performance = { score: 73, assisted_score: 88, assessed_turns: 3, assessed_questions: 2, assisted_turns: 1, assisted_questions: 1, weights: { professional: 50, logic: 30, expression: 20 } };
    state.score_summary.expression.average = 62;
    state.turns[1].evaluation!.score_scale = 100;
    state.turns[1].evaluation!.dimensions.expression.score = 4;
    const report = buildInterviewReportMarkdown(state);
    expect(report).toContain('## 整体表现\n\n73 / 100');
    expect(report).toContain('辅导后练习：88 / 100');
    expect(report).not.toContain('文字表达：60 / 100');
    expect(report).not.toContain('文字表达：4 / 100');
    state.performance.score = null;
    expect(buildInterviewReportMarkdown(state)).toContain('暂无足够依据生成综合分');
    expect(buildInterviewReportMarkdown(state)).not.toContain('\n\n0 / 100');
  });

  it('rejects an unfinished review and makes a filename safe for local downloads', () => {
    expect(() => buildInterviewReportMarkdown(snapshot())).toThrow('本场复盘尚未生成');
    expect(interviewReportFilename('前端/实习生:一组')).toBe('前端_实习生_一组-面试复盘.md');
    expect(interviewReportFilename('')).toBe('模拟面试-面试复盘.md');
  });

  it('does not reveal a report until the interview is completed, including stale review data', () => {
    const state = completed();
    expect(completedInterviewReport(state)).toBe(state.review);
    for (const status of ['active', 'paused', 'preparing', 'not_started'] as const) {
      state.status = status;
      expect(completedInterviewReport(state)).toBeNull();
      expect(() => buildInterviewReportMarkdown(state)).toThrow('本场复盘尚未生成');
    }
    expect(completedInterviewReport(null)).toBeNull();
    state.status = 'completed';
    state.review = null;
    expect(completedInterviewReport(state)).toBeNull();
  });

  it('does not reassemble old criticisms when the final review has no remaining issues', () => {
    const state = completed();
    state.review!.strengths = [];
    state.review!.improvements = [];
    expect(completedInterviewReport(state)?.improvements).toEqual([]);
    const report = buildInterviewReportMarkdown(state);
    expect(report).not.toContain(state.turns[0].evaluation!.improvements[0]);
    expect(report).toContain('## 需要提升的地方\n\n暂无记录');
  });
});
