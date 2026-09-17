import { applySubagentStep, type ExecutionMessage } from './executionTimeline';

function message(): ExecutionMessage {
  return {
    content: '',
    agentSteps: [],
    subagentRuns: [],
    subagentCalls: [],
  };
}

describe('子智能体委派时间线', () => {
  it('started 前不生成成员胶囊，started 后插在交付步骤下方', () => {
    const target = message();

    applySubagentStep(target, {
      phase: 'preparing', id: 'agent-1', name: '智能文档识别助手', task: '分析文档',
    });
    expect(target.agentSteps?.map((step) => step.kind)).toEqual(['tool']);
    expect(target.subagentRuns).toEqual([]);

    applySubagentStep(target, {
      phase: 'started', id: 'agent-1', name: '智能文档识别助手', task: '分析文档', icon: '/avatar.png',
    });
    expect(target.agentSteps?.map((step) => step.kind)).toEqual(['tool', 'subagent']);
    expect(target.agentSteps?.[0]).toMatchObject({
      kind: 'tool', status: 'completed', label: '已打开「智能文档识别助手」，任务已交给它处理',
    });
    expect(target.agentSteps?.[1]).toMatchObject({
      kind: 'subagent', name: '智能文档识别助手', status: 'running',
    });
    expect(target.subagentRuns?.[0]).toMatchObject({
      name: '智能文档识别助手', icon: '/avatar.png', status: 'running',
    });
  });

  it('连续节点只更新一个当前步骤，不把瞬时节点同时堆成多行', () => {
    const target = message();
    applySubagentStep(target, {
      phase: 'preparing', id: 'agent-1', name: '智能文档识别助手', task: '分析文档',
    });
    applySubagentStep(target, {
      phase: 'started', id: 'agent-1', name: '智能文档识别助手', task: '分析文档',
    });

    applySubagentStep(target, {
      phase: 'node', id: 'agent-1', name: '', label: '流程开始', status: 'running',
    });
    applySubagentStep(target, {
      phase: 'node', id: 'agent-1', name: '', label: '流程开始', status: 'success',
    });
    applySubagentStep(target, {
      phase: 'node', id: 'agent-1', name: '', label: 'AI 对话', status: 'running',
    });

    const nodeSteps = target.agentSteps?.filter(
      (step) => step.kind === 'tool' && step.operation === 'subagent_node',
    );
    expect(nodeSteps).toHaveLength(1);
    expect(nodeSteps?.[0]).toMatchObject({
      status: 'running', label: '「智能文档识别助手」正在AI 对话',
    });
    expect(target.subagentRuns?.[0].nodes).toHaveLength(3);

    applySubagentStep(target, {
      phase: 'completed', id: 'agent-1', name: '智能文档识别助手', preview: '完成',
    });
    expect(target.agentSteps?.find((step) => step.kind === 'subagent')).toMatchObject({ status: 'completed' });
    expect(nodeSteps?.[0].status).toBe('completed');
  });

  it('收尾时把持久化文件回执留在实时工作窗口运行档', () => {
    const target = message();
    applySubagentStep(target, {
      phase: 'started', id: 'agent-1', name: '文档助手', task: '生成 Word',
    });
    applySubagentStep(target, {
      phase: 'completed', id: 'agent-1', name: '文档助手', preview: '已完成',
      files: [{
        id: 'file-1', filename: '报告.docx', size: 128,
        source: 'generated', deliverable: true,
      }],
    });

    expect(target.subagentRuns?.[0]).toMatchObject({
      status: 'completed',
      files: [{ id: 'file-1', filename: '报告.docx', deliverable: true }],
    });
  });
});
