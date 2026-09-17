import type { InterviewSnapshot } from './types';

// Export user text literally: Markdown images, links and HTML in an answer are not export instructions.
function literal(value: string): string {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/[\\`*_{}\[\]()#+!|]/g, '\\$&');
}
function points(values: string[]): string { return values.map((value) => `- ${literal(value)}`).join('\n') || '暂无记录'; }
export function completedInterviewReport(snapshot?: InterviewSnapshot | null) {
  return snapshot?.status === 'completed' ? snapshot.review : null;
}

export function interviewReportFilename(jobTitle: string): string {
  const title = jobTitle.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').trim().slice(0, 80);
  return `${title || '模拟面试'}-面试复盘.md`;
}

export function buildInterviewReportMarkdown(snapshot: InterviewSnapshot): string {
  const review = completedInterviewReport(snapshot);
  if (!review) throw new Error('本场复盘尚未生成');
  const lines = [
    `# ${literal(snapshot.config?.job_title || '模拟面试')} · 面试复盘`,
    `已答 ${snapshot.progress.answered} / ${snapshot.progress.total} 道主问题 · 跳过 ${snapshot.progress.skipped} 题 · 追问 ${snapshot.progress.followups} 次`,
    '## 整体表现', snapshot.performance?.score != null ? `${snapshot.performance.score} / 100` : '暂无足够依据生成综合分',
    literal(review.summary),
    '## 做得好的地方', points(review.strengths),
    '## 需要提升的地方', points(review.improvements),
    ...(review.next_steps.length ? ['## 下一步练习', points(review.next_steps)] : []),
    '## 评分说明',
    '综合分依据整场实际回答与追问，按专业匹配 50%、逻辑思维 30%、文字表达 20% 汇总。未作答、未考察或证据不足不计零分；首次表现与辅导后练习分别统计。',
    ...(snapshot.performance?.assisted_score != null ? [`辅导后练习：${snapshot.performance.assisted_score} / 100`] : []),
  ];
  return `${lines.join('\n\n')}\n`;
}
