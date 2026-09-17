import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const component = readFileSync(resolve(__dirname, 'MyAgentsTab.vue'), 'utf8');
const styles = readFileSync(resolve(__dirname, '../styles/centerNew.less'), 'utf8');

describe('MyAgentsTab navigation', () => {
  it('keeps the labeled fold control and count badges in the workbench rail', () => {
    expect(component).toContain('MenuFoldOutlined');
    expect(component).toContain('MenuUnfoldOutlined');
    expect(component).toContain('<span v-if="!collapsed">收起</span>');
    expect(styles).toContain('.wb-rail-item .wb-rail-count {\n  display: inline-flex;');
    expect(styles).toContain('.wb-rail-toggle {\n  display: flex;');
  });
});
