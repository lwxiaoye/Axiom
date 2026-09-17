import {
  canEditKnowledge,
  canManageKnowledge,
  canUseKnowledge,
  normalizeKnowledgePermission,
} from './knowledgePermission';

describe('knowledge permission helpers', () => {
  it('enforces the viewer, editor, owner capabilities', () => {
    expect(canUseKnowledge('VIEWER')).toBe(true);
    expect(canEditKnowledge('VIEWER')).toBe(false);
    expect(canEditKnowledge('EDITOR')).toBe(true);
    expect(canManageKnowledge('EDITOR')).toBe(false);
    expect(canManageKnowledge('OWNER')).toBe(true);
  });

  it('normalizes the legacy USER value to VIEWER during rollout', () => {
    expect(normalizeKnowledgePermission('USER')).toBe('VIEWER');
  });
});
