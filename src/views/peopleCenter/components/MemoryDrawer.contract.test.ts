import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const source = readFileSync(resolve(__dirname, 'MemoryDrawer.vue'), 'utf8');

describe('MemoryDrawer 记忆库入口', () => {
  it('展示记忆库与个性化，不展示记忆摘要页签', () => {
    expect(source).toContain("label: '记忆库'");
    expect(source).toContain("label: '个性化'");
    expect(source).not.toContain("label: '记忆摘要'");
    expect(source).not.toContain("activeTab === 'summary'");
    expect(source).toContain("activeTab === 'library'");
    expect(source).toContain('listMemories');
    expect(source).toContain('addMemory');
    expect(source).toContain('updateMemory');
    expect(source).toContain('deleteMemory');
    expect(source).toContain('删除全部记忆');
    expect(source).toContain('搜索记忆');
  });

  it('不向用户展示内部任务复盘和 run 标识', () => {
    expect(source).toContain("startsWith('[task_lesson]')");
    expect(source).toContain('displayableItems');
  });
});
