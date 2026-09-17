/**
 * 浏览器步骤（browser_fetch / browser_open / browser_act / browser_close）失败不许被
 * 伪装成成功。守的是 2026-07-29 深扫实锤的那个 P0：渲染层把整个联网工具族的图标状态强改成
 * completed、行文案一律写死「查阅网页」、可展开详情面板只对 bash 开放 —— 于是 browser_act
 * 点提交 / 点确认支付失败时，时间线只是灰底一行「查阅网页」，没有失败三角、没有错误文案、
 * 点不开详情，**用户会以为操作成功了**。browser_act 的作用域含提交与支付，误判代价不对等。
 * 读文件 / 搜索探路失败不在此列：那些继续用原动作图标，不换警告三角。
 *
 * 两段判据性质不同，各自的边界都写清楚（不假装覆盖得更多）：
 *  ① 文案事实源 —— 真行为断言，直接调 toolStepDisplay。四个工具的三态文案本来就写在
 *     TIMELINE_TOOL_LABELS 里，出事的是"渲染层没用它"，所以这一段守的是"这张表继续可用"。
 *  ② 渲染层守卫 —— 源码断言。peopleCenter 没有组件测试基建（jest.config.chat.cjs 是
 *     testEnvironment:node、不带 vue transform），硬搭一套只会得到一条又慢又脆的用例。
 *     它证明的是"那几处判断还在"，**不是**"截图长对了"。
 *
 * ⚠️ 对 .vue 源码做文本判据前必须先剥注释：本次修复的注释里逐字写着被删掉的那两句文案，
 * 不剥注释，「不许再写死查阅网页」这条断言会被自己的注释永久证伪。解释陷阱的文字本身
 * 就是同一个陷阱的诱饵 —— 所以下面每条禁止型断言都跑在 CODE（已剥注释）上，并配阳性对照。
 */
import { readFileSync } from 'fs';
import { join } from 'path';
import { toolStepDisplay } from '../composables/executionTimeline';
import { isWebTimelineStepName, stepIconStatus } from '../composables/executionIconStatus';

const SOURCE = readFileSync(join(__dirname, 'MessageList.vue'), 'utf-8');

/** 剥掉 HTML 注释、块注释与行尾行注释。
 *  行注释不认 `://`：源码里有 https 字面量（favicon 地址、SVG xmlns），一刀切会把代码切掉。 */
function stripComments(text: string): string {
  return text
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');
}

const CODE = stripComments(SOURCE);

/** 取顶层函数体（到下一个顶层 `function` 为止），已剥注释。
 *  内含自证断言：取不到就当场失败，而不是静默返回空串 —— 空串会让"不包含 X"型断言全绿，
 *  得到一个永远通过的测试。 */
function bodyOf(name: string, from: string = CODE): string {
  const start = from.indexOf(`function ${name}(`);
  expect(start).toBeGreaterThan(-1);
  const rest = from.slice(start + 1);
  const end = rest.search(/\nfunction /);
  const body = end === -1 ? rest : rest.slice(0, end);
  expect(body.length).toBeGreaterThan(20);
  return body;
}

describe('浏览器工具三态文案的事实源（TIMELINE_TOOL_LABELS via toolStepDisplay）', () => {
  const NAMES = ['browser_fetch', 'browser_open', 'browser_act', 'browser_close'];

  it('四个工具的完成文案互不相同：读网页 / 打开 / 动手 / 关闭 是四件事', () => {
    const labels = NAMES.map((name) => toolStepDisplay({ name, status: 'completed' }).label);
    expect(new Set(labels).size).toBe(4);
    // 出现「已调用 browser_act」这类兜底文案 = 这张表被删了条目，内部工具名直接摊给用户
    expect(labels.some((l) => l.includes('browser_'))).toBe(false);
  });

  it('失败文案与完成文案不同，且不带「已…」的完成语气', () => {
    for (const name of NAMES) {
      const done = toolStepDisplay({ name, status: 'completed' }).label;
      const failed = toolStepDisplay({ name, status: 'failed' }).label;
      expect(failed).not.toBe(done);
      expect(failed.startsWith('已')).toBe(false);
    }
  });

  it('operation=fetch 不许接管浏览器工具的文案：点按钮不能说成「已读取」', () => {
    // 后端三处 sink 都写死 operation:"fetch"（chat/tools/browser.py），
    // 只有 ACTION_LABEL_UNTRUSTED 拦着它，才没把有副作用的动作说成只读抓取。
    // 阳性对照在前：同一个 operation 在**可信**的工具上确实会接管，证明这条判据不是空转。
    expect(toolStepDisplay({ name: 'read_file', status: 'completed', operation: 'fetch' }).label)
      .toBe('已读取');
    expect(toolStepDisplay({ name: 'browser_act', status: 'completed', operation: 'fetch' }).label)
      .toBe('已操作页面');
  });

  it('失败态永不套通用完成动作文案', () => {
    for (const name of NAMES) {
      expect(toolStepDisplay({ name, status: 'failed', operation: 'fetch' }).label)
        .not.toBe('已读取');
    }
  });
});

describe('MessageList 渲染层守卫：浏览器步骤失败不被改写成完成', () => {
  it('剥注释这一步是必要的，而且真的生效（阳性对照）', () => {
    // toolRowTitle 的修复注释里逐字写着被删掉的那两句文案（用来解释它错在哪）。
    // 剥之前有、剥之后没有 —— 两头都断言，才能证明下面那条禁止型判据不是被注释喂饱的。
    expect(bodyOf('toolRowTitle', SOURCE)).toContain('查阅网页');
    expect(bodyOf('toolRowTitle')).not.toContain('查阅网页');
  });

  it('工具行图标状态走 stepIconStatus，模板里不再一刀切改成 completed', () => {
    expect(CODE).toContain(':status="stepIconStatus(row.step)"');
    expect(CODE).not.toContain("isWebTimelineStep(row.step) ? 'completed'");
  });

  it('stepIconStatus：browser_act 失败仍是三角；读文件/搜索失败不换三角', () => {
    expect(CODE).toContain("from '../composables/executionIconStatus'");
    expect(stepIconStatus({ name: 'browser_act', status: 'failed' })).toBe('failed');
    expect(stepIconStatus({ name: 'read_file', status: 'failed' })).toBe('completed');
    expect(stepIconStatus({ name: 'search_web', status: 'failed' })).toBe('completed');
  });

  it('toolRowTitle 不再写死「查阅网页」，失败态用共享文案表的 label', () => {
    const body = bodyOf('toolRowTitle');
    expect(body).toContain('step.label || step.intent');    // 阳性对照
    expect(body).not.toContain('查阅网页');
    // 失败时不让 intent（模型动手**之前**写的「这次要做什么」）冒充回执
    expect(body).toMatch(/status === 'failed'[\s\S]*?return step\.label/);
    // 完成后不再用「正在检索网页」这种进行态套话继续闪标题
    expect(body).toContain('isProgressPhrase');
  });

  it('hasShellPanel：browser_* 失败可展开看错误详情（成功态刻意不给，保留）', () => {
    const body = bodyOf('hasShellPanel');
    expect(body).toContain("step.name === 'bash'");          // 阳性对照
    expect(body).toMatch(/status === 'failed'[\s\S]*?startsWith\('browser_'\)/);
    expect(CODE).toContain('class="ast-shell-panel"');
    expect(CODE).not.toContain('{{ errorBrief(row.step.error) }}');
  });

  it('执行步骤行只留动作标题，不跟 target / 失败说明', () => {
    expect(CODE).not.toContain('{{ visibleStepTarget(row.step) }}');
    expect(CODE).not.toContain('searchOpenedSummary(row.step)');
    expect(CODE).not.toContain('{{ errorBrief(row.step.error) }}');
    expect(CODE).not.toContain('v-else-if="showsStepTarget(row.step)');
  });

  it('联网族包含 search_web / deep_read / browser_*，阅读行不另铺 URL 胶囊', () => {
    expect(isWebTimelineStepName('search_web')).toBe(true);
    expect(isWebTimelineStepName('deep_read')).toBe(true);
    expect(isWebTimelineStepName('browser_fetch')).toBe(true);
    expect(isWebTimelineStepName('read_file')).toBe(false);
  });
});
