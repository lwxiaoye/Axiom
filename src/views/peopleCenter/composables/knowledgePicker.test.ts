import {
  isKnowledgeReady,
  pruneKnowledgeSelection,
  readyKnowledgeOptions,
} from './knowledgePicker';

describe('knowledgePicker', () => {
  const ready = { id: 'ready', chunkCount: 4 };
  const empty = { id: 'empty', chunkCount: 0 };

  it('只把存在可检索分段的知识库视为就绪', () => {
    expect(isKnowledgeReady(ready)).toBe(true);
    expect(isKnowledgeReady(empty)).toBe(false);
    expect(isKnowledgeReady({ id: 'missing' })).toBe(false);
    expect(readyKnowledgeOptions([ready, empty])).toEqual([ready]);
  });

  it('刷新清单后移除未就绪和已不可见的旧选中项', () => {
    const selected = [
      { id: 'ready', name: '可用库' },
      { id: 'empty', name: '空库' },
      { id: 'gone', name: '已删除' },
    ];
    expect(pruneKnowledgeSelection(selected, [ready, empty])).toEqual([
      { id: 'ready', name: '可用库' },
    ]);
  });

  it('选中项全部就绪时返回 null，避免无效更新', () => {
    expect(pruneKnowledgeSelection([{ id: 'ready' }], [ready])).toBeNull();
  });
});
