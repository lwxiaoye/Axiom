import type { InterviewInput, InterviewSnapshot, InterviewTurn } from './types';

/** 载荷在用户提交时冻结，服务端仍以版本与题目身份再次校验。 */
export function createInterviewAnswerInput(snapshot: InterviewSnapshot | null): InterviewInput | null {
  if (snapshot?.status !== 'active' || !snapshot.current_question?.id || !Number.isInteger(snapshot.version)) return null;
  return Object.freeze({
    action: 'answer',
    expected_version: snapshot.version,
    question_id: snapshot.current_question.id,
  });
}

/** Only whole, explicit shortcuts are controls; words inside an answer remain answer text. */
export function createInterviewComposerInput(snapshot: InterviewSnapshot | null, message = ''): InterviewInput | null {
  const answer = createInterviewAnswerInput(snapshot);
  if (!answer || !snapshot) return null;
  const text = message.trim().replace(/[。.!！]+$/, '');
  if (/^(?:请)?(?:给我(?:一个|一点)?提示|提示一下)(?:[，,]不要直接给完整答案)?$/.test(text)) {
    return Object.freeze({ ...answer, action: 'hint' });
  }
  if (/^(?:这道题我不知道[，,]?)?(?:先)?跳过(?:本题|这道题)?$/.test(text)) {
    return Object.freeze({ ...answer, action: 'skip' });
  }
  if (/^(?:压力有点大[，,])?(?:请)?降低(?:压力)?强度(?:[，,]换成正常问法)?$/.test(text)) {
    return Object.freeze({ ...answer, action: 'resume', pressure_level: 'gentle' });
  }
  if (/^(?:我想)?(?:根据刚才的反馈)?重新回答(?:这一题|这道题)$/.test(text)) {
    const previous = [...snapshot.turns].reverse().find((turn) => turn.action === 'answer');
    if (previous) return Object.freeze({ ...answer, action: 'retry', question_id: previous.question_id });
  }
  return answer;
}

export function latestEvaluatedInterviewTurn(turns: readonly InterviewTurn[]): InterviewTurn | null {
  return [...turns].reverse().find((turn) => turn.evaluation != null) || null;
}

/** Normalize legacy units for display only; the server owns all aggregate scores. */
export function interviewPercentage(score: number | null, scale: 5 | 100 = 5): number | null {
  if (score == null || !Number.isFinite(score) || score < (scale === 5 ? 1 : 0) || score > scale) return null;
  return Math.round(score * 100 / scale);
}

export function interviewDimensionDisplay(status: string, score: number | null, scale: 5 | 100 = 5): string {
  const percent = interviewPercentage(score, scale);
  if (status === 'scored' && percent != null) return `${percent} / 100`;
  const statuses: Record<string, string> = { not_assessed: '未考察', insufficient_evidence: '证据不足', unanswered: '未作答' };
  return statuses[status] || '待评价';
}

export const INTERVIEW_QUESTION_TYPES = { behavioral: '行为面试', professional: '专业面试', pressure: '压力情境' };

export function interviewCurrentQuestionLabel(snapshot: InterviewSnapshot): string {
  if (!snapshot.current_question) return '';
  const retrying = snapshot.turns.some((turn) => turn.action === 'answer' && turn.question_id === snapshot.current_question?.id);
  return `${retrying ? '重答 · ' : ''}第 ${snapshot.progress.current_number} 题${snapshot.current_question.parent_question_id ? ' · 追问' : ''}`;
}

export function interviewSessionMeta(snapshot: InterviewSnapshot): string {
  const { answered, total, current_number } = snapshot.progress;
  if (snapshot.status === 'preparing') return total ? `正在出题 · ${total} 题` : '正在出题';
  if (snapshot.status === 'active') return `第 ${current_number} / ${total} 题`;
  if (snapshot.status === 'paused') return `已暂停 · ${answered} / ${total}`;
  if (snapshot.status === 'completed') return `已结束 · ${answered} / ${total}`;
  return '';
}

export function interviewRoomWaiting(snapshot: InterviewSnapshot | null, busy: boolean, hasError = false): boolean {
  if (hasError || snapshot?.status === 'completed' || snapshot?.review) return false;
  if (snapshot?.current_question) return false;
  return busy || snapshot?.status === 'preparing' || snapshot == null;
}

export function isInterviewProgressConflict(failure: unknown): boolean {
  const status = (failure as { status?: number })?.status;
  if (status !== 409) return false;
  const message = failure instanceof Error ? failure.message : String((failure as { message?: unknown })?.message || '');
  return !/仍在执行|排队或先停止|加入排队/.test(message);
}

export interface InterviewQuestionStep {
  number: number;
  questionId: string | null;
  turnId: string | null;
  status: 'unasked' | 'current' | 'answered' | 'skipped';
  isCurrent: boolean;
}

/** Only public turns define question order; follow-ups and retries retain their root position. */
export function interviewQuestionSteps(snapshot: InterviewSnapshot): InterviewQuestionStep[] {
  const groups = new Map<string, InterviewTurn[]>();
  for (const turn of snapshot.turns) {
    const root = turn.question.parent_question_id || turn.question_id;
    const group = groups.get(root) || [];
    group.push(turn);
    groups.set(root, group);
  }
  const roots = [...groups.keys()];
  const currentRoot = snapshot.current_question?.parent_question_id || snapshot.current_question?.id;
  if (currentRoot && !roots.includes(currentRoot)) roots.splice(snapshot.progress.current_number - 1, 0, currentRoot);
  return Array.from({ length: snapshot.progress.total }, (_, index) => {
    const questionId = roots[index] || null;
    const turns = questionId ? groups.get(questionId) || [] : [];
    const isCurrent = Boolean(currentRoot && currentRoot === questionId);
    const answered = turns.some((turn) => turn.action === 'answer');
    return {
      number: index + 1,
      questionId,
      turnId: turns[turns.length - 1]?.id || null,
      status: answered ? 'answered' : turns.length ? 'skipped' : isCurrent ? 'current' : 'unasked',
      isCurrent,
    };
  });
}
