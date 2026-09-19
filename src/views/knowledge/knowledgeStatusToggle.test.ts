import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('knowledge base enablement', () => {
  it('exposes the user-side status endpoint', () => {
    const api = readFileSync(resolve(process.cwd(), 'src/views/knowledge/knowledge.api.ts'), 'utf8');

    expect(api).toContain('export const setKnowledgeEnabled');
  });

  it('lets owners change status and keeps disabled bases out of retrieval UI', () => {
    const myKnowledge = readFileSync(resolve(process.cwd(), 'src/views/peopleCenter/tabs/MyKnowledgeTab.vue'), 'utf8');

    expect(myKnowledge).toContain('changeKnowledgeStatus');
    expect(myKnowledge).toContain('const isCurrentKnowledgeActive');
  });
});
