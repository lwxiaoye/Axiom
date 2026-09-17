/** V2 同类归拢（collapseSandboxRuns）：连续安静 bash 行折组的边界行为。
 *  独立成文件（不并入 executionTimeline.test.ts）：该文件有并行会话的未提交改动。
 *  2026-07-29 夹具从已退休的沙箱执行器换成 bash——两者在 QUIET_FAMILIES 里同族同阈值，
 *  折叠行为等价；退休名的回放折叠另有 executionTimeline.test.ts「退休工具的历史回放」守着。 */
import {
  collapseSandboxRuns,
  restoreExecutionTrace,
  searchWebStepTitle,
  type ExecutionRow,
} from './executionTimeline';

function toolRow(
  stepIndex: number,
  over: Partial<Extract<ExecutionRow, { type: 'step' }> & { step: any }>['step'] = {},
  nested = false,
): ExecutionRow {
  return {
    type: 'step',
    stepIndex,
    nested,
    step: {
      kind: 'tool', name: 'bash', label: '已执行', status: 'completed', ...over,
    },
  };
}

function noteRow(stepIndex: number): ExecutionRow {
  return { type: 'step', stepIndex, nested: false, step: { kind: 'note', text: '说明' } };
}

describe('collapseSandboxRuns', () => {
  it('连续 ≥2 个安静 bash 折成一行组头，收起时成员不渲染', () => {
    const rows = [toolRow(0), toolRow(1), toolRow(2)];
    const out = collapseSandboxRuns(rows, 'm1', {});
    expect(out).toHaveLength(1);
    const head = out[0] as Extract<ExecutionRow, { type: 'step' }>;
    expect(head.step).toMatchObject({ kind: 'runGroup', count: 3, expanded: false, groupKey: 'm1:rg:0' });
    expect(head.stepIndex).toBe(0);
  });

  it('展开时组头后原样跟随成员行', () => {
    const rows = [toolRow(0), toolRow(1)];
    const out = collapseSandboxRuns(rows, 'm1', { 'm1:rg:0': true });
    expect(out).toHaveLength(3);
    expect((out[0] as any).step.kind).toBe('runGroup');
    expect((out[1] as any).stepIndex).toBe(0);
    expect((out[2] as any).stepIndex).toBe(1);
  });

  it('单独一个安静 bash 不折叠', () => {
    const out = collapseSandboxRuns([toolRow(0)], 'm1', {});
    expect(out).toHaveLength(1);
    expect((out[0] as any).step.kind).toBe('tool');
  });

  it('失败行/带产物行/运行中行都打断且不入组', () => {
    const rows = [
      toolRow(0),
      toolRow(1),
      toolRow(2, { status: 'failed', error: '炸了' }),
      toolRow(3, { target: '3 个产物' }),
      toolRow(4, { status: 'running' }),
      toolRow(5),
    ];
    const out = collapseSandboxRuns(rows, 'm1', {});
    expect((out[0] as any).step.kind).toBe('runGroup');
    expect((out[0] as any).step.count).toBe(2);
    expect((out[1] as any).step.status).toBe('failed');
    expect((out[2] as any).step.target).toBe('3 个产物');
    expect((out[3] as any).step.status).toBe('running');
    expect((out[4] as any).step.kind).toBe('tool');
    expect(out).toHaveLength(5);
  });

  it('非同族工具与叙述行打断分组', () => {
    const rows = [toolRow(0), noteRow(1), toolRow(2), toolRow(3)];
    const out = collapseSandboxRuns(rows, 'm1', {});
    expect(out).toHaveLength(3);
    expect((out[0] as any).step.kind).toBe('tool');
    expect((out[1] as any).step.kind).toBe('note');
    expect((out[2] as any).step).toMatchObject({ kind: 'runGroup', count: 2, groupKey: 'm1:rg:2' });
  });

  it('跨计划组（planKey 不同）不合并', () => {
    const rows = [
      toolRow(0, { planKey: 'p1' }),
      toolRow(1, { planKey: 'p1' }),
      toolRow(2, { planKey: 'p2' }),
      toolRow(3, { planKey: 'p2' }),
    ];
    const out = collapseSandboxRuns(rows, 'm1', {});
    expect(out).toHaveLength(2);
    expect((out[0] as any).step).toMatchObject({ kind: 'runGroup', count: 2, groupKey: 'm1:rg:0' });
    expect((out[1] as any).step).toMatchObject({ kind: 'runGroup', count: 2, groupKey: 'm1:rg:2' });
  });

  it('组键含消息 id：不同消息同下标互不串扰展开态', () => {
    const rows = [toolRow(0), toolRow(1)];
    const outA = collapseSandboxRuns(rows, 'mA', { 'mB:rg:0': true });
    expect(outA).toHaveLength(1);
  });
});

/** 2026-07-27 泛化：真机一次仓库分析吐出连续 14 行「读取 xxx」+ 12 行「获取 xxx」，
 *  用户要求同类折叠。这些行**带文件名 target**，此前被 `!target` 一刀切排除在外。 */
describe('collapseSandboxRuns · 多工具族', () => {
  const read = (i: number, over: any = {}) =>
    toolRow(i, { name: 'read_file', target: `f${i}.py`, ...over });
  const down = (i: number, over: any = {}) =>
    toolRow(i, { name: 'download_url', target: `f${i}.py`, ...over });

  it('连续 ≥3 个 read_file 折成「读取文件」组头，带文件名也照折', () => {
    const out = collapseSandboxRuns([read(0), read(1), read(2)], 'm1', {});
    expect(out).toHaveLength(1);
    expect((out[0] as any).step).toMatchObject({
      kind: 'runGroup', count: 3, label: '读取文件', unit: '个文件', icon: 'read',
    });
  });

  it('只有 2 个读取不折：组头+箭头比两行原文更难读', () => {
    const out = collapseSandboxRuns([read(0), read(1)], 'm1', {});
    expect(out).toHaveLength(2);
    expect((out[0] as any).step.kind).toBe('tool');
  });

  it('不同族相邻不合并，各折各的', () => {
    const rows = [read(0), read(1), read(2), down(3), down(4), down(5)];
    const out = collapseSandboxRuns(rows, 'm1', {});
    expect(out).toHaveLength(2);
    expect((out[0] as any).step).toMatchObject({ label: '读取文件', count: 3 });
    expect((out[1] as any).step).toMatchObject({ label: '下载文件', count: 3, icon: 'download' });
  });

  it('失败行不被折叠也不并进组：真机那批 download_url 有两个 404，必须留在外面可见', () => {
    const rows = [down(0), down(1), down(2), down(3, { status: 'failed', error: 'HTTP 404' }), down(4)];
    const out = collapseSandboxRuns(rows, 'm1', {});
    expect((out[0] as any).step).toMatchObject({ kind: 'runGroup', count: 3 });
    expect((out[1] as any).step).toMatchObject({ status: 'failed', error: 'HTTP 404' });
    expect((out[2] as any).step.kind).toBe('tool'); // 失败后只剩 1 个，不足 min 不折
    expect(out).toHaveLength(3);
  });

  it('沙箱探查族保持原口径：带产物计数的行永不折叠', () => {
    const rows = [toolRow(0), toolRow(1), toolRow(2, { target: '3 个产物' })];
    const out = collapseSandboxRuns(rows, 'm1', {});
    expect((out[0] as any).step).toMatchObject({ kind: 'runGroup', count: 2, label: '沙箱探查', unit: '步' });
    expect((out[1] as any).step.target).toBe('3 个产物');
  });

  it('运行中的行不折（无论哪一族）', () => {
    const rows = [read(0), read(1), read(2, { status: 'running' })];
    const out = collapseSandboxRuns(rows, 'm1', {});
    expect(out).toHaveLength(3);
    expect(out.every((r) => (r as any).step.kind === 'tool')).toBe(true);
  });

  it('保留搜索词，把连续网页抓取合并成可展开的动作摘要', () => {
    const rows = [
      toolRow(0, { name: 'search_web', intent: '具体搜索词 A' }),
      toolRow(1, { name: 'browser_fetch', target: 'https://example.com/a', status: 'failed', error: '抓取失败' }),
      toolRow(2, { name: 'browser_open', intent: '具体页面标题 B' }),
      toolRow(3, { name: 'browser_fetch', target: 'https://example.com/c' }),
    ];
    const out = collapseSandboxRuns(rows, 'm1', { 'm1:rg:1': true });
    expect(out).toHaveLength(5);
    expect((out[0] as any).step).toMatchObject({ kind: 'tool', name: 'search_web', intent: '具体搜索词 A' });
    expect((out[1] as any).step).toMatchObject({
      kind: 'runGroup',
      count: 3,
      label: '查阅网页',
      unit: '次',
      icon: 'web',
      expanded: true,
    });
  });

  it('单独一次网页搜索直接展示，不生成折叠组', () => {
    const out = collapseSandboxRuns([
      toolRow(0, { name: 'search_web', detail: 'Codex agent loop design' }),
    ], 'm1', {});
    expect(out).toHaveLength(1);
    expect((out[0] as any).step).toMatchObject({ kind: 'tool', name: 'search_web' });
  });

  it('连续两次网页搜索默认折叠为不显示次数的「已完成网络搜索」', () => {
    const rows = [
      toolRow(0, { name: 'search_web', detail: 'query A' }),
      toolRow(1, { name: 'search_web', detail: 'query B' }),
    ];
    const out = collapseSandboxRuns(rows, 'm1', {});
    expect(out).toHaveLength(1);
    expect((out[0] as any).step).toMatchObject({
      kind: 'runGroup',
      count: 2,
      label: '已完成网络搜索',
      unit: '次网页搜索',
      icon: 'web',
      familyId: 'search',
      expanded: false,
      groupKey: 'm1:rg:0',
    });
  });

  it('展开网页搜索组后逐条还原成员，并保留失败项', () => {
    const rows = [
      toolRow(0, { name: 'search_web', detail: 'query A' }),
      toolRow(1, { name: 'search_web', detail: 'query B', status: 'failed', error: '请求超时' }),
    ];
    const out = collapseSandboxRuns(rows, 'm1', { 'm1:rg:0': true });
    expect(out).toHaveLength(3);
    expect((out[0] as any).step).toMatchObject({ kind: 'runGroup', count: 2, failedCount: 1, expanded: true });
    expect(out.slice(1).every((row) => row.type === 'step' && row.groupMember)).toBe(true);
    expect((out[2] as any).step).toMatchObject({ status: 'failed', error: '请求超时' });
  });

  it('公开说明会打断网页搜索归拢，不能为压缩高度改写事件顺序', () => {
    const rows = [
      toolRow(0, { name: 'search_web', detail: 'query A' }),
      noteRow(1),
      toolRow(2, { name: 'search_web', detail: 'query B' }),
    ];
    const out = collapseSandboxRuns(rows, 'm1', {});
    expect(out).toHaveLength(3);
    expect(out.map((row: any) => row.step.kind)).toEqual(['tool', 'note', 'tool']);
  });
});

describe('searchWebStepTitle', () => {
  it('单次搜索只展示代表页面和域名，不复述查询词或追加命中数量', () => {
    const title = searchWebStepTitle({
      status: 'completed',
      detail: 'Codex agent loop design',
      pages: [{ title: 'Agent loop', url: 'https://www.openai.com/research/agent-loop' }],
    });
    expect(title).toBe('已搜索网页：Agent loop · openai.com');
    expect(title).not.toContain('Codex agent loop design');
    expect(title).not.toMatch(/\+\d|搜索到 \d/);
  });

  it('失败搜索只展示失败事实，不暴露后端错误原因', () => {
    expect(searchWebStepTitle({
      status: 'failed',
      detail: 'agent safety evaluation',
      error: '请求超时',
    })).toBe('网页搜索失败');
  });

  it('进行中不复述查询入参，也不提前展示结果页面', () => {
    expect(searchWebStepTitle({
      status: 'running',
      detail: 'agent safety evaluation',
      pages: [{ title: '旧页面', url: 'https://example.com/old' }],
    }, true)).toBe('正在搜索网页…');
  });

  it('没有结果页时只保留完成事实，不回退到查询词', () => {
    expect(searchWebStepTitle({ status: 'completed', detail: 'OpenAI' }))
      .toBe('已搜索网页');
  });

  it('历史回放还原查询词和代表来源后，标题与实时路径一致', () => {
    const restored = restoreExecutionTrace({
      status: 'completed',
      steps: [{
        kind: 'tool',
        name: 'search_web',
        status: 'completed',
        detail: 'Codex agent loop design',
        pages: [{ title: 'Agent loop', url: 'https://openai.com/research/agent-loop' }],
      }],
    });
    const step = restored.agentSteps?.[0];
    expect(step?.kind).toBe('tool');
    if (step?.kind !== 'tool') return;
    expect(searchWebStepTitle(step)).toBe('已搜索网页：Agent loop · openai.com');
  });
});
