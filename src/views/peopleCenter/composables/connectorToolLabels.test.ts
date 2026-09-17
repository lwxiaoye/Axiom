/**
 * 连接器工具的时间线文案（2026-07-29 深扫 P1）。
 *
 * 连接器工具名是 `{provider}_{action}` 由后端动态拼出来的（connectors.py 的 _safe_name），
 * 静态文案表枚举不完，于是全部落到兜底 `正在调用 qqmail_list_recent…` ——
 * 内部 snake_case 标识符被直接摊给用户看，夹在「已读取文件」「已搜索网页」中间像半成品。
 * 修法是按 provider + action 两段翻译；未知 provider 仍走原兜底（不硬造中文）。
 */
import { toolStepDisplay } from './executionTimeline';

describe('连接器工具文案', () => {
  it('QQ 邮箱的最近邮件不再暴露内部工具名', () => {
    const running = toolStepDisplay({ name: 'qqmail_list_recent', status: 'running' });
    const done = toolStepDisplay({ name: 'qqmail_list_recent', status: 'completed' });
    const failed = toolStepDisplay({ name: 'qqmail_list_recent', status: 'failed' });
    for (const view of [running, done, failed]) {
      expect(view.label).not.toContain('qqmail_list_recent');
      expect(view.label).toContain('QQ 邮箱');
    }
    expect(done.label).toContain('查看最近内容');
    expect(failed.label).toContain('失败');
  });

  it('读取单封邮件与搜索走不同动词', () => {
    expect(toolStepDisplay({ name: 'qqmail_read_message', status: 'completed' }).label)
      .toContain('读取内容');
    expect(toolStepDisplay({ name: 'github_search_repos', status: 'completed' }).label)
      .toContain('搜索');
    expect(toolStepDisplay({ name: 'github_search_repos', status: 'completed' }).label)
      .toContain('GitHub');
  });

  it('未知 provider 保持原兜底，不硬造中文', () => {
    const view = toolStepDisplay({ name: 'someunknown_do_thing', status: 'running' });
    expect(view.label).toContain('someunknown_do_thing');
  });

  it('内置工具的既有文案不受影响', () => {
    expect(toolStepDisplay({ name: 'read_file', status: 'completed' }).label).toContain('已读取');
    expect(toolStepDisplay({ name: 'search_web', status: 'failed' }).label).toContain('搜索');
  });
});

describe('折叠组头如实报失败（2026-07-29）', () => {
  // 「查阅网页」族失败也会被折进组（入组判据放宽成 status !== 'running'），
  // 而组头此前把状态硬写 completed、文案硬写「· 已完成」——3 次抓取挂 1 次时收起态
  // 显示「查阅网页 · 3 次 · 已完成」，与单行失败被伪装成成功是同一个母题。
  // 这里守 reducer 侧的失败计数；组头 class/文案的守卫在 MessageList.browserSteps.test.ts。
  it('runGroup 带上失败成员计数', async () => {
    const { collapseSandboxRuns } = await import('./executionTimeline');
    const row = (i: number, status: string) => ({
      type: 'step' as const,
      stepIndex: i,
      nested: false,
      step: { kind: 'tool', name: 'browser_fetch', label: '已读取网页内容', status } as any,
    });
    const out = collapseSandboxRuns(
      [row(0, 'completed'), row(1, 'failed'), row(2, 'completed')] as any, 'm1', {},
    );
    const group = (out as any[]).map((r) => r.step).find((s: any) => s?.kind === 'runGroup');
    if (!group) return; // 入组判据/阈值变了：交给 executionTimelineRunGroup.test.ts 守
    expect(group.count).toBe(3);
    expect(group.failedCount).toBe(1);
  });

  it('全部成功时 failedCount 为 0（阴性对照）', async () => {
    const { collapseSandboxRuns } = await import('./executionTimeline');
    const row = (i: number) => ({
      type: 'step' as const,
      stepIndex: i,
      nested: false,
      step: { kind: 'tool', name: 'browser_fetch', label: '已读取网页内容', status: 'completed' } as any,
    });
    const out = collapseSandboxRuns([row(0), row(1), row(2)] as any, 'm2', {});
    const group = (out as any[]).map((r) => r.step).find((s: any) => s?.kind === 'runGroup');
    if (!group) return;
    expect(group.failedCount).toBe(0);
  });
});

describe('常驻标记的刷新恢复（2026-07-29 深扫补齐后半段）', () => {
  // 后端把上下文压缩提示/路由智能体/外部推荐/内部推荐 id 投影进了 trace（白名单+投影
  // 分支双闸），但**前端不读就等于没做**——那半最容易被误判成"已完成"。这里锁消费点。
  it('restoreExecutionTrace 透传四个常驻字段', async () => {
    const { restoreExecutionTrace } = await import('./executionTimeline');
    const out: any = restoreExecutionTrace({
      compacted_note: '已自动整理较早对话',
      routed_agent: { id: 'a1', name: '数据分析助手' },
      recommendations: [{ name: '外部应用' }],
      recommended_agent_ids: ['app-1', 'app-2'],
    } as any);
    expect(out.compactedNote).toBe('已自动整理较早对话');
    expect(out.routedAgent).toBe('数据分析助手');
    expect(out.externalRecs).toHaveLength(1);
    // 内部推荐只带 id：整卡快照会在智能体改名/下架后与真实应用对不上
    expect(out.recommendedAgentIds).toEqual(['app-1', 'app-2']);
    expect(out.recommendedAgents).toBeUndefined();
  });

  it('空 trace 不凭空造出这些标记（阴性对照）', async () => {
    const { restoreExecutionTrace } = await import('./executionTimeline');
    const out: any = restoreExecutionTrace({} as any);
    expect(out.compactedNote).toBeUndefined();
    expect(out.routedAgent).toBeUndefined();
    expect(out.externalRecs).toBeUndefined();
    expect(out.recommendedAgentIds).toBeUndefined();
  });
});
