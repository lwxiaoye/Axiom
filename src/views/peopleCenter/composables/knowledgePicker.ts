type KnowledgeOptionLike = {
  id: string;
  chunkCount?: number;
};

/** 只有已产生可检索分段的知识库才能带进主对话。 */
export function isKnowledgeReady(knowledge: KnowledgeOptionLike): boolean {
  return Number(knowledge.chunkCount || 0) > 0;
}

export function readyKnowledgeOptions<T extends KnowledgeOptionLike>(options: T[]): T[] {
  return options.filter(isKnowledgeReady);
}

/**
 * 知识库清单刷新后移除已删除、已失权或尚未产生分段的旧选中项。
 * 返回 null 表示选中态没有变化。
 */
export function pruneKnowledgeSelection<T extends { id: string }>(
  selected: T[],
  options: KnowledgeOptionLike[],
): T[] | null {
  const readyIds = new Set(readyKnowledgeOptions(options).map((item) => item.id));
  const kept = selected.filter((item) => readyIds.has(item.id));
  return kept.length === selected.length ? null : kept;
}
