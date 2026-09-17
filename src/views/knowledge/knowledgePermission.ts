import type { KnowledgePermission } from './knowledge.types';

export function normalizeKnowledgePermission(value: unknown): KnowledgePermission | undefined {
  const permission = String(value || '').trim().toUpperCase();
  if (permission === 'USER') return 'VIEWER';
  return ['VIEWER', 'EDITOR', 'OWNER'].includes(permission)
    ? permission as KnowledgePermission
    : undefined;
}

export function canUseKnowledge(permission?: KnowledgePermission): boolean {
  return Boolean(permission);
}

export function canEditKnowledge(permission?: KnowledgePermission): boolean {
  return permission === 'EDITOR' || permission === 'OWNER';
}

export function canManageKnowledge(permission?: KnowledgePermission): boolean {
  return permission === 'OWNER';
}
