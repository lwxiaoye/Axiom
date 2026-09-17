import { resolveRunInspirationScenes, resolveRunWelcomeText } from './agentRunPresentation';

describe('agent run presentation defaults', () => {
  it('uses the configured welcome text and provides a named fallback', () => {
    expect(resolveRunWelcomeText('前端开发协作助手', '  欢迎回来  ')).toBe('欢迎回来');
    expect(resolveRunWelcomeText('前端开发协作助手', '')).toBe(
      '你好，我是前端开发协作助手。告诉我你想完成什么，我会根据当前能力帮你推进。',
    );
  });

  it('shows only builder-configured recommendations, preserving order and wording', () => {
    const scenes = resolveRunInspirationScenes({
      quickQuestions: [' 你可以帮我做什么？ ', '把这些要点整理成汇报稿', '你可以帮我做什么？'],
    });

    expect(scenes).toEqual([{
      key: 'configured',
      label: '推荐',
      tasks: ['你可以帮我做什么？', '把这些要点整理成汇报稿'],
    }]);
  });

  it('preserves builder-configured scene allocation and ignores empty scenes', () => {
    expect(resolveRunInspirationScenes({
      inspirationScenes: [
        { key: 'translate', label: '翻译', tasks: ['翻译成英文'] },
        { key: 'polish', label: '润色', tasks: ['优化语气'] },
        { key: 'empty', label: '空场景', tasks: [] },
      ],
      quickQuestions: ['旧扁平推荐'],
    })).toEqual([
      { key: 'translate', label: '翻译', tasks: ['翻译成英文'] },
      { key: 'polish', label: '润色', tasks: ['优化语气'] },
    ]);
  });

  it('does not invent fallback recommendations when the builder has not configured any', () => {
    expect(resolveRunInspirationScenes({ quickQuestions: [] })).toEqual([]);
  });
});
