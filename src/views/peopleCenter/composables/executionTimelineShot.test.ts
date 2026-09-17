/** 页面快照进时间线（2026-07-27 用户拍板「抓到网页就把截图给用户看」）。
 *  独立成文件：executionTimeline.test.ts 有并行会话的未提交改动。 */
import {
  applyToolEvent, buildExecutionRows, collapseSandboxRuns, type ExecutionMessage,
} from './executionTimeline';

function msg(): ExecutionMessage {
  return { content: '' };
}

function completed(target: ExecutionMessage, name: string, meta: any) {
  applyToolEvent(target, { phase: 'started', name, timestamp: 1000 } as any);
  applyToolEvent(target, { phase: 'completed', name, timestamp: 2000, meta } as any);
}

function lastTool(target: ExecutionMessage) {
  const steps = (target.agentSteps || []).filter((s) => s.kind === 'tool');
  return steps[steps.length - 1] as any;
}

describe('browser_fetch 页面快照', () => {
  it('meta.shot 进 step.shot，供时间线渲染缩略图', () => {
    const m = msg();
    completed(m, 'browser_fetch', { urls: ['https://example.com/'], shot: 'data:image/jpeg;base64,AAAA' });
    expect(lastTool(m).shot).toBe('data:image/jpeg;base64,AAAA');
  });

  it('没有 shot 就不设该字段（旧消息/截图失败时不留空壳）', () => {
    const m = msg();
    completed(m, 'browser_fetch', { urls: ['https://example.com/'] });
    expect(lastTool(m).shot).toBeUndefined();
  });

  it('非浏览器工具即使带 shot 也不认：截图只属于浏览器那几个工具', () => {
    const m = msg();
    completed(m, 'read_file', { shot: 'data:image/jpeg;base64,AAAA' });
    expect(lastTool(m).shot).toBeUndefined();
  });

  it.each(['browser_open', 'browser_act'])('有状态浏览的 %s 也带截图（每一步都让用户看见）', (name) => {
    const m = msg();
    completed(m, name, { shot: 'data:image/jpeg;base64,CCCC' });
    expect(lastTool(m).shot).toBe('data:image/jpeg;base64,CCCC');
  });

  it('shot 不是字符串就忽略，不把对象塞进 img src', () => {
    const m = msg();
    completed(m, 'browser_fetch', { urls: ['https://x.com/'], shot: { data: 'x' } });
    expect(lastTool(m).shot).toBeUndefined();
  });

  // 三处校验（实时 reducer / 历史回放 restoreStep / 后端 task_run_service）此前只有后两处
  // 有 data: 前缀闸。缺的正是最靠前那处：外链在实时能看见、刷新后被另外两道闸挡掉，
  // 同一张图「刷新一次就消失」；更要紧的是它等于开了个由工具回执控制的外部请求通道
  // （可用来探测内网 / 追踪用户）。
  it.each([
    'https://evil.example.com/track.gif',
    '//evil.example.com/track.gif',
    'javascript:alert(1)',
    'DATA:image/png;base64,AAAA', // 大小写不放行：与另外两处 startsWith 判据逐字一致
  ])('非 data: 开头的 %s 一律不认', (shot) => {
    const m = msg();
    completed(m, 'browser_fetch', { urls: ['https://x.com/'], shot });
    expect(lastTool(m).shot).toBeUndefined();
  });
});

describe('打不开的网页不出图（2026-07-28 用户拍板）', () => {
  // 一张登录墙/报错页摊在执行流里，读者得先看懂「这张是失败的」，比一行文字更费解。
  // 后端已经不在失败路径截图；这里是第二道闸，挡旧轨迹与将来别的工具乱塞。
  it('failed 步骤即使带 shot 也不收', () => {
    const m = msg();
    applyToolEvent(m, { phase: 'started', name: 'browser_fetch', timestamp: 1000 } as any);
    applyToolEvent(m, {
      phase: 'failed', name: 'browser_fetch', timestamp: 2000,
      meta: { urls: ['https://zhihu.com/x'], shot: 'data:image/jpeg;base64,WALL' },
    } as any);
    expect(lastTool(m).status).toBe('failed');
    expect(lastTool(m).shot).toBeUndefined();
  });
});

describe('折叠组头带网页快照（2026-07-28）', () => {
  // 「查阅网页」族连抓 3 次以上就折成一行，成员行连同缩略图一起被藏起来——
  // 而那恰恰是最该让用户感到「它在干活」的时候。组头带图是这条的补偿。
  function webSteps(n: number, withShot = true) {
    return Array.from({ length: n }, (_, i) => ({
      kind: 'tool' as const,
      name: 'browser_fetch',
      label: '查阅网页',
      status: 'completed' as const,
      ...(withShot ? { shot: `data:image/jpeg;base64,S${i}` } : {}),
    }));
  }

  function rowsOf(steps: any[]) {
    return collapseSandboxRuns(buildExecutionRows(undefined, steps as any), 'm1', {});
  }

  function groupOf(steps: any[]) {
    return rowsOf(steps).find((r: any) => r.step.kind === 'runGroup') as any;
  }

  it('折叠后组头带上成员的前 3 张快照', () => {
    const group = groupOf(webSteps(5));
    expect(group).toBeTruthy();
    expect(group.step.count).toBe(5);
    expect(group.step.shots).toEqual([
      'data:image/jpeg;base64,S0',
      'data:image/jpeg;base64,S1',
      'data:image/jpeg;base64,S2',
    ]);
  });

  it('成员都没有快照时不留空数组（模板 v-if 才不会渲染空容器）', () => {
    const group = groupOf(webSteps(3, false));
    expect(group.step.shots).toBeUndefined();
  });

  it('不足折叠阈值时不成组，缩略图仍挂在各自的行上', () => {
    const rows = rowsOf(webSteps(2));
    expect(rows.find((r: any) => r.step?.kind === 'runGroup')).toBeUndefined();
    expect(rows.every((r: any) => typeof r.step.shot === 'string')).toBe(true);
  });
});

describe('折叠组只摊成功页面的图（2026-07-28）', () => {
  it('组里混着失败成员时，失败那张不进组头', () => {
    const steps = [
      { kind: 'tool', name: 'browser_fetch', label: '查阅网页', status: 'completed',
        shot: 'data:image/jpeg;base64,OK1' },
      { kind: 'tool', name: 'browser_fetch', label: '查阅网页', status: 'failed',
        shot: 'data:image/jpeg;base64,WALL' },
      { kind: 'tool', name: 'browser_fetch', label: '查阅网页', status: 'completed',
        shot: 'data:image/jpeg;base64,OK2' },
    ];
    const rows = collapseSandboxRuns(buildExecutionRows(undefined, steps as any), 'm1', {});
    const group: any = rows.find((r: any) => r.step.kind === 'runGroup');
    expect(group.step.count).toBe(3);   // 失败成员照样折进组（「查阅网页」族的既有语义）
    expect(group.step.shots).toEqual([  // 但它的图不摊出来
      'data:image/jpeg;base64,OK1',
      'data:image/jpeg;base64,OK2',
    ]);
  });
});
