import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const source = readFileSync(resolve(__dirname, 'useCenterChat.ts'), 'utf8');

describe('useCenterChat Skill 编辑重发契约', () => {
  it('把 Skill 快照绑定在原用户消息上', () => {
    expect(source).toContain('turnSkills: [...sentSkills]');
    expect(source).toContain('referenceId: att.reference_id ? String(att.reference_id) : undefined');
    expect(source).toContain('messageTurnSkills(undefined, storedAttachments, mentionSkills.value)');
  });

  it('编辑重发使用原消息 Skill，不读或清空 composer 当前 Skill', () => {
    const resendStart = source.indexOf('async function resendEditedMessage');
    const resendEnd = source.indexOf('// 把某页返回里的活跃 Run', resendStart);
    const resendBody = source.slice(resendStart, resendEnd);
    expect(resendBody).toContain('original.turnSkills');
    expect(resendBody).toContain('skillOverride: originalSkills');
    expect(resendBody).toContain('turnSkills: [...originalSkills]');

    const contextStart = source.indexOf('const turnSkills = reuseTurn');
    const contextEnd = source.indexOf('const turnThreads:', contextStart);
    const contextBody = source.slice(contextStart, contextEnd);
    expect(contextBody).toContain('opts?.skillOverride ? [...opts.skillOverride]');
    expect(contextBody).toContain('!ctxOverride && !opts?.skillOverride');
  });
});
