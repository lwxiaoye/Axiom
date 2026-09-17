import { getKnowledgeList } from '../../knowledge/knowledge.api';
import type { KnowledgeBase, KnowledgePermission } from '../../knowledge/knowledge.types';

export type WorkflowSelectableKnowledgeOption = {
  id: string;
  name: string;
  description?: string;
  permission?: KnowledgePermission;
  chunkCount?: number;
  [key: string]: any;
};

type KnowledgeBaseRecord = KnowledgeBase & {
  knowledgeName?: string;
};

function resolvePermission(item: KnowledgeBase): KnowledgePermission | undefined {
  return (
    item.currentPermission ||
    item.accessPermission ||
    item.aclPermission ||
    item.sharePermission ||
    item.permission ||
    undefined
  );
}

export function mergeWorkflowSelectableKnowledgeOptions(
  ownedRecords: KnowledgeBase[] = [],
  sharedRecords: KnowledgeBase[] = []
): WorkflowSelectableKnowledgeOption[] {
  const merged: KnowledgeBaseRecord[] = [
    ...ownedRecords,
    ...sharedRecords,
  ];
  const seen = new Set<string>();
  return merged
    .filter((item) => item?.id && item.status !== 'DISABLED')
    .filter((item) => {
      const id = String(item.id);
      if (seen.has(id)) {
        return false;
      }
      seen.add(id);
      return true;
    })
    .map((item) => ({
      ...item,
      id: String(item.id),
      name: String(item.name || item.knowledgeName || item.id),
      description: item.description,
      permission: resolvePermission(item),
      chunkCount: Number(item.chunkCount || 0),
    }));
}

export async function loadWorkflowSelectableKnowledgeOptions(
  pageSize = 100
): Promise<WorkflowSelectableKnowledgeOption[]> {
  const [owned, shared] = await Promise.all([
    getKnowledgeList({ pageNo: 1, pageSize, scope: 'owned' }),
    getKnowledgeList({ pageNo: 1, pageSize, scope: 'shared' }),
  ]);
  return mergeWorkflowSelectableKnowledgeOptions(owned?.records || [], shared?.records || []);
}
