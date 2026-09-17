import type { SkillItem } from '../agentApi';
import { hasSkillReference, messageTurnSkills } from './messageTurnContext';

const catalog: SkillItem[] = [
  { id: 'skill-ppt', name: 'PPT 助手', description: '生成演示文稿' },
  { id: 'skill-doc', name: '文档助手' },
];

describe('messageTurnSkills', () => {
  it('优先使用消息当轮的显式 Skill 快照', () => {
    expect(messageTurnSkills([catalog[0]], [], catalog)).toEqual([catalog[0]]);
  });

  it('刷新后可从持久化的 Skill reference id 恢复', () => {
    expect(messageTurnSkills(undefined, [
      { kind: 'skill', filename: 'PPT 助手', referenceId: 'skill-ppt' },
    ], catalog)).toEqual([catalog[0]]);
  });

  it('旧历史只按唯一名称命中恢复，不在歧义时猜 Skill', () => {
    expect(messageTurnSkills(undefined, [
      { kind: 'skill', filename: '文档助手' },
    ], catalog)).toEqual([catalog[1]]);
    expect(messageTurnSkills(undefined, [
      { kind: 'skill', filename: '同名 Skill' },
    ], [
      { id: 'same-1', name: '同名 Skill' },
      { id: 'same-2', name: '同名 Skill' },
    ])).toEqual([]);
  });

  it('能识别用户消息中的 Skill 引用卡', () => {
    expect(hasSkillReference([{ kind: 'skill', filename: 'PPT 助手' }])).toBe(true);
    expect(hasSkillReference([{ kind: 'pdf', filename: 'brief.pdf' }])).toBe(false);
  });
});
