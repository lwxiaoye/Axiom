// agent-browser 冒烟：跨容器连 ws → 起 context → 真实抓一个页面 → 截图 → 干净关闭。
// 由 smoke.sh 在一次性容器里执行（同一张 docker 网络），走的路径与 agent-api 将来完全一致。
const WS_ENDPOINT = process.env.WS_ENDPOINT || 'ws://agent-browser:3000/';
const TARGET_URL = process.env.TARGET_URL || 'https://example.com/';
const OUT = process.env.OUT_DIR || '/out';

// 镜像里 playwright 是全局安装的，不在挂载目录 /smoke 的模块解析路径上；
// smoke.sh 已设 NODE_PATH，这里再留两条绝对路径兜底（不同镜像版本 global 前缀会变）。
async function loadPlaywright() {
  const specs = [
    'playwright',
    '/usr/lib/node_modules/playwright/index.mjs',
    '/usr/local/lib/node_modules/playwright/index.mjs',
  ];
  const errs = [];
  for (const spec of specs) {
    try {
      return await import(spec);
    } catch (e) {
      errs.push(`${spec}: ${e.message.split('\n')[0]}`);
    }
  }
  throw new Error('无法加载 playwright 模块：\n  ' + errs.join('\n  '));
}

let browser;
let exitCode = 0;
try {
  const { chromium } = await loadPlaywright();

  console.log(`[1/5] connect ${WS_ENDPOINT}`);
  browser = await chromium.connect(WS_ENDPOINT, { timeout: 30_000 });
  console.log(`      服务端浏览器版本 ${browser.version()}`);

  // 一个会话一个 context（隔离单位），不持久化
  console.log('[2/5] new_context');
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();

  console.log(`[3/5] goto ${TARGET_URL}`);
  const resp = await page.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  const title = await page.title();
  const text = await page.evaluate(() => document.body.innerText || '');
  console.log(`      HTTP ${resp?.status()}  title="${title}"  正文 ${text.length} 字符`);
  if (!title && text.length === 0) throw new Error('页面既没标题也没正文，抓取实际失败');

  console.log('[4/5] screenshot');
  const shot = `${OUT}/smoke.png`;
  await page.screenshot({ path: shot });
  const { statSync } = await import('node:fs');
  console.log(`      ${shot}  ${statSync(shot).size} 字节`);

  console.log('[5/5] 关闭 context（显式关闭是回收第一层，别指望 TTL）');
  await ctx.close();

  console.log('PASS —— 跨容器 ws 连通、真实渲染、截图、干净关闭全部通过');
} catch (e) {
  console.error('FAIL —— ' + (e?.stack || e));
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
}
process.exit(exitCode);
