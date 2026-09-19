/**
 * usePageBack：靠 vue-router 写进 history.state 的 position 判断站内有没有上一页。
 * 登录后 replace 落地、新标签页直开、刷新首屏 → 走 fallback；push 进来的页 → router.back()。
 */
const back = jest.fn();
const replace = jest.fn(() => Promise.resolve());
jest.mock('vue-router', () => ({
  useRouter: () => ({ back, replace }),
}));

type AfterEach = (to: { name?: string }, from: { name?: string }) => void;

function setPosition(position: number) {
  (globalThis as any).window = { history: { state: { position } } };
}

// 每个用例重新加载模块：基准 position 是模块级状态，不能在用例间串
function load(): { afterEach: AfterEach; usePageBack: typeof import('../../../hooks/web/usePageBack')['usePageBack'] } {
  jest.resetModules();
  const mod = require('../../../hooks/web/usePageBack');
  let hook: AfterEach = () => undefined;
  mod.setupBackTracking({ afterEach: (fn: AfterEach) => { hook = fn; } });
  return { afterEach: hook, usePageBack: mod.usePageBack };
}

describe('usePageBack', () => {
  beforeEach(() => {
    back.mockClear();
    replace.mockClear();
  });

  it('登录后 replace 落地：position 没涨，回退走 fallback 而不是浏览器空白页', () => {
    setPosition(3);
    const { afterEach, usePageBack } = load();
    afterEach({ name: 'Login' }, {}); // 首屏 /login，基准 = 3
    afterEach({ name: 'CampusRun' }, { name: 'Login' }); // replace 落地，position 仍是 3
    const { goBack, canGoBack } = usePageBack('/center/agent');
    expect(canGoBack).toBe(false);
    goBack();
    expect(replace).toHaveBeenCalledWith('/center/agent');
    expect(back).not.toHaveBeenCalled();
  });

  it('广场 push 进运行页：position 比基准大，回退用 router.back()', () => {
    setPosition(3);
    const { afterEach, usePageBack } = load();
    afterEach({ name: 'AgentMarket' }, {});
    setPosition(4);
    afterEach({ name: 'AgentRun' }, { name: 'AgentMarket' });
    const { goBack, canGoBack } = usePageBack('/center/agent');
    expect(canGoBack).toBe(true);
    goBack();
    expect(back).toHaveBeenCalledTimes(1);
    expect(replace).not.toHaveBeenCalled();
  });
});
