import type { RunSession } from './agentRun.api';

/**
 * 悬浮子智能体对话窗打开时的默认选中会话（纯函数，便于回归）：
 *  ① 优先当前主对话产生的委派会话（parentThreadId 匹配）；
 *  ② 否则回退最新的**非委派**会话（手动独立对话）；
 *  ③ 都没有则 null（交由调用方新建空对话）。
 *
 * 绝不自动选中「其他主对话的委派会话」或「孤儿委派会话」：委派记录在子智能体终态才落库，
 * 本轮委派进行中打开窗口时 ① 查不到；删来源主对话会清空委派会话的 parentThreadId，故判定
 * 委派身份用稳定的 origin='delegation' 标记，而非可空的 parentThreadId（三轮评审 P2b）。
 * sessions 约定按 updated_at 倒序（后端 run/sessions 已排序），故 find 命中即最新一条。
 */
export function pickInitialSession(
  sessions: RunSession[],
  parentThreadId?: string | null,
): string | null {
  const preferred = parentThreadId
    ? sessions.find((s) => s.parentThreadId === parentThreadId)
    : undefined;
  if (preferred) return preferred.id;
  const fallback = sessions.find((s) => s.origin !== 'delegation' && !s.parentThreadId);
  if (fallback) return fallback.id;
  return null;
}
