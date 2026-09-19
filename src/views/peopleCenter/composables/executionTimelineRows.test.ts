/**
 * 执行行缓存指纹 + 面板表头（2026-07-28）。
 *
 * 指纹是 MessageList `executionRows()` 缓存的失效判据。它的两种错法方向相反：
 *   · 漏收一个会改变**行结构**的字段 → 该重排的行不重排（缓存吐出过期布局，静默）；
 *   · 多收一个纯展示字段 → 缓存天天失效，等于没加（只是慢，不会错）。
 * 所以测试同时压两侧：结构变了必须变，展示字段变了必须不变。
 *
 * 独立成文件：executionTimeline.test.ts 有并行会话的未提交改动。
 */
import {
  buildExecutionRows,
  execRowsSignature,
  isLiveRunningAction,
  shellInputTitle,
  visibleTaskPlan,
  type AgentStep,
} from './executionTimeline';

function tool(over: Partial<Extract<AgentStep, { kind: 'tool' }>> = {}): AgentStep {
  return { kind: 'tool', name: 'bash', status: 'completed', label: '已执行', ...over } as AgentStep;
}

describe('execRowsSignature：结构变了就必须变', () => {
  const base = { agentSteps: [tool()] };

  it('新增步骤', () => {
    expect(execRowsSignature({ agentSteps: [tool(), tool()] }, false))
      .not.toBe(execRowsSignature(base, false));
  });

  it('步骤状态从 running 变 completed（折叠族只收已完成的行）', () => {
    expect(execRowsSignature({ agentSteps: [tool({ status: 'running' })] }, false))
      .not.toBe(execRowsSignature(base, false));
  });

  it('冒出 error（失败行永不折叠）', () => {
    expect(execRowsSignature({ agentSteps: [tool({ error: '沙箱超时' })] }, false))
      .not.toBe(execRowsSignature(base, false));
  });

  it('冒出 target（沙箱探查族的 noTarget 判据：带产物就不能藏）', () => {
    expect(execRowsSignature({ agentSteps: [tool({ target: '2 个产物' })] }, false))
      .not.toBe(execRowsSignature(base, false));
  });

  it('planKey 变了（分桶归属变，行会挪到另一个计划组下）', () => {
    expect(execRowsSignature({ agentSteps: [tool({ planKey: 'p2' })] }, false))
      .not.toBe(execRowsSignature({ agentSteps: [tool({ planKey: 'p1' })] }, false));
  });

  it('工具名变了（跨族不合并）', () => {
    expect(execRowsSignature({ agentSteps: [tool({ name: 'read_file' })] }, false))
      .not.toBe(execRowsSignature(base, false));
  });

  it('note 正文变了（相邻同文才去重，逐字参与判定）', () => {
    const a = { agentSteps: [{ kind: 'note', text: '正在整理' } as AgentStep] };
    const b = { agentSteps: [{ kind: 'note', text: '正在整理结果' } as AgentStep] };
    expect(execRowsSignature(a, false)).not.toBe(execRowsSignature(b, false));
  });

  it('计划项的 key / 标题 / 状态', () => {
    const plan = (status: 'pending' | 'running') => ({
      taskPlan: [{ key: 'k1', title: '制作', status }],
      agentSteps: [],
    });
    expect(execRowsSignature(plan('pending'), true)).not.toBe(execRowsSignature(plan('running'), true));
    expect(
      execRowsSignature({ taskPlan: [{ key: 'k2', title: '制作', status: 'pending' }], agentSteps: [] }, true),
    ).not.toBe(execRowsSignature(plan('pending'), true));
  });

  it('running 开关（渐进披露：运行中藏 pending 计划行，终态回看全量展示）', () => {
    expect(execRowsSignature(base, true)).not.toBe(execRowsSignature(base, false));
  });

  it('归拢组展开态（组键顺序不影响结果，避免 Object.keys 顺序抖动导致的假失效）', () => {
    expect(execRowsSignature(base, false, ['3:rg:1'])).not.toBe(execRowsSignature(base, false, []));
    expect(execRowsSignature(base, false, ['3:rg:1', '3:rg:5']))
      .toBe(execRowsSignature(base, false, ['3:rg:5', '3:rg:1']));
  });

  it('字段之间不粘连：name/status 的内容互换不能撞出同一个指纹', () => {
    expect(execRowsSignature({ agentSteps: [tool({ name: 'ab', status: 'completed' })] }, false))
      .not.toBe(execRowsSignature({ agentSteps: [tool({ name: 'a', status: 'bcompleted' as never })] }, false));
  });
});

describe('execRowsSignature：纯展示字段变了不该失效', () => {
  // 行对象持有的是 step **原对象引用**，label/preview/耗时变了模板照样重新渲染
  // （读的是 row.step.x）；让它们参与失效判定，缓存在流式期间就等于没加。
  it.each([
    ['label', { label: '正在执行…' }],
    ['preview', { preview: 'exit_code=0' }],
    ['durationMs', { durationMs: 4200 }],
    ['elapsedMs', { elapsedMs: 900 }],
    ['intent', { intent: '生成封面' }],
    ['urls', { urls: ['https://a.example.com/'] }],
    ['shot', { shot: 'data:image/jpeg;base64,AAAA' }],
  ])('%s', (_name, patch) => {
    expect(execRowsSignature({ agentSteps: [tool(patch)] }, false))
      .toBe(execRowsSignature({ agentSteps: [tool()] }, false));
  });

  it('同一份数据反复求指纹恒等（缓存命中的前提）', () => {
    const message = { agentSteps: [tool(), tool({ name: 'read_file', target: 'a.md' })] };
    expect(execRowsSignature(message, true, ['1:rg:0'])).toBe(execRowsSignature(message, true, ['1:rg:0']));
  });

  it('空消息不抛', () => {
    expect(typeof execRowsSignature({}, false)).toBe('string');
  });
});

describe('shellInputTitle：展开面板里 command 段的表头', () => {
  it('bash 的命令全文是「执行命令」，不是「入参」', () => {
    // 上一批把 args.command 接进 command 字段后，面板里终于能看到 bash 跑了什么，
    // 但表头只特判了 run_code，bash 落到兜底的「入参」——一条 shell 命令不是入参。
    expect(shellInputTitle('bash')).toBe('执行命令');
  });

  it('其余工具兜底「入参」（它们的 command 本就是空的，这条极少出现）', () => {
    expect(shellInputTitle('write_file')).toBe('入参');
    expect(shellInputTitle('')).toBe('入参');
  });
});

describe('isLiveRunningAction：做完的步骤不再算当前动作', () => {
  const search = (): AgentStep => ({
    kind: 'tool',
    name: 'search_web',
    status: 'running',
    label: '正在检索网页',
    intent: '正在检索网页',
  });
  const thinking = (status: 'running' | 'completed' = 'running'): AgentStep => ({
    kind: 'thinking',
    text: '整理检索结果',
    status,
  });

  it('检索仍是最后一步时保持进行中', () => {
    expect(isLiveRunningAction([search()], 0)).toBe(true);
  });

  it('后面已经开始思考时，检索不再闪烁', () => {
    expect(isLiveRunningAction([thinking('completed'), search(), thinking('running')], 1)).toBe(false);
  });

  it('后面出现公开叙述时也不再闪烁', () => {
    expect(isLiveRunningAction([search(), { kind: 'note', text: '网页检索已经完成' }], 0)).toBe(false);
  });

  it('并行仍在 running 的工具互不取消', () => {
    const other: AgentStep = { kind: 'tool', name: 'deep_read', status: 'running', label: '正在深读网页…' };
    expect(isLiveRunningAction([search(), other], 0)).toBe(true);
    expect(isLiveRunningAction([search(), other], 1)).toBe(true);
  });

  it('后面工具已经收尾，则前面的 running 也不再算当前', () => {
    const done: AgentStep = { kind: 'tool', name: 'update_plan', status: 'completed', label: '已更新计划' };
    expect(isLiveRunningAction([search(), done], 0)).toBe(false);
  });
});

describe('visibleTaskPlan / buildExecutionRows：现行步骤按数组顺序，不展示已替换残骸', () => {
  const plan = [
    { key: 'write', title: '撰写并生成 Word 文档', status: 'running' as const },
    { key: 'qa', title: '质检并交付文档', status: 'pending' as const },
    { key: 'outline', title: '确定文档结构大纲', status: 'invalidated' as const },
    { key: 'old-write', title: '生成 Word 文档', status: 'invalidated' as const },
  ];

  it('任务协作只保留现行步骤，顺序与数组一致', () => {
    expect(visibleTaskPlan(plan).map((step) => step.title)).toEqual([
      '撰写并生成 Word 文档',
      '质检并交付文档',
    ]);
  });

  it('时间线组头跳过 invalidated，名下动作落到尾部而不是插在后面', () => {
    const rows = buildExecutionRows(plan, [
      { kind: 'tool', name: 'bash', status: 'completed', label: '已落盘', planKey: 'outline' } as AgentStep,
      { kind: 'tool', name: 'bash', status: 'running', label: '正在生成', planKey: 'write' } as AgentStep,
    ]);
    const titles = rows
      .filter((row) => row.type === 'plan')
      .map((row) => (row.type === 'plan' ? row.item.title : ''));
    expect(titles).toEqual(['撰写并生成 Word 文档', '质检并交付文档']);
    const tail = rows.filter((row) => row.type === 'step' && !row.nested);
    expect(tail.some((row) => row.type === 'step' && row.step.planKey === 'outline')).toBe(true);
  });
});
