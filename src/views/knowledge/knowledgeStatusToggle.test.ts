import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('knowledge base enablement', () => {
  it('exposes separate user and management status endpoints', () => {
    const api = readFileSync(resolve(process.cwd(), 'src/views/knowledge/knowledge.api.ts'), 'utf8');

    expect(api).toContain("baseEnable: '/ai/knowledge/base/enable'");
    expect(api).toContain("baseDisable: '/ai/knowledge/base/disable'");
    expect(api).toContain('export const setKnowledgeEnabled');
    expect(api).toContain('export const setManagedKnowledgeEnabled');
  });

  it('lets owners change status and keeps disabled bases out of retrieval UI', () => {
    const myKnowledge = readFileSync(resolve(process.cwd(), 'src/views/peopleCenter/tabs/MyKnowledgeTab.vue'), 'utf8');

    expect(myKnowledge).toContain('changeKnowledgeStatus');
    expect(myKnowledge).toContain('const isCurrentKnowledgeActive');
  });
});
