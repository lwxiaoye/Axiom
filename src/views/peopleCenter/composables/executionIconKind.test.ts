import { executionIconKind } from './executionIconKind';

describe('executionIconKind', () => {
  it('keeps one glyph per ordinary tool name', () => {
    expect(executionIconKind({ name: 'read_file' })).toBe('read');
    expect(executionIconKind({ name: 'bash' })).toBe('bash');
    expect(executionIconKind({ name: 'search_web' })).toBe('search');
    expect(executionIconKind({ name: 'unknown_tool' })).toBe('tool');
  });

  it('maps interview loop steps to the public action, not a generic wrench', () => {
    expect(executionIconKind({ name: 'get_interview_session', intent: '查看本场面试' })).toBe('search');
    expect(executionIconKind({ name: 'get_interview_session', intent: '阅读简历' })).toBe('read');
    expect(executionIconKind({ name: 'get_interview_session', intent: '阅读岗位要求' })).toBe('knowledge');
    expect(executionIconKind({ name: 'get_interview_session', intent: '查看已答记录' })).toBe('search');
    expect(executionIconKind({ name: 'get_interview_session', intent: '整理本场题目' })).toBe('files');
    expect(executionIconKind({ name: 'commit_interview_turn', intent: '准备下一问' })).toBe('ask');
    expect(executionIconKind({ name: 'get_interview_session' })).toBe('search');
    expect(executionIconKind({ name: 'commit_interview_turn' })).toBe('ask');
  });

  it('does not let interview tools fall through to the wrench', () => {
    const kinds = [
      executionIconKind({ name: 'get_interview_session', intent: '查看本场面试' }),
      executionIconKind({ name: 'get_interview_session', intent: '阅读简历' }),
      executionIconKind({ name: 'get_interview_session', intent: '阅读岗位要求' }),
      executionIconKind({ name: 'commit_interview_turn', intent: '准备下一问' }),
    ];
    expect(kinds).not.toContain('tool');
    expect(new Set(kinds).size).toBe(kinds.length);
  });
});
