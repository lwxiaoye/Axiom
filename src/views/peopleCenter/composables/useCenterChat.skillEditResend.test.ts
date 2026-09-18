import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const source = readFileSync(resolve(__dirname, 'useCenterChat.ts'), 'utf8');

/** 截取 [start, end) 两个锚点之间的源码；锚点找不到时直接失败并报出锚点名，
 *  而不是让 indexOf 返回 -1 后 slice 出空串、再报一堆看不懂的「Received: ""」。 */
function sliceBetween(start: string, end: string): string {
  const from = source.indexOf(start);
  expect({ anchor: start, found: from >= 0 }).toEqual({ anchor: start, found: true });
  const to = source.indexOf(end, from);
  expect({ anchor: end, found: to >= 0 }).toEqual({ anchor: end, found: true });
  return source.slice(from, to);
}

describe('useCenterChat Skill 编辑重发契约', () => {
  it('把 Skill 快照绑定在原用户消息上', () => {
    expect(source).toContain('turnSkills: [...sentSkills]');
    expect(source).toContain('referenceId: att.reference_id ? String(att.reference_id) : undefined');
    expect(source).toContain('messageTurnSkills(undefined, storedAttachments, mentionSkills.value)');
  });

  it('编辑重发使用原消息 Skill，不读或清空 composer 当前 Skill', () => {
    const resendBody = sliceBetween('async function resendEditedMessage', '// 把某页返回里的活跃 Run');
    expect(resendBody).toContain('original.turnSkills');
    expect(resendBody).toContain('skillOverride: originalSkills');
    expect(resendBody).toContain('turnSkills: [...originalSkills]');

    // 锚点只钉到 `const turnSkills =`：右值的第一个分支后来插入了 presentationTurn（PPT 模式禁用 Skill），
    // 原先钉 `= reuseTurn` 会让 indexOf 落空、整段切成空串。本契约关心的是 skillOverride 分支仍在，与首分支无关。
    const contextBody = sliceBetween('const turnSkills =', 'const turnThreads:');
    expect(contextBody).toContain('opts?.skillOverride ? [...opts.skillOverride]');
    expect(contextBody).toContain('!ctxOverride && !opts?.skillOverride');
  });
});
