import { describeSubmitReviewFailure, getPublishReadiness } from './publishReadiness';

const agentCanvas = (model: string) =>
  JSON.stringify({
    version: 1,
    fastgpt: {
      nodes: [
        { nodeId: 'userGuide', flowNodeType: 'userGuide', inputs: [] },
        { nodeId: 'workflowStartNodeId', flowNodeType: 'workflowStart', inputs: [] },
        { nodeId: '7BdojPlukIQw', flowNodeType: 'agent', inputs: [{ key: 'model', value: model }] },
      ],
      edges: [],
    },
  });

describe('publish readiness before opening the publish dialog', () => {
  it('sends a freshly created chat agent to the config page instead of the publish dialog', () => {
    const result = getPublishReadiness({ aiAppType: 'chatAgent', status: 'draft' }, { draftJson: null, publishedJson: null });
    expect(result.ready).toBe(false);
    if (result.ready) return;
    expect(result.nextStepLabel).toBe('去配置');
    expect(result.reason).toContain('保存草稿');
  });

  it('treats a legacy config-only draft without a canvas as not configured', () => {
    const result = getPublishReadiness({ aiAppType: 'chatAgent' }, { draftJson: '{}' });
    expect(result).toMatchObject({ ready: false, nextStepLabel: '去配置' });
  });

  it('requires a model on the agent node', () => {
    const result = getPublishReadiness({ aiAppType: 'chatAgent' }, { draftJson: agentCanvas('') });
    expect(result.ready).toBe(false);
    if (result.ready) return;
    expect(result.reason).toContain('对话模型');
  });

  it('passes the saved draft through once the chat agent has a model', () => {
    const draft = agentCanvas('grok-4.6');
    expect(getPublishReadiness({ aiAppType: 'chatAgent' }, { draftJson: draft })).toEqual({ ready: true, workflowJson: draft });
  });

  it('labels the next step for workflows as the editor', () => {
    const result = getPublishReadiness({ aiAppType: 'workflow' }, { draftJson: '' });
    expect(result).toMatchObject({ ready: false, nextStepLabel: '去编排' });
  });
});

describe('submit review failure normalization', () => {
  it('flattens the backend validation problems and flags a 400 as a configuration problem', () => {
    const failure = describeSubmitReviewFailure({
      response: {
        status: 400,
        data: {
          detail: {
            message: '工作流校验未通过，无法提交发布',
            problems: [{ nodeId: '', name: '', reason: '对话 Agent 尚未完成配置' }, { nodeId: 'n1', name: 'HTTP 请求', reason: '必填输入「URL」未填写' }],
          },
        },
      },
    });
    expect(failure).toEqual({
      status: 400,
      message: '工作流校验未通过，无法提交发布',
      problems: ['对话 Agent 尚未完成配置', 'HTTP 请求：必填输入「URL」未填写'],
      needsConfiguration: true,
    });
  });

  it('keeps a plain string detail such as the pending-review conflict', () => {
    const failure = describeSubmitReviewFailure({ response: { status: 409, data: { detail: '该智能体已有待审核版本' } } });
    expect(failure.message).toBe('该智能体已有待审核版本');
    expect(failure.needsConfiguration).toBe(false);
  });

  it('falls back to a generic message for network errors', () => {
    expect(describeSubmitReviewFailure(new Error('Network Error')).message).toBe('Network Error');
    expect(describeSubmitReviewFailure(undefined).message).toBe('提交发布失败');
  });
});
