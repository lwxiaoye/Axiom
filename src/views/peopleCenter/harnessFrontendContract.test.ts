import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import {
  applyCapabilityLoaded,
  applyCommentary,
  applyCompaction,
  applyModelConnection,
  applyReasoningDelta,
  applyTaskPlan,
  applyToolEvent,
  clearTransientReasoning,
  deriveRunPanel,
  ensureRunningThought,
  parkTransientReasoning,
  restoreExecutionTrace,
  revealAssistantOutput,
} from './composables/executionTimeline';

const root = resolve(__dirname);
const apiSource = readFileSync(resolve(root, 'agentApi.ts'), 'utf8');
const chatSource = readFileSync(resolve(root, 'composables/useCenterChat.ts'), 'utf8');
const processStreamsSource = readFileSync(resolve(root, 'composables/harnessProcessStreams.ts'), 'utf8');
const centerSource = readFileSync(resolve(root, 'center.vue'), 'utf8');
const timelineSource = readFileSync(resolve(root, 'composables/executionTimeline.ts'), 'utf8');
const collaborationSource = readFileSync(resolve(root, 'components/TaskCollaborationPopover.vue'), 'utf8');
const messageListSource = readFileSync(resolve(root, 'components/MessageList.vue'), 'utf8');
const compactionStepSource = readFileSync(resolve(root, 'components/CompactionStep.vue'), 'utf8');
const meterSource = readFileSync(resolve(root, 'composables/generationMeterStatus.ts'), 'utf8');
const chatTabSource = readFileSync(resolve(root, 'tabs/ChatTab.vue'), 'utf8');
const overlaySource = readFileSync(resolve(root, 'components/WorkspaceOverlay.vue'), 'utf8');
const visibilitySource = readFileSync(resolve(root, 'mainChatFeatureVisibility.ts'), 'utf8');
const execTeamSource = readFileSync(resolve(root, 'components/ExecTeamPanel.vue'), 'utf8');
const chatPageSource = readFileSync(resolve(root, 'pages/ChatPage.vue'), 'utf8');
const subagentPanelSource = readFileSync(resolve(root, 'components/SubagentChatPanel.vue'), 'utf8');
const agentMarketSource = readFileSync(resolve(root, 'composables/useAgentMarket.ts'), 'utf8');
const agentMarketTabSource = readFileSync(resolve(root, 'tabs/AgentMarketTab.vue'), 'utf8');

describe('主对话 Harness 前端契约', () => {
  it('并发同名工具按 callId 精确收口', () => {
    const target: any = { agentSteps: [], toolSteps: [] };
    applyToolEvent(target, { phase: 'started', name: 'read_file', callId: 'call-a' });
    applyToolEvent(target, { phase: 'started', name: 'read_file', callId: 'call-b' });
    applyToolEvent(target, {
      phase: 'completed', name: 'read_file', callId: 'call-a', preview: 'A', timestamp: 10,
    });

    const first = target.agentSteps.find((step: any) => step.callId === 'call-a');
    const second = target.agentSteps.find((step: any) => step.callId === 'call-b');
    expect(first).toMatchObject({ status: 'completed', preview: 'A' });
    expect(second).toMatchObject({ status: 'running' });
  });

  it('所有请求固定声明 Harness Protocol 1', () => {
    expect(apiSource).toContain("'X-Harness-Protocol-Version': '1'");
    expect(apiSource).not.toContain("'X-Harness-Protocol-Version': 'v1'");
    expect(apiSource).not.toContain("'X-Protocol-Version'");
  });

  it('Run 创建后立即订阅唯一事件流，终态仲裁使用权威快照接口', () => {
    expect(apiSource).toContain("requestAgentApi('/chat/runs'");
    expect(apiSource).toContain('await subscribeChatRun(String(started.run_id)');
    expect(apiSource).toContain('`/chat/runs/${encodeURIComponent(runId)}/events${query}`');
    expect(apiSource).toContain('`/chat/runs/${encodeURIComponent(runId)}`');
    expect(apiSource).not.toContain('`/chat/runs/${encodeURIComponent(runId)}/state`');
  });

  it('Profile 只通过 agent_mode 传递，不再发送旧模式字段', () => {
    expect(chatSource).toContain('agent_mode:');
    expect(chatSource).not.toMatch(/\btask_mode\b/);
    expect(chatSource).not.toMatch(/\bresearch_mode\b/);
  });

  it('reasoning 走瞬时 delta、可展开 Thought 步骤和 completed 回放', () => {
    expect(apiSource).toContain("case 'message.reasoning.delta':");
    expect(apiSource).toContain("case 'message.reasoning.completed':");
    expect(apiSource).toContain('onReasoningDelta: params.onReasoningDelta');
    expect(apiSource).toContain('onReasoningCompleted: params.onReasoningCompleted');
    expect(chatSource).toContain('onReasoningDelta: (delta, fullReasoning) =>');
    expect(chatSource).toContain("from './harnessProcessStreams'");
    expect(chatSource.match(/reasoningStream\.push\(delta, fullReasoning\)/g)).toHaveLength(3);
    expect(chatSource.match(/return reasoningStream\.complete\(payload\)/g)).toHaveLength(3);
    expect(processStreamsSource).toContain('parkTransientReasoning(target, {');
    expect(processStreamsSource).toContain('text: payload?.text || authoritative');
    expect(timelineSource).toContain("kind: 'thinking'");
    expect(timelineSource).toContain('reasoning_summary?:');
    expect(chatSource).toContain('ensureRunningThought(assistantMessage)');
    expect(timelineSource).toContain('export function ensureRunningThought');
  });

  it('message.completed 通过专用回调把权威全文按序排入绘制队列', () => {
    expect(apiSource).toContain('cb.onMessageCompleted?.(content)');
    expect(apiSource).toContain('onMessageCompleted: params.onMessageCompleted');
    expect(chatSource).toContain('onMessageCompleted: (fullContent) =>');
    expect(chatSource).toContain('tw.finalize(');
    expect(chatSource).toContain('paintAssistantStreamText(');
    expect(chatSource).toContain("if (phase === 'complete')");
    expect(chatSource).not.toContain('cancelFrame();\n        commit(targetContent);\n        resolveWaiters();');
  });

  it('partial 与 cancelled 保留独立终态，不冒充 completed', () => {
    expect(apiSource).toContain('cb.onRunPartial?.({');
    expect(apiSource).toContain('cb.onRunCancelled?.({');
    expect(apiSource).not.toMatch(/case 'run\.partial':[\s\S]{0,240}cb\.onRunCompleted/);
    expect(apiSource).not.toMatch(/case 'run\.cancelled':[\s\S]{0,180}cb\.onRunCompleted/);

    const restored = restoreExecutionTrace({
      status: 'partial',
      completedAt: 100,
      task_plan: [
        { key: 'done', title: '已完成', status: 'completed' },
        { key: 'left', title: '未完成', status: 'pending' },
      ],
    } as any);
    expect(restored.runPartial).toBe(true);
    expect(restored.runFailed).toBeUndefined();
    expect(restored.taskPlan?.map((step) => step.status)).toEqual(['completed', 'pending']);
  });

  it('前端不在 Run 终态时乐观改写权威 Plan', () => {
    expect(timelineSource).not.toContain("item.status = status === 'completed' ? 'completed' : 'failed'");
    expect(timelineSource).not.toContain("status: failOpen ? 'failed' as const : 'completed' as const");
    expect(timelineSource).not.toContain('const trivialPlan =');

    const terminalPlan = deriveRunPanel([{
      role: 'assistant',
      runId: 'completed-run',
      runCompletedAt: 100,
      taskPlan: [{ key: 'write', title: '撰写文档', status: 'running' }],
    } as any]);
    expect(terminalPlan.settled).toBe(true);
    expect(terminalPlan.running).toBe(false);
    // 终态仍保留权威快照，顶栏组件只把它从“活动计划”视图移走。
    expect(terminalPlan.plan[0].status).toBe('running');
    expect(collaborationSource).toContain('v-if="!panel.settled"');
    expect(collaborationSource).toContain('!props.panel.settled && props.panel.plan.some');
  });

  it('SSE 空闲钟只认 data 帧，ping 注释不能把半开连接伪装成仍在推步骤', () => {
    expect(apiSource).toContain('let lastDataAt = Date.now()');
    expect(apiSource).toContain('if (sawData) lastDataAt = Date.now()');
    expect(apiSource).toContain("if (!trimmed.startsWith('data:')) continue");
    expect(apiSource).toContain('const STREAM_IDLE_TIMEOUT_MS = 45_000');
  });

  it('context.compaction 画出 Codex 过程：Compacting context → Context compacted', () => {
    expect(apiSource).toContain("case 'context.compaction':");
    expect(apiSource).toContain('cb.onCompaction?.({');
    expect(chatSource).toContain('onCompaction: (payload) =>');
    expect(chatSource).toContain('applyCompaction(target, payload)');
    expect(timelineSource).toContain("kind: 'compaction'");
    expect(messageListSource).toContain('<CompactionStep');
    expect(messageListSource).toContain(':active="row.step.status === \'running\'"');
    // 文案改成用户能看懂的中文（内部术语「Compacting context」不再露出）；过程与时长逻辑不变。
    expect(compactionStepSource).toContain("const LABEL = '正在整理较早的对话'");
    expect(compactionStepSource).toContain("props.failed ? '较早的对话整理失败' : '已整理较早的对话'");
    expect(compactionStepSource).toContain('const SWEEP_MS = 2000');
    expect(compactionStepSource).toContain('shimmer.rs');

    const target: any = { agentSteps: [] };
    applyCompaction(target, { status: 'started' });
    expect(target.agentSteps).toEqual([
      expect.objectContaining({ kind: 'compaction', status: 'running' }),
    ]);
    applyCompaction(target, { status: 'completed', seconds: 4 });
    expect(target.agentSteps[0]).toEqual(
      expect.objectContaining({ kind: 'compaction', status: 'completed', seconds: 4 }),
    );

    const restored = restoreExecutionTrace({
      steps: [{ kind: 'compaction', status: 'completed', seconds: 3 }],
    } as any);
    expect(restored.agentSteps).toEqual([
      { kind: 'compaction', status: 'completed', seconds: 3, planKey: undefined },
    ]);
  });

  it('模型流式降级用结构化状态原位收口，不混进最终回答', () => {
    expect(apiSource).toContain("case 'model.connection':");
    expect(apiSource).toContain('cb.onModelConnection?.({');
    expect(chatSource).toContain('onModelConnection: (payload) =>');
    expect(chatSource).toContain('applyModelConnection(target, payload)');

    const target: any = { agentSteps: [] };
    applyModelConnection(target, {
      status: 'recovering', transport: 'stream_retry', attempt: 1, maxRetries: 5,
    });
    expect(target.agentSteps).toEqual([
      expect.objectContaining({
        kind: 'note',
        connectionStatus: 'recovering',
        text: '模型连接暂时中断，正在自动重连（1/5）…',
      }),
    ]);
    applyModelConnection(target, {
      status: 'recovering', transport: 'stream_retry', attempt: 5, maxRetries: 5,
    });
    expect(target.agentSteps).toHaveLength(1);
    expect(target.agentSteps[0]).toMatchObject({
      connectionStatus: 'recovering', text: '模型连接暂时中断，正在自动重连（5/5）…',
    });
    applyModelConnection(target, { status: 'recovered', transport: 'stream_retry' });
    expect(target.agentSteps[0]).toMatchObject({
      kind: 'note', connectionStatus: 'recovered', text: '模型连接已恢复，继续执行。',
    });

    applyModelConnection(target, {
      status: 'recovering', transport: 'stream_retry', attempt: 5, maxRetries: 5,
    });
    applyModelConnection(target, {
      status: 'failed', transport: 'stream_retry', attempt: 5, maxRetries: 5,
    });
    expect(target.agentSteps[1]).toMatchObject({
      kind: 'note', connectionStatus: 'failed', text: '模型连接连续重试 5 次仍未恢复，已停止本轮。',
    });
    expect(target.content).toBeUndefined();
    expect(target.preamble).toBeUndefined();
  });

  it('正典能力与产物事件有完整消费链路', () => {
    expect(apiSource).toContain("case 'capability.loaded':");
    expect(apiSource).toContain("case 'artifact.saved':");
    expect(apiSource).toContain("case 'research.progress':");
    expect(chatSource).toContain('onCapabilityLoaded: (names) =>');
    expect(chatSource).toContain('onArtifactSaved: (payload) =>');
    expect(chatSource).toContain('onResearchProgress: (payload) =>');

    const restored = restoreExecutionTrace({
      loaded_capabilities: ['skill:PPT'],
      steps: [],
    } as any);
    expect(restored.loadedCapabilities).toEqual(['skill:PPT']);
    expect(restored.agentSteps).toBeUndefined();
  });

  it('Skill capability 旧事件不生成摘要，公开阐述仍作为首句保留', () => {
    const target: any = { agentSteps: [] };
    applyCapabilityLoaded(target, ['skill:ppt-studio']);
    applyCommentary(target, '已经加载演示文稿能力，接下来开始整理内容。');

    expect(target.preamble).toBe('已经加载演示文稿能力，接下来开始整理内容。');
    expect(target.agentSteps).toEqual([]);
  });

  it('有任务计划或子智能体委派时，入口切换为三行错峰点亮的任务计划态', () => {
    expect(collaborationSource).toContain('v-if="hasTaskActivity"');
    expect(collaborationSource).toContain("step.status === 'pending' || step.status === 'running'");
    expect(collaborationSource).toContain("teamMembers.value.some((run) => run.status === 'running')");
    expect(collaborationSource).toContain('hasOpenPlanSteps.value || hasRunningTeam.value');
    expect(collaborationSource).toContain("hasTaskActivity ? '任务计划' : '任务协作'");
    expect(collaborationSource).toContain('@keyframes tct-plan-line-highlight');
    expect(collaborationSource).toContain('animation-delay: 0.3s');
    expect(collaborationSource).toContain('animation-delay: 0.6s');
    expect(collaborationSource).toContain('52.6%, 68.3%');
    const highlight = collaborationSource.match(/@keyframes tct-plan-line-highlight \{[\s\S]*?\n\}/)?.[0] || '';
    expect(highlight).toContain('#60a8f0');
    expect(highlight).toContain('#b4daff');
    expect(highlight).not.toContain('#4f6ef7');
    expect(highlight).not.toContain('#aeb9ec');
    expect(collaborationSource).toMatch(/\.task-collaboration-trigger\.task-active[\s\S]*?color: #4d525c;/);
  });

  it('委派步骤下方的成员胶囊显示真实智能体名称，不让场景化岗位名覆盖', () => {
    expect(messageListSource).toContain("{{ row.step.name || '子智能体' }}");
    expect(messageListSource).not.toContain('{{ run.roleName || run.name }}');
    expect(messageListSource).toContain('岗位：${run.roleName}');
  });

  it('成员只在 started 后以无动画头像名称胶囊进入执行时间线', () => {
    expect(chatTabSource).toContain(':subagents="subagents"');
    expect(messageListSource).toContain('class="pill-agent-avatar"');
    expect(messageListSource).toContain('subagentStepIconUrl(message, row.step)');
    expect(messageListSource).toContain("row.step.kind === 'subagent'");
    expect(messageListSource).not.toContain('class="sub-team-pills"');
    expect(messageListSource).toContain('border-radius: 999px');
    expect(messageListSource).toContain(":class=\"['subagent-member-row', row.step.status]\"");
    expect(messageListSource).toContain('.subagent-member-row {');
    expect(messageListSource).not.toContain('pill-breathe');
    expect(messageListSource).not.toContain('pill-status');
  });

  it('委派过程按真实事件展开，整轮状态头与子智能体步骤分层展示', () => {
    expect(apiSource).toContain("case 'subagent.preparing':");
    expect(timelineSource).toContain('正在打开「${name}」并准备委派');
    expect(timelineSource).toContain("operation: 'subagent_prepare'");
    expect(timelineSource).toContain("operation: 'subagent_node'");
    expect(timelineSource).toContain('currentNodeStep.label = nodeLabel');
    expect(timelineSource).not.toContain('「${name}」已完成委派任务');
    expect(messageListSource).toContain('{{ execHeadTitle(message) }}');
    expect(messageListSource).toContain('class="exec-head"');
    expect(messageListSource).toContain('class="exec-head-elapsed">{{ execHeadTimeText(message) }}');
    expect(messageListSource).toContain('v-show="!isExecCollapsed(message)"');
    expect(messageListSource).toContain('hasExecutionStreamBody(message)');
    expect(messageListSource).toContain("['subagent-member-row', row.step.status]");
  });

  it('委派帧把真实头像存入运行档，不能依赖临时 @ 候选目录', () => {
    expect(apiSource).toContain('icon: d.icon ? String(d.icon) : undefined');
    expect(timelineSource).toContain('...(ev.icon ? { icon: ev.icon } : {}),');
    expect(messageListSource).toContain("icon: run.icon || current?.icon || ''");
  });

  it('@ 候选只读取当前用户实际可委派的 Agent API 集合', () => {
    expect(apiSource).toContain("requestAgentApi(`/chat/subagents?${params.toString()}`, { method: 'GET' })");
    expect(apiSource).not.toContain("url: '/app/appInfo/my/all/list'");
    expect(apiSource).toContain("const params = new URLSearchParams({ limit: '50' })");
  });

  it('智能体广场创建人头像直接使用应用接口返回字段', () => {
    expect(agentMarketSource).toContain("myAppList({ column: 'createTime', order: 'desc' })");
    expect(agentMarketSource).not.toContain('queryMarketplaceCreators');
    expect(agentMarketTabSource).toContain('item.createByAvatar');
  });

  it('智能体目录 404 不向主对话弹出 axios 原文，也不在发送时反复重试', () => {
    const myAppListStart = agentMarketSource.indexOf('const myAppList = ');
    const myAppListSource = agentMarketSource.slice(myAppListStart, agentMarketSource.indexOf(';', myAppListStart));
    expect(myAppListStart).toBeGreaterThan(-1);
    expect(myAppListSource).toContain('errorMessageMode: \'none\'');
    expect(myAppListSource).toContain('successMessageMode: \'none\'');
    expect(agentMarketSource).toContain('catalogUnavailable');
    expect(agentMarketSource).toContain('catalogLoaded');
    expect(agentMarketSource).toContain("options.activeSection.value !== 'agent'");
    expect(agentMarketSource).toContain('智能体广场暂时无法加载，请稍后重试');
    expect(agentMarketSource).not.toContain('if (appResult.status === \'rejected\') throw appResult.reason');
  });

  it('任务计划与委派进入终态后撤掉顶栏动效', () => {
    expect(collaborationSource).toContain("s.status === 'skipped' || s.status === 'invalidated'");
    expect(collaborationSource).not.toContain('hasTaskSteps.value || teamMembers.value.length > 0');
  });

  it('任务计划面板锚定在顶栏按钮下方并避开底部输入框', () => {
    expect(collaborationSource).toContain('<Teleport to="body">');
    expect(collaborationSource).toContain('function placePanel()');
    expect(collaborationSource).toContain('const COMPOSER_CLEARANCE = 120');
    expect(collaborationSource).toContain('let left = rect.left');
    expect(collaborationSource).not.toMatch(/\.task-collaboration-panel \{[\s\S]*?\bright:\s*0;/);
  });

  it('Plan 版本或 SSE 游标断层时重拉权威 Run 快照', () => {
    expect(chatSource).toContain('nextVersion > previousVersion + 1');
    expect(chatSource).toContain('await getRunState(runId)');
    expect(chatSource).toContain('onSequenceGap: () =>');
  });

  it('计划协议保留 skipped 与 invalidated，不降级成 pending', () => {
    expect(apiSource).toContain("'skipped', 'invalidated'");
    const restored = restoreExecutionTrace({
      task_plan: [
        { key: 'skip', title: '已跳过', status: 'skipped' },
        { key: 'old', title: '已失效', status: 'invalidated' },
      ],
    } as any);
    expect(restored.taskPlan?.map((step) => step.status)).toEqual(['skipped', 'invalidated']);
    expect(collaborationSource).toContain("if (status === 'invalidated') return 'invalidated'");
    expect(collaborationSource).not.toContain("step.status === 'skipped' || step.status === 'invalidated'");

    const panel = deriveRunPanel([{
      role: 'assistant',
      taskPlan: [
        { key: 'write', title: '撰写并生成 Word 文档', status: 'running' },
        { key: 'qa', title: '质检并交付文档', status: 'pending' },
        { key: 'outline', title: '确定文档结构大纲', status: 'invalidated' },
        { key: 'old-write', title: '生成 Word 文档', status: 'invalidated' },
      ],
    } as any]);
    expect(panel.plan.map((step) => step.title)).toEqual([
      '撰写并生成 Word 文档',
      '质检并交付文档',
    ]);
    expect(timelineSource).toContain('export function visibleTaskPlan');
  });

  it('任务协作面板展示目标契约与步骤验收标准', () => {
    expect(collaborationSource).toContain('contractBanner');
    expect(collaborationSource).toContain('验收：{{ step.acceptance }}');
    expect(collaborationSource).toContain('class="task-goal-contract"');
    expect(apiSource).toContain('acceptance: s.acceptance ? String(s.acceptance)');
    expect(centerSource).toContain('acceptance: step.acceptance');
    expect(centerSource).toContain('goalContract: plan.goal_contract || base.goalContract');
    expect(centerSource).toContain('blocked: Boolean(step.blocked)');
    expect(collaborationSource).toContain('等待上一步');
    const restored = restoreExecutionTrace({
      goal_contract: { goal: '国产数据库选型', deliverable: '文件' },
      task_plan: [
        { key: 'research', title: '调研竞品', status: 'running', acceptance: '至少两份可核验来源' },
      ],
    } as any);
    expect(restored.taskGoalContract?.goal).toBe('国产数据库选型');
    expect(restored.taskPlan?.[0].acceptance).toBe('至少两份可核验来源');
  });

  it('历史 trace 恢复思考正文，仍丢弃旧任务图投影', () => {
    const restored = restoreExecutionTrace({
      steps: [
        { kind: 'thinking', text: '内部思考', seconds: 2.5 },
        { kind: 'note', text: '正在核对文件。' },
      ],
      task_graph: { graph_id: 'old', nodes: [{ key: 'n1' }] },
    } as any);
    expect(restored.agentSteps).toEqual([
      { kind: 'thinking', text: '内部思考', status: 'completed', seconds: 2.5, planKey: undefined },
      { kind: 'note', text: '正在核对文件。', planKey: undefined },
    ]);
    expect((restored as any).taskGraph).toBeUndefined();
  });

  it('无 thinking 步骤时用 reasoning_summary 回放思考正文', () => {
    const restored = restoreExecutionTrace({
      reasoning_summary: '已经想清楚下一步。',
      reasoning_seconds: 4,
      steps: [{ kind: 'note', text: '开始执行。' }],
    } as any);
    expect(restored.agentSteps?.[0]).toEqual({
      kind: 'thinking',
      text: '已经想清楚下一步。',
      status: 'completed',
      seconds: 4,
    });
  });

  it('瞬时 reasoning 在 burst 间原位替换顶部尾窗，有正文后思考仍进时间线', () => {
    const target: any = { agentSteps: [], runStartedAt: 1 };

    applyReasoningDelta(target, '正在核对', '正在核对');
    expect(target.reasoningSummary).toBe('正在核对');
    expect(target.agentSteps[0]).toMatchObject({ kind: 'thinking', text: '正在核对', status: 'running' });
    parkTransientReasoning(target, { text: '正在核对', seconds: 1.2 });
    expect(target.reasoningSummary).toBe('正在核对');
    expect(target.reasoningPendingReset).toBe(true);
    expect(target.agentSteps[0]).toMatchObject({
      kind: 'thinking', text: '正在核对', status: 'completed', seconds: 1.2,
    });

    applyToolEvent(target, { phase: 'started', name: 'read_file', args: {} } as any);
    expect(target.reasoningSummary).toBe('正在核对');

    applyReasoningDelta(target, '继续确认', '继续确认');
    expect(target.reasoningSummary).toBe('继续确认');
    expect(target.reasoningPendingReset).toBe(false);
    expect(target.agentSteps.some((step: any) => step.kind === 'thinking' && step.status === 'running')).toBe(true);

    applyCommentary(target, '现有结构已经确认，接下来只调整事件边界。');
    expect(target.reasoningSummary).toBe('');
    expect(target.agentSteps.filter((step: any) => step.kind === 'thinking').length).toBeGreaterThan(0);

    applyReasoningDelta(target, '准备回答', '准备回答');
    parkTransientReasoning(target);
    target.content = '最终回答';
    revealAssistantOutput(target);
    expect(target.reasoningSummary).toBe('');
    applyReasoningDelta(target, '最后一段', '最后一段');
    expect(target.reasoningSummary).toBe('');
    expect(target.agentSteps[target.agentSteps.length - 1]).toMatchObject({
      kind: 'thinking', text: '最后一段', status: 'running',
    });

    clearTransientReasoning(target);
    expect(target.reasoningSummary).toBe('');
    expect(target.agentSteps.some((step: any) => step.kind === 'thinking')).toBe(true);
  });

  it('收到 reasoning 才插入 Thought 步骤，结束后保留全文而不是最后 64 字', () => {
    const target: any = { agentSteps: [] };
    ensureRunningThought(target);
    expect(target.agentSteps).toHaveLength(0);

    const long = 'The user asked a short question. I should answer directly without tools. that I can answer directly without any tools. No plan is needed.';
    applyReasoningDelta(target, long, long);
    expect(target.agentSteps[0]).toMatchObject({ kind: 'thinking', status: 'running' });
    parkTransientReasoning(target, { text: long, seconds: 5 });
    expect(target.agentSteps[0].text).toContain('The user asked a short question');
    expect(target.agentSteps[0].text).toContain('No plan is needed.');
    expect(target.agentSteps[0]).toMatchObject({ status: 'completed', seconds: 5 });

    const busy: any = {
      agentSteps: [{ kind: 'tool', name: 'bash', label: '运行', status: 'running' }],
    };
    ensureRunningThought(busy);
    expect(busy.agentSteps).toHaveLength(1);
  });

  it('工具之后才到的 reasoning.completed 也要带 Thoughts for Ns', () => {
    const target: any = {
      agentSteps: [{ kind: 'tool', name: 'search_web', label: '搜索', status: 'completed' }],
    };
    parkTransientReasoning(target, { text: '根据刚才的搜索继续核对出处。' });
    const thought = target.agentSteps.find((step: any) => step.kind === 'thinking');
    expect(thought).toMatchObject({ status: 'completed', text: '根据刚才的搜索继续核对出处。' });
    expect(thought.seconds).toBeGreaterThanOrEqual(1);
  });

  it('旧 reasoning_summary 只按普通过程说明兼容，不恢复常驻思考样式', () => {
    const target: any = {
      agentSteps: [{ kind: 'tool', name: 'read_file', status: 'completed' }],
    };
    applyCommentary(target, '已核对命令输出，下一步整理结果。', 'reasoning_summary');
    expect(target.agentSteps[target.agentSteps.length - 1]).toEqual({
      kind: 'note',
      text: '已核对命令输出，下一步整理结果。',
      planKey: undefined,
    });

    const restored = restoreExecutionTrace({
      steps: [{ kind: 'summary', text: '旧版公开阶段判断。' }],
    } as any);
    expect(restored.agentSteps).toEqual([{
      kind: 'note',
      text: '旧版公开阶段判断。',
      planKey: undefined,
    }]);
  });

  it('完成轨迹刷新后保留公开过程并默认展开', () => {
    const restored = restoreExecutionTrace({
      status: 'completed',
      startedAt: 100,
      completedAt: 200,
      preamble: '我直接执行并回传输出。',
      steps: [
        {
          kind: 'tool',
          name: 'bash',
          status: 'completed',
          command: 'printf ok',
          preview: 'ok',
        },
        { kind: 'note', text: '这一批结果已经拿到。' },
        { kind: 'note', text: '已运行 `printf ok`，输出是 `ok`。' },
      ],
    } as any);

    expect(restored.preamble).toBe('我直接执行并回传输出。');
    expect(restored.executionCollapsed).toBe(false);
    expect(restored.agentSteps).toEqual([
      expect.objectContaining({
        kind: 'tool',
        name: 'bash',
        status: 'completed',
        command: 'printf ok',
        preview: 'ok',
      }),
      { kind: 'note', text: '这一批结果已经拿到。', planKey: undefined },
      { kind: 'note', text: '已运行 `printf ok`，输出是 `ok`。', planKey: undefined },
    ]);
  });

  it('进入主对话不自动打开上次或最近会话，只预加载历史列表', () => {
    expect(chatSource).not.toContain('center-chat:active-thread');
    expect(chatSource).not.toContain('window.sessionStorage.getItem(activeThreadStorageKey');
    expect(chatSource).not.toContain('const loaded = await loadThread(storedThreadId, true)');
    expect(chatSource).not.toMatch(/loadThread\(threadList\.value\[0\]\.id/);
    expect(chatSource).toContain('await loadThreads()');
    expect(chatSource).toContain('if (viewVersion !== restoreToken) return;');
    expect(chatSource).toContain('if (viewVersion !== viewToken) return false;');
    expect(centerSource).toContain('void centerChat.restoreCurrentThread();');
    expect(centerSource).not.toContain('void centerChat.loadThreads().finally');
    expect(centerSource).toContain('function openNewChat()');
    expect(centerSource).toContain('resetChat()');
    expect(centerSource).toContain("{ key: 'chat' as const, label: '主对话'");
    expect(centerSource).not.toContain('@click="openNewChat"');
    expect(centerSource).toContain('if (opening) void centerChat.loadThreads()');
    const navHandler = centerSource.match(
      /function onNavItem\(section: CenterSectionKey\) \{[\s\S]*?\n\}/,
    )?.[0] || '';
    expect(navHandler).toContain("if (section === 'chat')");
    expect(navHandler).toContain('openNewChat()');
    expect(navHandler).not.toContain('resetChat()');
    expect(centerSource).not.toContain('window.location.reload()');
    expect(centerSource).not.toContain('target="_blank"');
    expect(centerSource).not.toContain('if (chatInput.value.trim()) return;');
    expect(centerSource).toContain("exclude=\"['CenterChatPage']\"");
    expect(centerSource).toContain('chatPageEpoch.value += 1');
    expect(centerSource).not.toContain('nav-task-orbit');
    expect(chatTabSource).not.toContain('正在恢复最近对话');
  });

  it('切回活动 Run 先恢复完整轨迹和计时，再从快照游标后续订新事件', () => {
    expect(chatSource).toContain('findActiveRunReplayTarget(chatMessages.value, serverRun.id)');
    expect(chatSource).toContain('findActiveRunTraceSnapshot(messages, serverRun.id)');
    expect(chatSource).toContain('restoreHarnessRunSnapshot(serverRun, activeTrace)');
    expect(chatSource).toContain('Object.assign(target, restoreHistoryTrace(executionTrace))');
    expect(chatSource).toContain('const runState = await getRunState(serverRun.id)');
    expect(chatSource).toContain('Number(activeTrace?.event_cursor || 0)');
    expect(chatSource).toContain('Number(runState.event_cursor || 0) - 8');
    expect(chatSource).toContain('initialContent: replayTarget.content');
    expect(chatSource).toContain('reuseMessageId: replayTarget.messageId');
    expect(chatSource).not.toContain('replayTarget == null\n            ? undefined');
    expect(chatSource).toContain('游标不可用时禁止退回 sequence=0 可见全量重播');

    const restoredActive = restoreExecutionTrace({
      startedAt: 1788229776392,
      status: 'running',
      steps: [
        { kind: 'thinking', text: 'earlier reasoning', status: 'completed', seconds: 8 },
        { kind: 'tool', name: 'write_file', status: 'running' },
      ],
    } as any);
    expect(restoredActive.runStartedAt).toBe(1788229776392);
    expect(restoredActive.agentSteps).toHaveLength(2);
    expect(restoredActive.agentSteps?.[1]).toMatchObject({
      kind: 'tool',
      name: 'write_file',
      status: 'running',
    });
    expect((restoredActive.agentSteps?.[1] as any)?.error).toBeUndefined();

    const restoredTerminalOrphan = restoreExecutionTrace({
      status: 'completed',
      steps: [{ kind: 'tool', name: 'write_file', status: 'running' }],
    } as any);
    expect(restoredTerminalOrphan.agentSteps?.[0]).toMatchObject({
      kind: 'tool',
      status: 'failed',
      error: '这一步没有跑完（连接中断，并非任务失败）',
    });

    const resetChatBody = chatSource.match(/function resetChat\(\) \{[\s\S]*?\n  \}/)?.[0] || '';
    expect(resetChatBody).toContain('abortController?.abort()');
    expect(resetChatBody).toContain('stopVisibleSubscription()');
    expect(resetChatBody).not.toContain('cancelChatRun');
  });

  it('主对话侧栏与对话区之间可拖动调宽', () => {
    expect(centerSource).toContain('class="nav-resizer"');
    expect(centerSource).toContain('role="separator"');
    expect(centerSource).toContain('onNavResizeStart');
    expect(centerSource).toContain('CENTER_NAV_STORAGE_KEY');
    expect(centerSource).toContain("'--center-nav-width': `${effectiveNavWidth}px`");
  });

  it('plan.updated 解析批准版本与偏离，审查卡和面板消费同一份状态', () => {
    expect(apiSource).toContain('approved_version: d.approved_version == null');
    expect(apiSource).toContain('diverged: Boolean(d.diverged)');
    expect(chatSource).toContain("interactiveSnapshot?.kind === 'plan_confirmation'");
    expect(chatSource).not.toContain("paused?.interactive?.type === 'plan_confirmation'");
    expect(chatSource).toContain('mergeIntoPaused = Boolean(interactiveSnapshot?.ask_user) && !isPlanConfirm');
    expect(chatSource).toContain('planUserMessageId');
    expect(chatSource).toContain('PLAN_EXECUTE_RESUME_RE');
    expect(chatSource).toContain('PLAN_SKIP_RESUME_RE');
    expect(chatSource).toContain('if (unlockPlanExecution)');
    expect(chatSource).toMatch(/if \(unlockPlanExecution\) \{[\s\S]*?planMode\.value = false/);
    expect(chatSource).toContain('&& !unlockPlanExecution');
    expect(chatSource).toContain('unlockedPlanRuns');
    expect(chatSource).toContain('if (!runId || unlockedPlanRuns.has(runId)) return');
    expect(chatSource).toContain('function detachRunObserversBeforeResume(runId: string)');
    expect(chatSource).toContain('detachRunObserversBeforeResume(runId);');
    expect(chatSource).toContain('routeArtifactEventToRun(');
    expect((chatSource.match(/current\.planReport/g) || []).length).toBeGreaterThanOrEqual(2);
    expect(messageListSource).toContain('class="plan-review-card"');
    expect(messageListSource).not.toContain('class="plan-review-steps"');
    expect(messageListSource).toContain('开始执行');
    expect(messageListSource).toContain('好，请执行此计划。');
    expect(messageListSource).toContain('__PLAN_SKIP__');
    expect(messageListSource).not.toContain('跳过，按当前计划执行。');
    expect(messageListSource).not.toContain('开始执行计划');
    expect(messageListSource).not.toContain('进行补充');
    expect(messageListSource).toContain('需要调整计划，直接在这里补充');
    expect(messageListSource).toContain('generationMeterStatus');
    expect(meterSource).toContain('正在规划执行方案');
    expect(messageListSource).toContain('function isPlanReportPending');
    expect(messageListSource).toContain('function hasReadyPlanDocument');
    expect(messageListSource).toContain('buildPlanCardMarkdown');
    expect(messageListSource).toContain('v-html="planCardBodyHtml(message)"');
    expect(messageListSource).toContain('stripLeadingTitleHeadings');
    expect(messageListSource).toContain('class="research-report-preview plan-report-preview"');
    expect(messageListSource).toContain('.research-report-preview::after');
    expect(messageListSource).not.toContain("polyline points='6 9 12 15 18 9'");
    expect(messageListSource).not.toContain('.research-report-preview::before');
    expect(messageListSource).toContain('<FileTextOutlined />');
    expect(messageListSource).toContain('function copyPlanReport');
    expect(messageListSource).toContain('class="copy-report-action"');
    expect(chatSource).toContain('paintAssistantStreamText');
    expect(chatSource).toContain('ingestPlanReportText');
    expect(messageListSource).not.toContain("e: 'revisePlanStep'");
    expect(messageListSource).not.toContain('改这一步');
    expect(messageListSource).toMatch(/<ChoiceQuestionCard\s+v-else-if/);
    expect(collaborationSource).not.toContain('v{{ panel.planVersion }}');
    expect(collaborationSource).not.toContain('aicss-todo-ver');
    expect(collaborationSource).toContain('已偏离批准版本');
    expect(chatTabSource).toContain('PLAN_HINT_RE');
    expect(chatTabSource).toContain('acceptPlanHint');

    const restored = restoreExecutionTrace({
      plan_version: 4,
      approved_version: 3,
      diverged: true,
      task_plan: [
        { key: 'a', title: '调研', status: 'running', acceptance: '两份来源' },
      ],
    } as any);
    expect(restored.taskPlanVersion).toBe(4);
    expect(restored.taskPlanApprovedVersion).toBe(3);
    expect(restored.taskPlanDiverged).toBe(true);

    const target: any = {};
    applyTaskPlan(target, {
      plan_version: 1,
      approved_version: 1,
      steps: [{ key: 'a', title: '调研', status: 'pending' }],
    } as any);
    applyTaskPlan(target, {
      plan_version: 2,
      steps: [{ key: 'a', title: '调研竞品', status: 'pending' }],
      diverged: true,
    } as any);
    expect(target.taskPlanPrevious?.[0].title).toBe('调研');
    expect(target.taskPlanDiverged).toBe(true);
    applyTaskPlan(target, {
      plan_version: 3,
      approved_version: 3,
      steps: [{ key: 'a', title: '调研竞品', status: 'running' }],
    } as any);
    expect(target.taskPlanDiverged).toBeUndefined();

    const divergedKeep: any = {};
    applyTaskPlan(divergedKeep, {
      plan_version: 2,
      approved_version: 1,
      diverged: true,
      steps: [{ key: 'a', title: '调研', status: 'pending' }],
    } as any);
    expect(divergedKeep.taskPlanDiverged).toBe(true);
    applyTaskPlan(divergedKeep, {
      steps: [{ key: 'a', title: '调研', status: 'running' }],
    } as any);
    expect(divergedKeep.taskPlanDiverged).toBe(true);
    expect(divergedKeep.taskPlanVersion).toBe(2);

    const panel = deriveRunPanel([{
      role: 'assistant',
      taskPlan: target.taskPlan,
      taskPlanVersion: 5,
      taskPlanApprovedVersion: 3,
      taskPlanDiverged: true,
    } as any]);
    expect(panel.planVersion).toBe(5);
    expect(panel.approvedVersion).toBe(3);
    expect(panel.planDiverged).toBe(true);

    const panelStatusOnly = deriveRunPanel([{
      role: 'assistant',
      taskPlan: [{ key: 'a', title: '调研', status: 'running' }],
      taskPlanVersion: 5,
      taskPlanApprovedVersion: 3,
    } as any]);
    expect(panelStatusOnly.planDiverged).toBe(false);

    expect(timelineSource).not.toMatch(
      /Number\(planSource\?\.taskPlanVersion \|\| 0\) > Number\(planSource\.taskPlanApprovedVersion\)/,
    );
    expect(messageListSource).not.toContain('class="plan-review-steps"');
    expect(collaborationSource).toContain('验收：');
    expect(chatSource).toContain('!CANCEL_INTENT_RE.test(content)');
    expect(chatSource).toContain('isWaitingForUserStatus(liveActive?.status)');
    expect(chatSource).toContain('if (!accepted && !chatInput.value) chatInput.value = draft');
  });

  it('主 Agent 产品名称是 AXIOM Agent', () => {
    expect(execTeamSource).toContain("'AXIOM Agent'");
    expect(execTeamSource).not.toContain("'项目主管'");
    expect(timelineSource).toContain('缺省回退 AXIOM Agent');
    expect(apiSource).toContain('AXIOM Agent 在本次任务里的场景化身份');
  });

  it('执行团队成员恢复独立过程窗并绑定本次流式运行档', () => {
    expect(chatTabSource).toContain("emit(\n    'openSubagentChat'");
    expect(chatTabSource).not.toMatch(
      /function onOpenSubagentRun[\s\S]{0,360}emit\('selectSubagent'/,
    );
    expect(chatPageSource).toContain('<SubagentChatPanel');
    expect(chatPageSource).toContain(':delegation-run="findDelegationRun(sa.runKey)"');
    expect(centerSource).toContain('openSubagent(match ||');
    expect(subagentPanelSource).toContain('class="agent-run-page"');
    expect(subagentPanelSource).toContain('placeholder="输入你的问题..."');
    expect(subagentPanelSource).toContain('props.delegationRun?.output');
    expect(subagentPanelSource).toContain('delegationRun.reasoning');
    expect(subagentPanelSource).toContain('delegationRun?.nodes');
    expect(subagentPanelSource).toContain('showLiveDelegation');
    expect(subagentPanelSource).toContain('delegationRun?.files');
    expect(subagentPanelSource).toContain('<RunGeneratedFiles v-if="delegationFiles.length"');
    expect(subagentPanelSource).toContain('skipLiveDelegation.value = true');
    expect(subagentPanelSource).toContain('function onNewConversation()');
    expect(subagentPanelSource).toContain('class="run-brand"');
    expect(subagentPanelSource).toContain('class="run-brand-avatar"');
    expect(subagentPanelSource).toContain('aria-label="关闭子智能体对话"');
    expect(subagentPanelSource).toMatch(
      /class="sac-close-btn"[\s\S]*?@click\.stop="emit\('close'\)"/,
    );
    expect(subagentPanelSource).not.toContain('class="back-btn"');
    expect(subagentPanelSource).toContain('<AgentOutputDisclaimer v-if="messages.length || showLiveDelegation" />');
    expect(subagentPanelSource).not.toContain('class="run-header"');
    expect(subagentPanelSource).toContain('打开完整对话');
    expect(subagentPanelSource).toContain('openAgentRunWindow');
    expect(subagentPanelSource).not.toContain('送回主任务');
    expect(chatPageSource).not.toContain('onSendBack');
    expect(chatPageSource).not.toContain('@send-back');
  });

  it('运行中回车默认入队，点调整方向才注入，停键不因草稿消失', () => {
    expect(chatTabSource).toContain('调整方向');
    expect(chatTabSource).not.toContain('class="steer-task-bar"');
    expect(chatTabSource).not.toContain('onAdjustDirection');
    expect(chatTabSource).not.toContain("<span>引导</span>");
    expect(chatTabSource).toContain('instructQueueItem(item.id)');
    expect(chatTabSource).toContain('关闭排队');
    expect(chatTabSource).toContain('v-if="loading || stopping"');
    expect(chatTabSource).not.toContain('(loading || stopping) && !input.trim()');
    expect(chatTabSource).toContain('随心输入');
    expect(chatTabSource).toContain('(e.metaKey || e.ctrlKey) && e.shiftKey');
    expect(chatSource).toContain("=== 'steer' ? 'steer' : 'queue'");
    expect(chatSource).toContain('forceSteer?: boolean');
    expect(chatSource).toContain('const forceSteer = Boolean(opts?.forceSteer)');
    expect(chatSource).toContain('if (!forceSteer && active?.id && isGeneratingRunStatus(activeStatus) && followUpIntent ===');
    expect(chatSource).toContain("if (followUpIntent === 'queue')");
    expect(chatSource).toContain('expectedRunId: active.id');
    expect(chatSource).toContain('resume_source_run_id');
    expect(chatSource).toContain('lastUnfinishedResearchRunId');
    expect(chatSource).toContain('item.runCancelled || item.runFailed');
    expect(chatSource).not.toContain('item.runCancelled || item.runPartial || item.runFailed');
    expect(chatSource).not.toContain("showNotice('已插入当前任务')");
  });

  it('排队消息只通过左侧手柄拖拽，拖动项跟随指针并在松手后持久化顺序', () => {
    expect(chatTabSource).toContain('class="queue-drag-handle"');
    expect(chatTabSource).toContain('<draggable');
    expect(chatTabSource).toContain('handle=".queue-drag-handle"');
    expect(chatTabSource).toContain(':force-fallback="true"');
    expect(chatTabSource).toContain('ghost-class="queue-sort-ghost"');
    expect(chatTabSource).toContain('fallback-class="queue-sort-fallback"');
    expect(chatTabSource).toContain('@start="onQueueDragStart"');
    expect(chatTabSource).toContain('@end="onQueueDragEnd"');
    expect(chatTabSource).toContain(':animation="queueSortAnimationMs"');
    expect(chatTabSource).toContain('void reorderQueueItems(orderedIds)');
    expect(chatTabSource).toContain('@keydown.up.prevent="moveQueueItemByKeyboard(item.id, -1)"');
    expect(chatTabSource).toMatch(/\.queue-drag-handle\s*\{[\s\S]*?opacity:\s*1;/);
    expect(chatTabSource).not.toContain("window.addEventListener('pointermove'");
    expect(chatTabSource).not.toContain('onQueuePointerMove');
    const reorderGuard = chatTabSource.match(
      /const queueCanReorder = computed\([\s\S]*?\n\)\);/,
    )?.[0] || '';
    expect(reorderGuard).not.toContain('queuePaused');
    expect(reorderGuard).not.toContain('loading');
    expect(reorderGuard).not.toContain('canInstruct');
  });

  it('Deep Research 在 completed/partial 交付后退出，中断和历史回放不误改 Profile', () => {
    const settleBody = chatSource.match(
      /function settleResearchProfile[\s\S]*?\n  }\n\n  \/\*\*/,
    )?.[0] || '';
    expect(settleBody).toContain('researchProfileRuns.delete(runId)');
    expect(settleBody).toContain('if (!researchRunDelivered(outcome)) return');
    expect(settleBody).toContain('currentThreadId.value === threadId');
    expect(settleBody).toContain('researchProfile.value = false');
    expect(chatSource).toContain('onRunSettled(runThreadId, runId, terminalOutcome)');
    expect(chatSource).toContain("onRunSettled(threadId, active.id, 'cancelled')");
    expect(chatSource).toMatch(/agent_mode:\s*campusTurn\s*\?\s*'standard'\s*:\s*turnResearchProfile\s*\?\s*'research'/);
    expect(chatSource).toContain('const submittedResearchProfile = researchProfile.value || Boolean(unfinishedResearchRunId)');
    expect(chatSource).toContain('researchProfile: submittedResearchProfile');
    expect(chatSource).toContain(': (researchProfile.value || Boolean(lastUnfinishedResearchRunId()))');
    expect(chatSource).not.toContain(': (researchProfile.value || Boolean(resumeSourceRunId))');
    expect(chatSource).toContain('item.runCancelled || item.runFailed');
    expect(chatSource).not.toContain('item.runCancelled || item.runPartial || item.runFailed');
    expect(chatSource).toMatch(
      /function toggleResearchProfile\(\)[\s\S]*?researchProfile\.value = !researchProfile\.value/,
    );
  });

  it('发送时冻结 Profile，异步准备不得把已选 Plan 降成 standard', () => {
    const submitSnapshot = chatSource.indexOf('const submittedPlanMode = planMode.value');
    const asyncPreparation = chatSource.indexOf('await options.reloadApps()', submitSnapshot);
    const runTurn = chatSource.indexOf('const accepted = await runAssistantTurn(content', submitSnapshot);
    expect(submitSnapshot).toBeGreaterThan(0);
    expect(asyncPreparation).toBeGreaterThan(submitSnapshot);
    expect(runTurn).toBeGreaterThan(asyncPreparation);
    expect(chatSource).toContain('planMode: submittedPlanMode');
    expect(chatSource).toContain('researchProfile: submittedResearchProfile');
  });

  it('主对话隐藏连接器和工作区入口，但保留后台工作区能力', () => {
    expect(visibilitySource).toContain('connectors: false');
    expect(visibilitySource).toContain('workspace: false');
    expect(chatTabSource).toMatch(
      /<ConnectorMenu\b[^>]*\bv-if="MAIN_CHAT_FEATURE_VISIBILITY\.connectors(?:\s*&&[^\"]*)?"/,
    );
    expect(centerSource).toMatch(
      /<button\s+v-if="MAIN_CHAT_FEATURE_VISIBILITY\.workspace"[\s\S]{0,240}<span>工作区<\/span>/,
    );
    expect(centerSource).not.toMatch(
      /<button\s+v-if="MAIN_CHAT_FEATURE_VISIBILITY\.workspace"[\s\S]{0,240}<span>对话历史<\/span>/,
    );
    expect(centerSource).toContain('工作区');
    expect(centerSource).toContain('WorkspaceOverlay');
    expect(apiSource).toContain('/workspace/');
    expect(centerSource).not.toMatch(/navItems[\s\S]*工作区/);
    expect(chatTabSource).not.toContain('工作区');
    expect(overlaySource).toContain('搜索文件...');
    expect(overlaySource).not.toContain('查看全部文件');
    expect(overlaySource).toContain('class="ws-card"');
    expect(overlaySource).toContain('var(--center-nav-width');
    expect(overlaySource).not.toContain('body:has(.tox-center.sidebar-collapsed)');
    expect(overlaySource).not.toContain('在对话中继续');
    expect(overlaySource).not.toContain('CloseOutlined');
    expect(overlaySource).toContain('center-workspace-changed');
    expect(overlaySource).toContain('输入框附件也会进本会话工作区');
    expect(chatSource).toContain('uploadWorkspaceFile');
    expect(chatSource).toContain('mirrorAttachmentToWorkspace');
  });
});
