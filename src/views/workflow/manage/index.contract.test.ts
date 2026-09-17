import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const page = readFileSync(resolve(__dirname, 'index.vue'), 'utf8');

describe('workflow management list actions', () => {
  it('uses the management drawer as the sole lifecycle entry point', () => {
    expect(page).toContain("label: '管理'");
    expect(page).not.toContain("label: '版本历史'");
    expect(page).not.toContain("label: '下架'");
    expect(page).not.toContain("label: '删除'");
    expect(page).not.toContain('versionsDrawer');
  });
});
