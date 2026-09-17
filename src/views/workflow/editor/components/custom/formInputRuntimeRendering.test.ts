/**
 * 工作流调试抽屉里的表单控件：文件字段给上传、开关不撑满、下拉走工作流统一样式。
 *
 * ⚠️ 这个文件 2026-07-20 写的时候还断言 `src/views/workflow/run/index.vue`（运行页），
 * 而那个文件在 **2026-07-22 16:30 的提交 `d1c3bdb5「内置工具」** 里被删掉了。
 * `readFileSync` 写在 describe 体里，所以文件一消失**整个套件直接加载失败**——
 * 于是它连"3 条失败"都算不上：`Tests:` 那一行根本不统计加载不起来的套件，
 * 报数的人（含中心调度会话）看到的是 "3 failed"，实际是 3 条失败 + 1 个套件跑不起来。
 * **套件加载失败比断言失败更容易被漏掉，因为它不出现在失败计数里。**
 *
 * 现在只保留仍然存在的两个面：DebugDrawer.vue 与 workflowSelect.less。
 * 运行页那半边不是"改路径就能救"——那个页面已经不存在了，硬找一个替代品去断言
 * 等于凭空发明一个守护对象。
 */
import fs from 'fs';
import path from 'path';

const debugSource = fs.readFileSync(path.join(__dirname, '../DebugDrawer.vue'), 'utf8');
const selectStyleSource = fs.readFileSync(
  path.join(__dirname, '../../../shared/workflowSelect.less'),
  'utf8',
);

describe('form input runtime rendering', () => {
  it('读到的源文件都非空（辅助自证：空串会让 not.toContain 全部假绿）', () => {
    expect(debugSource.length).toBeGreaterThan(1000);
    expect(selectStyleSource.length).toBeGreaterThan(100);
  });

  it('调试抽屉的文件字段给上传控件，不让用户手填 URL', () => {
    expect(debugSource).toContain("v-else-if=\"fieldItem.type === 'fileSelect'\"");
    expect(debugSource).toContain('handleFormFileChange');
    expect(debugSource).not.toContain('placeholder="文件 URL"');
  });

  it('调试抽屉的开关保持紧凑（不被 flex 拉满整行）', () => {
    expect(debugSource).toContain('class="form-switch"');
  });

  it('调试抽屉的下拉走工作流统一样式', () => {
    expect(debugSource).toContain('class="workflow-select-control"');
    expect(debugSource).toContain('popup-class-name="wf-node-select-popup"');
    expect(debugSource).toContain("@import '../../shared/workflowSelect.less';");

    expect(selectStyleSource).toContain('.workflow-select-control.ant-select .ant-select-selector');
    expect(selectStyleSource).toContain('.wf-node-select-popup');
  });
});
