import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('knowledge management API boundary', () => {
  const api = readFileSync(resolve(process.cwd(), 'src/views/knowledge/knowledge.api.ts'), 'utf8');

  it('keeps management requests under the server-side admin namespace', () => {
    expect(api).toContain("const ManagementApiPrefix = '/ai/knowledge/admin'");
    expect(api).toContain('export const getManagedKnowledgeList');
    expect(api).toContain('export const getManagedDocumentList');
    expect(api).toContain('export const getManagedKnowledgeAcl');
    expect(api).toContain('export const setManagedKnowledgeEnabled');
  });

  it('makes the backend management page use management APIs', () => {
    const list = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeList.vue'), 'utf8');
    const detail = readFileSync(resolve(process.cwd(), 'src/views/knowledge/components/KnowledgeDetail.vue'), 'utf8');
    expect(list).toContain('getManagedKnowledgeList');
    expect(detail).toContain('getManagedKnowledgeDetail');
    expect(detail).toContain(':management="true"');
    expect(detail).toContain('setManagedKnowledgeEnabled');
  });
});
