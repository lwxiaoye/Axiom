/**
 * 主对话「运行中消息队列」纯逻辑辅助（P2：从 useCenterChat 抽出可单测部分）。
 * 网络请求与 Vue 状态仍由 useCenterChat 持有；这里只放无副作用规则。
 */

export type QueueFollowUpMode = 'steer' | 'queue';

/** 运行中回车默认行为：invert 时取反。 */
export function resolveFollowUpIntent(
  mode: QueueFollowUpMode,
  invert?: boolean,
): QueueFollowUpMode {
  if (!invert) return mode;
  return mode === 'queue' ? 'steer' : 'queue';
}

/** 取消意图（挂起卡 free-text 放弃任务）。 */
export const CANCEL_INTENT_RE = /取消|算了|不做了|不请了|不请假了|放弃|不用了|退出|结束任务/;

/** 排队项是否可被「立即引导」发出（dispatching 中禁止双发）。 */
export function canInstructQueueItem(status: string | undefined): boolean {
  return status !== 'dispatching';
}

/** 本地队列乐观重排：orderedIds 必须与当前项一一对应，否则拒绝。 */
export function reorderQueueByIds<T extends { id: string }>(
  current: T[],
  orderedIds: string[],
): T[] | null {
  if (orderedIds.length !== current.length) return null;
  const map = new Map(current.map((item) => [item.id, item]));
  const next: T[] = [];
  for (const id of orderedIds) {
    const item = map.get(id);
    if (!item) return null;
    next.push(item);
  }
  if (orderedIds.every((id, i) => id === current[i]?.id)) return current;
  return next;
}

/**
 * 把指定队列项移到「移除它之后」的插入位置。
 * Pointer 拖拽和键盘上/下移共用同一条纯逻辑，避免视觉预览与最终持久化顺序不一致。
 */
export function moveQueueEntryToIndex<T extends { id: string }>(
  current: T[],
  sourceId: string,
  insertIndex: number,
): T[] {
  const sourceIndex = current.findIndex((item) => item.id === sourceId);
  if (sourceIndex < 0) return current;

  const next = [...current];
  const [source] = next.splice(sourceIndex, 1);
  const boundedIndex = Math.max(0, Math.min(next.length, Math.trunc(insertIndex)));
  next.splice(boundedIndex, 0, source);
  return next.every((item, index) => item.id === current[index]?.id) ? current : next;
}
