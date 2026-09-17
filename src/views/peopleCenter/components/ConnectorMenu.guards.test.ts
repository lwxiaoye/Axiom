/**
 * ConnectorMenu 的守卫源码断言。
 *
 * 这里断言的是**代码里那几道守卫还在**，不是"竞态不可能发生"——后者需要挂载组件
 * 并编排异步时序，而 peopleCenter 目前没有组件测试基建，硬搭一套只会得到一条又慢又脆
 * 的用例。**判据的边界写清楚，比假装它覆盖得更多有用。**
 *
 * 守的是 2026-07-29 真实发生过的一类回归：改动时删掉三处判断中的某一处，
 * 而删掉任何一处都不报错、类型也对。
 */
import { readFileSync } from 'fs';
import { join } from 'path';

const SOURCE = readFileSync(join(__dirname, 'ConnectorMenu.vue'), 'utf-8');

/** 取某个函数的函数体（到下一个顶层 `function` 为止），避免把别处的代码算进来 */
function bodyOf(name: string): string {
  // async 与普通 function 都要认：第一版只认 `async function`，而 openFlyout 是普通
  // 函数，于是 indexOf 返回 -1。**幸好里面这条断言在，否则会静默返回空串，
  // 后面所有 expect(body).toContain(...) 都会失败得莫名其妙**（或者更糟：
  // 如果判据写的是"不包含某串"，空串会让它全绿）。
  const start = ['async function ', 'function ']
    .map((kw) => SOURCE.indexOf(`${kw}${name}(`))
    .filter((i) => i > -1)
    .sort((a, b) => a - b)[0] ?? -1;
  expect(start).toBeGreaterThan(-1);
  const rest = SOURCE.slice(start + 1);
  const end = rest.search(/\n(?:async )?function /);
  return end === -1 ? rest : rest.slice(0, end);
}

describe('ConnectorMenu 守卫', () => {
  it('loadResources 三处分支都用请求令牌挡住过期响应', () => {
    const body = bodyOf('loadResources');
    // 阳性对照：确认 bodyOf 真的取到了函数体而不是空串
    expect(body).toContain('listConnectorResources');

    expect(body).toContain('const seq = ++resourceSeq');
    // 成功、失败、finally 三处都要判：
    //   成功不判 → 旧响应把内容写进新面板（悬停 GitHub 却显示邮件文件夹）
    //   失败不判 → 旧错误盖在新面板上，显示成新连接器加载失败
    //   finally 不判 → 旧请求把新请求的 loading 关掉，「转圈没了但列表是空的」
    const guards = body.match(/seq\s*!==\s*resourceSeq|seq\s*===\s*resourceSeq/g) || [];
    expect(guards.length).toBeGreaterThanOrEqual(3);
  });

  it('请求序号定义在普通 script 块里，不在 script setup 里', () => {
    // `<script setup>` 的内容会被编译进 setup()，写在那里的 let 是**每实例各自归零**的，
    // 多实例时静默失效且完全看不出来（本项目已经因为同款栽过一次：图标实例后缀）。
    // ⚠️ 必须**行首锚定**：本文件和被测文件的注释里都写着 `<script setup>` 这几个字
    // （用来解释这个坑本身），`indexOf('<script setup')` 会命中注释而不是标签。
    // 这个判据我第一版就是这么写错的——**为了解释陷阱而写的文字，成了触发同一陷阱的诱饵。**
    const lines = SOURCE.split('\n');
    const setupLine = lines.findIndex((l) => l.startsWith('<script setup'));
    const seqLine = lines.findIndex((l) => l.startsWith('let resourceSeq'));
    expect(setupLine).toBeGreaterThan(-1);
    expect(seqLine).toBeGreaterThan(-1);
    expect(seqLine).toBeLessThan(setupLine);
  });
  it('账户型连接器不去拉资源列表', () => {
    // 邮箱没有可勾的资源（授权码/OAuth 本身即整个邮箱的权限），拉回来也不渲染。
    // 而它是「飞出面板串台」的**源头**：拉回的邮件文件夹会写进共享的 resources，
    // 随后落到 GitHub 的面板里。请求令牌治的是症状，这一条治的是产生它的那一层。
    const body = bodyOf('openFlyout');
    // 阳性对照：确认取到的是真函数体而不是空串（空串会让下面的匹配莫名其妙地失败）
    expect(body).toContain('flyoutSide');
    expect(body).toMatch(/if\s*\(\s*item\.resourceKind\s*\)\s*\{\s*\n\s*loadResources/);
  });
});

/**
 * 剥掉注释后的源码。
 *
 * **必须剥**：下面几条判据要数「某句文案在源码里出现几次」，而本次改动的注释里为了解释
 * 这个坑，逐字写了「暂无可用的连接器」「console.warn」这些词——不剥注释的话，判据数到的
 * 是解释陷阱的文字本身，恰好是同一个陷阱（这是本仓库 2026-07-29 一天里出现六次的形态）。
 * 本文件不含 `http://` 之类的协议串（已核过），所以行注释可以按 `//` 直接切。
 */
function stripComments(text: string): string {
  return text
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|\s)\/\/[^\n]*/g, '$1');
}

const STRIPPED = stripComments(SOURCE);

describe('ConnectorMenu 连接器清单的加载失败态', () => {
  it('剥注释这一步真的生效了（阳性对照）', () => {
    // 用**合成输入**验证剥注释本身：不去断言"某句注释还在"——那种判据会被一次措辞改写
    // 弄红，而它其实与被测行为无关。
    expect(stripComments('<div/><!-- 暂无可用的连接器 -->')).not.toContain('暂无');
    expect(stripComments('const a = 1; // 暂无可用的连接器')).not.toContain('暂无');
    expect(stripComments('/* 暂无可用的连接器 */const a = 1;')).toBe('const a = 1;');
    // 真源码上：注释确实被剥掉了一大截（本文件注释很密），而代码标记都还在
    expect(STRIPPED.length).toBeLessThan(SOURCE.length * 0.85);
    expect(STRIPPED).toContain('cn-state-error');
    expect(STRIPPED).toContain('<style scoped>');
  });

  it('loadConnectors 失败时留下可判定的失败态，而不是只 console.warn', () => {
    const body = bodyOf('loadConnectors');
    // 阳性对照：确认取到了真函数体
    expect(body).toContain('listConnectors');
    const code = body
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/(^|\s)\/\/[^\n]*/g, '$1');
    // 失败分支必须写 loadError（此前整个 catch 只有一句 console.warn，
    // 于是首次加载失败与"真的没有连接器"在界面上完全同形）
    expect(code).toMatch(/catch[\s\S]*loadError\.value\s*=/);
    // 成功分支必须清掉它，否则陈旧提示条会在数据已经刷新之后继续挂着
    expect(code).toMatch(/loadError\.value\s*=\s*''/);
    // **不能**在失败时清空 connectors：连着的应用不会因为一次请求失败就断开，
    // 清空会让触发器图标从"手上有 GitHub"突然变回通用图标，比过期更误导
    expect(code).not.toMatch(/catch[\s\S]*connectors\.value\s*=\s*\[\]/);
  });

  it('空态、失败态、陈旧态是三个互不相同的分支', () => {
    // 判据针对的真实缺陷：`!connectors.length` 一个分支同时承担了「没连过任何应用」和
    // 「请求挂了」，用户读到前者就会跑去重新授权一遍已经连好的 GitHub。
    expect(STRIPPED).toMatch(/v-else-if="loadError && !connectors\.length"/);
    expect(STRIPPED).toMatch(/v-else-if="!connectors\.length"/);
    expect(STRIPPED).toMatch(/v-else-if="loadError"/);
    // 「暂无可用的连接器」只能出现在**一个**地方：出现两次就说明失败态又被写成了同一句话
    expect(STRIPPED.match(/暂无可用的连接器/g) || []).toHaveLength(1);
    // 失败态与陈旧态各自有自己的文案，且都不复用那句空态文案
    expect(STRIPPED).toContain('连接器清单加载失败');
    expect(STRIPPED).toContain('状态可能不是最新');
  });

  it('失败态和陈旧态都带重试入口，且重试按钮受 loading 禁用', () => {
    const retries = STRIPPED.match(/class="cn-retry"/g) || [];
    expect(retries.length).toBeGreaterThanOrEqual(2);   // 失败态 + 陈旧态各一个
    const buttons = STRIPPED.match(/<button[^>]*class="cn-retry"[\s\S]*?<\/button>/g) || [];
    expect(buttons).toHaveLength(retries.length);
    for (const btn of buttons) {
      expect(btn).toContain('@click.stop="loadConnectors"');
      // 面板是浮层，冒泡会被外部点击处理器读成"点到面板外"→ 立刻关掉；
      // 不禁用则连点会并发发请求，最后回来的那个说话（旧响应盖新响应）
      expect(btn).toContain(':disabled="loading"');
    }
  });

  it('新加的类名都有对应样式规则', () => {
    // 本仓库记过的假阴性：模板里用了一个 style 段里根本没定义的类，
    // 截图、lint、单测全绿——只有肉眼能看出"那行没样式"。
    const styleStart = STRIPPED.indexOf('<style scoped>');
    expect(styleStart).toBeGreaterThan(-1);
    const styles = STRIPPED.slice(styleStart);
    for (const cls of ['cn-state-error', 'cn-stale', 'cn-state-text', 'cn-retry']) {
      expect(styles).toContain(`.${cls}`);
    }
  });
});
