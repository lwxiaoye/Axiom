export type InterviewLevel = 'intern' | 'graduate' | 'experienced';
export type InterviewPressureLevel = 'gentle' | 'normal' | 'challenging';
export type InterviewAction = 'start' | 'answer' | 'skip' | 'pause' | 'resume' | 'finish' | 'retry' | 'hint';

export type InterviewConfig = {
  job_title: string;
  jd_text: string;
  resume_file_id: string;
  jd_file_id?: string;
  resume_notes?: string;
  question_count: number;
  level: InterviewLevel;
  pressure_level: InterviewPressureLevel;
};

export type InterviewInput = {
  action: InterviewAction;
  expected_version?: number;
  question_id?: string;
  config?: InterviewConfig;
  pressure_level?: InterviewPressureLevel;
};

export type InterviewDimensionKey = 'expression' | 'logic' | 'professional';
export type InterviewScoreStatus = 'scored' | 'not_assessed' | 'insufficient_evidence' | 'unanswered';

export type InterviewEvidence = { message_id: number; quote: string };

export type InterviewDimension = {
  status: InterviewScoreStatus;
  score: number | null;
  reason: string;
  evidence: InterviewEvidence[];
};

export type InterviewSourceReference = {
  kind: 'resume' | 'jd' | 'answer';
  quote: string;
  file_id?: string | null;
  message_id?: number | null;
};

export type InterviewQuestion = {
  id: string;
  type: 'behavioral' | 'professional' | 'pressure';
  text: string;
  competency: string;
  source_refs: InterviewSourceReference[];
  difficulty: 'easy' | 'medium' | 'hard';
  parent_question_id?: string | null;
};

export type InterviewEvaluation = {
  score_scale?: 100;
  dimensions: Record<InterviewDimensionKey, InterviewDimension>;
  feedback: string;
  strengths: string[];
  improvements: string[];
  sample_answer?: string;
};

export type InterviewTurn = {
  id: string;
  run_id: string;
  question_id: string;
  question: InterviewQuestion;
  answer_message_id: number;
  answer: string;
  attempt: number;
  assisted: boolean;
  action: InterviewAction;
  evaluation: InterviewEvaluation | null;
  committed_version: number;
};

export type InterviewScoreSummary = Record<InterviewDimensionKey, {
  average: number | null;
  assessed_count: number;
  assisted_average: number | null;
  assisted_count: number;
}>;

export type InterviewPerformance = {
  score: number | null;
  assisted_score: number | null;
  assessed_turns: number;
  assisted_turns: number;
  assessed_questions: number;
  assisted_questions: number;
  weights: Record<InterviewDimensionKey, number>;
};

export type InterviewReview = {
  summary: string;
  strengths: string[];
  improvements: string[];
  next_steps: string[];
  covered: string[];
  uncovered: string[];
  score_summary: InterviewScoreSummary;
};

export type InterviewSnapshot = {
  id: string | null;
  thread_id: string | null;
  version: number;
  status: 'not_started' | 'preparing' | 'active' | 'paused' | 'completed';
  config: InterviewConfig | null;
  pressure_level: InterviewPressureLevel | null;
  current_question: InterviewQuestion | null;
  profile: { summary: string; competencies: string[]; source_refs: InterviewSourceReference[] } | null;
  progress: { answered: number; total: number; skipped: number; followups: number; current_number: number };
  turns: InterviewTurn[];
  review: InterviewReview | null;
  score_summary: InterviewScoreSummary;
  score_scale?: 100;
  performance?: InterviewPerformance;
  latest_hint?: string | null;
  materials: { kind: 'resume' | 'jd'; file_id: string; filename: string; status: string; note: string; truncated: boolean }[];
};

export const INTERVIEW_DIMENSIONS: readonly { key: InterviewDimensionKey; label: string }[] = [
  { key: 'expression', label: '文字表达' },
  { key: 'logic', label: '逻辑思维' },
  { key: 'professional', label: '专业匹配' },
];

export const INTERVIEW_SCORE_STATUS: Record<InterviewScoreStatus, string> = {
  scored: '已评分',
  not_assessed: '未考察',
  insufficient_evidence: '证据不足',
  unanswered: '未作答',
};
