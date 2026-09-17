import fs from 'node:fs';
import path from 'node:path';

describe('My Knowledge analytics ownership boundary', () => {
  it('only mounts analytics for the actual owner identity', () => {
    const source = fs.readFileSync(path.join(process.cwd(), 'src/views/peopleCenter/tabs/MyKnowledgeTab.vue'), 'utf8');
    expect(source).toContain('const isActualOwner = computed');
    expect(source).toContain('ownerId === currentUserId');
    expect(source).toContain("item.key === 'analytics'");
    expect(source).toContain('v-else-if="activeView === \'analytics\' && isActualOwner"');
    expect(source).toContain('activeView.value = \'documents\'');
  });

  it('keeps long analytics content scrollable inside the detail panel', () => {
    const styles = fs.readFileSync(path.join(process.cwd(), 'src/views/peopleCenter/styles/centerNew.less'), 'utf8');
    const contentRule = styles.match(/\.knowledge-detail-content\s*\{([\s\S]*?)\n\}/)?.[1] || '';

    expect(contentRule).toContain('overflow-y: auto');
  });
});
