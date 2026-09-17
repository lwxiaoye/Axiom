import fs from 'fs';
import path from 'path';

describe('workflow editor loop quick add behavior', () => {
  const source = fs.readFileSync(path.join(__dirname, 'index.vue'), 'utf8');

  it('inherits container parentNodeId when quick adding from a loop body node', () => {
    expect(source).toContain('parentNodeId?: string');
    expect(source).toContain('if (parentNodeId) node.parentNodeId = parentNodeId;');
    expect(source).toContain('const inheritedParentNodeId = source?.parentNodeId;');
    expect(source).toContain('addNodeFromTemplate(template, request?.position, inheritedParentNodeId)');
  });

  it('uses the inherited loop container when quick adding a loop break node', () => {
    expect(source).toContain('item.nodeId === parentNodeId && item.flowNodeType === FlowNodeTypeEnum.loopRun');
  });

  it('moves a top-level target into the same loop container when connecting from a loop body node', () => {
    expect(source).toContain('if (sourceNode?.parentNodeId && targetNode && !targetNode.parentNodeId)');
    expect(source).toContain('targetNode.parentNodeId = sourceNode.parentNodeId;');
    expect(source).toContain('x: targetNode.position.x - parent.position.x');
    expect(source).toContain('y: targetNode.position.y - parent.position.y');
  });

  it('keeps the loop body start node inside the loop container without extra frame nodes', () => {
    expect(source).toContain('createNodeFromTemplate(LoopRunStartTemplate, { x: 40, y: 160 })');
    expect(source).not.toContain('LoopBodyFrame');
    expect(source).not.toContain('loopBodyFrame');
    expect(source).not.toContain('LOOP_BODY_FRAME_NODE_PREFIX');
    expect(source).not.toContain('computeLoopBodyFrame');
  });

  it('keeps normal node spacing when quick adding inside loop bodies', () => {
    expect(source).toContain('position: { x: source.position.x + 560, y: source.position.y }');
    expect(source).not.toContain('const offsetX = source.parentNodeId ? 420 : 560;');
    expect(source).not.toContain('LOOP_FRAME_MIN_WIDTH');
  });

  it('shows a success message for explicit toolbar draft saves', () => {
    expect(source).toContain('@click="handleSaveDraft"');
    expect(source).toContain('async function handleSaveDraft()');
    expect(source).toContain('await saveDraft(false)');
    expect(source).toContain('const notify = silent !== true');
    expect(source).toContain("message.success({ content: '草稿已保存', key: 'workflow-draft-save' })");
  });

  it('loads only owned and shared knowledge bases for workflow knowledge nodes', () => {
    expect(source).toContain("import { loadWorkflowSelectableKnowledgeOptions } from '../utils/knowledgeSelection'");
    expect(source).toContain('knowledgeOptions.value = await loadWorkflowSelectableKnowledgeOptions(100);');
    expect(source).not.toContain("getKnowledgeList({ pageNo: 1, pageSize: 100, scope: 'all' } as any)");
  });
});
