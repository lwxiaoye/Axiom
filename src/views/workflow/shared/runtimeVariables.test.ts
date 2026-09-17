import { buildMessageHistories, buildWorkflowRuntimeVariables, interpolateWorkflowText } from './runtimeVariables';

describe('workflow runtime variables', () => {
  it('injects user identity and an empty histories array even before the second turn', () => {
    const variables = buildWorkflowRuntimeVariables({
      userInfo: { id: 'u1', username: 'zhangsan', realname: '张三' },
      histories: [],
    });

    expect(variables).toMatchObject({
      userId: 'u1',
      username: 'zhangsan',
      realname: '张三',
      histories: [],
    });
  });

  it('keeps only successful chat messages as history variables', () => {
    const histories = buildMessageHistories([
      { role: 'user', content: '你好' },
      { role: 'assistant', content: '你好，有什么可以帮你？' },
      { role: 'assistant', content: '失败内容', failed: true },
    ]);

    expect(histories).toEqual([
      { role: 'user', content: '你好' },
      { role: 'assistant', content: '你好，有什么可以帮你？' },
    ]);
  });

  it('renders workflow template text from runtime variables', () => {
    expect(interpolateWorkflowText('你好，{{realname}}，历史：{{histories}}', {
      realname: '张三',
      histories: [],
    })).toBe('你好，张三，历史：[]');
  });
});
