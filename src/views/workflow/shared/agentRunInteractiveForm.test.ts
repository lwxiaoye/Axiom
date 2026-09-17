/**
 * 交互表单必须渲染**真控件**，不能退回成一排纯文本框。
 *
 * ⚠️ 这个文件 2026-07-20 写的时候，字段渲染是**每个宿主各写一份**的（agent run 页、
 * DebugDrawer、主对话各一套 `f.type === 'xxx'` 分支），所以它直接读
 * `src/views/agent/run/index.vue` 的源码文本做断言。
 *
 * 2026-07-22（提交 1eda9e3e）把全字段渲染**收敛进共享组件**
 * `peopleCenter/components/InteractiveFormFields.vue`，宿主只负责挂载。于是原来那些
 * 断言开始失败——**而它们失败的方式极具误导性**：它们要求宿主文件里必须出现
 * `f.type === 'numberInput'`，也就是**要求把刚刚消灭掉的重复渲染搬回来**。
 *
 * 所以这次不是"把路径指对"，是把守护意图翻译到新形态上，并且顺手让它更强：
 *   ① 宿主必须**委派**给共享组件，且**不许**自己再判字段类型（防重复渲染回流）
 *   ② 共享组件必须覆盖全部字段类型 + 文件上传（防某个类型被悄悄漏掉）
 *
 * 这条红了整整 8 天没人发现，因为大家（包括改坏它的人）都只跑 `pnpm test:chat`，
 * 而它属于 `pnpm test:workflow`。
 */
import fs from 'fs';
import path from 'path';

const ROOT = path.join(__dirname, '../../..');
const HOST = path.join(ROOT, 'views/agent/run/index.vue');
const SHARED = path.join(ROOT, 'views/peopleCenter/components/InteractiveFormFields.vue');

const hostSource = fs.readFileSync(HOST, 'utf8');
const sharedSource = fs.readFileSync(SHARED, 'utf8');

describe('agent run interactive form rendering', () => {
  it('读到的两个源文件都非空（辅助自证：空串会让下面的 not.toContain 全部假绿）', () => {
    expect(hostSource.length).toBeGreaterThan(1000);
    expect(sharedSource.length).toBeGreaterThan(1000);
  });

  it('宿主委派给共享组件，而不是自己再写一套字段分支', () => {
    expect(hostSource).toContain('InteractiveFormFields');
    expect(hostSource).toContain('components/InteractiveFormFields.vue');
    // 关键的反向断言：宿主里**不该**再出现字段类型判断。
    // 一旦有人在宿主里"顺手补一个字段类型"，重复渲染就回来了，而两处不同步时
    // 只有一处会被改（本仓库最难发现的那类缺陷）。
    expect(hostSource).not.toContain("f.type === '");
  });

  it('草稿或审核预览会激活内部占位会话，使 HITL 表单能写回当前页面', () => {
    const ensureSessionStart = hostSource.indexOf('async function ensureSession');
    const ensureSessionEnd = hostSource.indexOf('\nfunction openFilePicker', ensureSessionStart);
    const ensureSessionSource = hostSource.slice(ensureSessionStart, ensureSessionEnd);

    expect(ensureSessionStart).toBeGreaterThan(-1);
    expect(ensureSessionEnd).toBeGreaterThan(ensureSessionStart);
    expect(ensureSessionSource).toContain('activeSessionId.value = PREVIEW_SESSION_ID');
    expect(ensureSessionSource).toContain('return PREVIEW_SESSION_ID');
    expect(ensureSessionSource.indexOf('activeSessionId.value = PREVIEW_SESSION_ID'))
      .toBeLessThan(ensureSessionSource.indexOf('return PREVIEW_SESSION_ID'));
  });

  it('共享组件为文件字段渲染上传控件，而不是让用户手填 URL', () => {
    expect(sharedSource).toContain("'fileSelect'");
    expect(sharedSource).toContain('上传文件');
    // arrayString 的文件字段（多文件）也要走上传，不能退化成字符串数组输入
    expect(sharedSource).toContain("valueType === 'arrayString'");
  });

  it('共享组件覆盖全部常见字段类型（少一个就是一排裸文本框）', () => {
    for (const type of [
      'numberInput',
      'password',
      'select',
      'multipleSelect',
      'switch',
      'textarea',
      'timePointSelect',
      'timeRangeSelect',
    ]) {
      expect(sharedSource).toContain(`f.type === '${type}'`);
    }
  });
});
