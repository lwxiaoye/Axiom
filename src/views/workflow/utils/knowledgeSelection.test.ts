import fs from 'fs';
import path from 'path';

jest.mock('../../knowledge/knowledge.api', () => ({
  getKnowledgeList: jest.fn(),
}));

const { mergeWorkflowSelectableKnowledgeOptions } = require('./knowledgeSelection');

describe('workflow knowledge selection scope', () => {
  const source = fs.readFileSync(path.join(__dirname, 'knowledgeSelection.ts'), 'utf8');

  it('loads selectable knowledge bases from owned and shared scopes only', () => {
    expect(source).toContain("scope: 'owned'");
    expect(source).toContain("scope: 'shared'");
    expect(source).not.toContain("scope: 'all'");
  });

  it('keeps viewer shared knowledge bases selectable for agent and workflow pickers', () => {
    const options = mergeWorkflowSelectableKnowledgeOptions(
      [{ id: 'owned-viewer', name: '自有查看库', status: 'ACTIVE', currentPermission: 'VIEWER' } as any],
      [
        { id: 'shared-viewer', name: '共享查看库', status: 'ACTIVE', currentPermission: 'VIEWER' } as any,
        { id: 'shared-editor', name: '共享编辑库', status: 'ACTIVE', currentPermission: 'EDITOR' } as any,
        { id: 'shared-owner', name: '共享所有者库', status: 'ACTIVE', currentPermission: 'OWNER' } as any,
      ],
    );

    expect(options.map((item) => item.id)).toEqual([
      'owned-viewer', 'shared-viewer', 'shared-editor', 'shared-owner',
    ]);
  });
});
