import { SYSTEM_VARIABLES } from './constants';
import {
  buildTextVariableGroups,
  buildTextareaVariableGroups,
  buildTextareaVariableOptions,
  computeReferenceCandidates,
  formatReferenceLabel,
} from './utils';
import { VARIABLE_NODE_ID } from './constants';

describe('workflow system variables', () => {
  it('exposes current user real name as a selectable system variable', () => {
    expect(SYSTEM_VARIABLES).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ key: 'realname', label: '用户姓名' }),
      ])
    );
  });

  it('builds textarea slash-picker options from global variables and system variables', () => {
    const options = buildTextareaVariableOptions({
      variables: [{ id: 'v1', key: 'customer_name', label: '客户姓名', type: 'input' as any }],
    });

    expect(options).toEqual(
      expect.arrayContaining([
        { label: '客户姓名', value: 'customer_name' },
        { label: '用户姓名', value: 'realname' },
        { label: '聊天记录', value: 'histories' },
      ])
    );
  });

  it('can resolve user real name variable labels in reference pickers', () => {
    const candidates = computeReferenceCandidates('node1', [], [], { variables: [] });
    const globals = candidates.find((item) => item.nodeId === VARIABLE_NODE_ID);

    expect(globals?.outputs).toEqual(
      expect.arrayContaining([expect.objectContaining({ key: 'realname', label: '用户姓名' })])
    );
    expect(formatReferenceLabel([VARIABLE_NODE_ID, 'realname'], [], { variables: [] })).toBe('全局变量 / 用户姓名');
  });

  it('shows an empty label for blank reference tuple defaults', () => {
    expect(formatReferenceLabel(['', ''], [], { variables: [] })).toBe('');
  });

  it('keeps legacy no-node text fields in the same two picker groups', () => {
    expect(
      buildTextareaVariableGroups({
        variables: [{ id: 'v1', key: 'customer_name', label: '客户姓名', type: 'input' as any }],
      })
    ).toEqual([
      { label: '节点变量', options: [] },
      {
        label: '全局变量',
        options: expect.arrayContaining([
          { label: '客户姓名', detail: 'customer_name', value: 'customer_name' },
          { label: '用户姓名', detail: 'realname', value: 'realname' },
        ]),
      },
    ]);
  });

  it('groups every ancestor output separately from global variables for text insertion', () => {
    const groups = buildTextVariableGroups(
      'text',
      [
        {
          nodeId: 'start',
          name: '开始',
          outputs: [
            { key: 'userChatInput', label: '用户问题', type: 'static' },
            { key: 'runtimeValue', label: '运行值', type: 'dynamic' },
          ],
        } as any,
        { nodeId: 'text', name: '文本', outputs: [] } as any,
      ],
      [{ source: 'start', target: 'text' } as any],
      { variables: [{ id: 'v1', key: 'customer_name', label: '客户姓名', type: 'input' as any }] }
    );

    expect(groups).toEqual([
      {
        label: '节点变量',
        options: expect.arrayContaining([
          { label: '开始 / 用户问题', detail: 'userChatInput', value: 'node:start:userChatInput' },
          { label: '开始 / 运行值', detail: 'runtimeValue', value: 'node:start:runtimeValue' },
        ]),
      },
      {
        label: '全局变量',
        options: expect.arrayContaining([
          { label: '客户姓名', detail: 'customer_name', value: 'customer_name' },
          { label: '用户姓名', detail: 'realname', value: 'realname' },
        ]),
      },
    ]);
  });

  it('orders upstream node variables from nearest to farthest', () => {
    const candidates = computeReferenceCandidates(
      'target',
      [
        { nodeId: 'start', name: '开始', outputs: [{ key: 'userChatInput', label: '用户问题', type: 'static' }] } as any,
        { nodeId: 'far', name: '较远节点', outputs: [{ key: 'farOutput', label: '较远输出', type: 'static' }] } as any,
        { nodeId: 'near', name: '直接上游', outputs: [{ key: 'nearOutput', label: '直接输出', type: 'static' }] } as any,
        { nodeId: 'target', name: '目标节点', outputs: [] } as any,
      ],
      [
        { source: 'start', target: 'far' } as any,
        { source: 'far', target: 'near' } as any,
        { source: 'near', target: 'target' } as any,
      ],
      { variables: [] }
    );

    expect(candidates.filter((item) => item.nodeId !== VARIABLE_NODE_ID).map((item) => item.nodeId)).toEqual([
      'near',
      'far',
      'start',
    ]);
  });

  it('does not expose output configuration anchors to downstream nodes', () => {
    const candidates = computeReferenceCandidates(
      'answer',
      [
        {
          nodeId: 'code',
          name: '代码运行',
          outputs: [
            { key: 'system_addOutputParam', label: '', type: 'dynamic' },
            { key: 'result', label: 'result', type: 'dynamic', valueType: 'string' },
          ],
        } as any,
        { nodeId: 'answer', name: '回复', outputs: [] } as any,
      ],
      [{ source: 'code', target: 'answer' } as any],
      { variables: [] }
    );

    expect(candidates.find((item) => item.nodeId === 'code')?.outputs).toEqual([
      { key: 'result', label: 'result', valueType: 'string' },
    ]);
  });

  it('can include loop container child outputs for aggregation references', () => {
    const candidates = computeReferenceCandidates(
      'loop',
      [
        { nodeId: 'loop', name: '循环节点', flowNodeType: 'loopRun', outputs: [] } as any,
        {
          nodeId: 'extract',
          name: '提取当前项',
          parentNodeId: 'loop',
          outputs: [{ key: 'result', label: '结果', type: 'static', valueType: 'string' }],
        } as any,
      ],
      [],
      { variables: [] },
      { includeChildren: true }
    );

    expect(candidates.find((item) => item.nodeId === 'extract')?.outputs).toEqual([
      { key: 'result', label: '结果', valueType: 'string' },
    ]);
  });

  it('does not include loop child outputs in normal upstream references', () => {
    const candidates = computeReferenceCandidates(
      'loop',
      [
        { nodeId: 'loop', name: '循环节点', flowNodeType: 'loopRun', outputs: [] } as any,
        {
          nodeId: 'extract',
          name: '提取当前项',
          parentNodeId: 'loop',
          outputs: [{ key: 'result', label: '结果', type: 'static', valueType: 'string' }],
        } as any,
      ],
      [],
      { variables: [] }
    );

    expect(candidates.some((item) => item.nodeId === 'extract')).toBe(false);
  });
});
