import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('KnowledgeList empty state', () => {
  it('does not offer knowledge creation in the empty shared-with-me scope', () => {
    const component = readFileSync(resolve(process.cwd(), 'src/views/peopleCenter/tabs/MyKnowledgeTab.vue'), 'utf8');
    expect(component).toContain('v-if="scope === \'owned\'" class="knowledge-create-card"');
    expect(component).toContain('v-if="scope === \'shared\' && !items.length"');
  });
});
