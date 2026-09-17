/**
 * 计划报告收进独立卡片（2026-07-28 用户拍板）。
 *
 * 此前计划轮的正文走 message.commentary，前端 applyCommentary 按「有没有工具动过手」
 * 二选一：没动过手 → 开场白气泡；动过手 → 时间线里的一个 note 行。而计划轮模型通常
 * 先 read_file/glob 勘查再写计划，于是整份计划（Context/指导原则/Phase/验收/待拍板）
 * 会被塞进一个 note 行里。
 *
 * 现在后端在计划轮显式打 kind='plan'，前端不再猜。
 */
import {
  applyCommentary,
  buildPlanCardMarkdown,
  ingestPlanReportText,
  looksLikePlanReport,
  restoreExecutionTrace,
  shouldHoldPlanStreamOffBody,
} from './executionTimeline';

function msg(): any {
  return { content: '', agentSteps: [] };
}

const PLAN = [
  '## 计划',
  '',
  '**Context**：把 md 转成 PPT，先核对现有材料再写步骤。',
  '',
  '**Phase A**：读文件并列出改动范围',
  '',
  '**验收**：生成的文件可打开。',
].join('\n');

describe('计划报告卡', () => {
  it("kind='plan' 落进 planReport，不碰开场白也不进时间线", () => {
    const m = msg();
    applyCommentary(m, PLAN, 'plan');
    expect(m.planReport).toBe(PLAN);
    expect(m.preamble).toBeUndefined();
    expect(m.agentSteps).toHaveLength(0);
  });

  it('模型先勘查再出计划时也不会掉进 note 行（这正是原来的病灶）', () => {
    const m = msg();
    m.agentSteps = [{ kind: 'tool', name: 'read_file' }];
    applyCommentary(m, PLAN, 'plan');
    expect(m.planReport).toBe(PLAN);
    expect(m.agentSteps).toHaveLength(1); // 没有新增 note
  });

  it('不带 kind 的普通过程说明行为完全不变', () => {
    const opening = msg();
    applyCommentary(opening, '我先看一下现有材料。');
    expect(opening.preamble).toBe('我先看一下现有材料。');
    expect(opening.planReport).toBeUndefined();

    const mid = msg();
    mid.agentSteps = [{ kind: 'tool', name: 'read_file' }];
    applyCommentary(mid, '接着写第二页。');
    expect(mid.planReport).toBeUndefined();
    expect(mid.agentSteps.filter((s: any) => s.kind === 'note')).toHaveLength(1);
  });

  it('空计划不产生空卡片', () => {
    const m = msg();
    applyCommentary(m, '   ', 'plan');
    expect(m.planReport).toBeUndefined();
  });

  it('v1.104 旧历史的带标签 note 刷新后恢复为蓝色计划卡', () => {
    const tagged = [
      '<proposed_plan>',
      '# 调研并生成 Word',
      '## Summary',
      '核对资料后生成可下载文档。',
      '## Key Changes',
      '- 整理规格与来源',
      '- 生成并检查 Word',
      '## Test Plan',
      '- 验证文件可打开且表格正常',
      '## Assumptions',
      '- 无',
      '</proposed_plan>',
    ].join('\n');
    const restored: any = restoreExecutionTrace({
      agent_mode: 'standard',
      steps: [{ kind: 'note', text: tagged }],
    });

    expect(restored.planReport).toContain('## Summary');
    expect(restored.planReport).not.toContain('<proposed_plan>');
    expect(restored.agentSteps || []).toHaveLength(0);
    expect(buildPlanCardMarkdown(restored)).toBe(restored.planReport);
  });

  it('权威 plan_report 即使 Run 续接后已是 standard 也仍渲染计划卡', () => {
    const restored: any = restoreExecutionTrace({
      agent_mode: 'standard',
      plan_report: PLAN,
      steps: [],
    });

    expect(buildPlanCardMarkdown(restored)).toBe(PLAN);
  });

  it('后到的短 intro 不得盖掉已经画出来的完整计划', () => {
    const m = msg();
    const longPlan = `${PLAN}\n\n${'按型号整理参数与价格，并写出可执行建议。'.repeat(8)}`;
    applyCommentary(m, longPlan, 'plan');
    applyCommentary(m, '你有几份现成材料，方向还需要对齐几个关键点：', 'plan');
    expect(m.planReport).toBe(longPlan);
  });

  it('未打标的完整计划会并进卡片，短过程句不会先占住计划卡', () => {
    const m = msg();
    m.agentMode = 'plan';
    m.agentSteps = [{ kind: 'tool', name: 'search_web' }];
    applyCommentary(m, '先核对现有材料。', 'plan');
    expect(m.planReport).toBeUndefined();
    const report = [
      '## 计划',
      '',
      '**Context**：把选购指南写成 Word，覆盖 15/16/17 系列。',
      '',
      '**Phase A**：整理参数与价格',
      '',
      '**验收**：三到五页可执行，含对比表。',
    ].join('\n');
    applyCommentary(m, report);
    expect(m.planReport).toContain('Phase A');
    expect(m.agentSteps.filter((s: any) => s.kind === 'note')).toHaveLength(1);
  });

  it('用户已同意执行后，新助手气泡不再把正文藏成计划卡', () => {
    const report = [
      '## 计划',
      '',
      '**Context**：把选购指南写成 Word，覆盖 15/16/17 系列。',
      '',
      '**Phase A**：整理参数与价格',
      '',
      '**验收**：三到五页可执行，含对比表。',
    ].join('\n');
    const m: any = {
      content: '',
      agentMode: 'plan',
      planExecutionUnlocked: true,
      taskPlan: [{ key: 's1', title: '调研', status: 'pending' }],
    };
    expect(shouldHoldPlanStreamOffBody(m)).toBe(false);
    expect(ingestPlanReportText(m, report)).toBe(false);
    expect(m.planReport).toBeUndefined();
    applyCommentary(m, report, 'plan');
    expect(m.planReport).toBeUndefined();
  });

  it('补充计划仍会把新报告收进卡片', () => {
    const report = [
      '## 计划',
      '',
      '**Context**：按补充意见收窄范围，只做 16 系列对比。',
      '',
      '**Phase A**：整理参数与价格',
      '',
      '**验收**：三到五页可执行，含对比表。',
    ].join('\n');
    const m: any = { content: '', agentMode: 'plan', taskPlan: [{ key: 's1', title: '调研', status: 'pending' }] };
    expect(shouldHoldPlanStreamOffBody(m)).toBe(true);
    expect(ingestPlanReportText(m, report)).toBe(true);
    expect(m.planReport).toContain('Phase A');
  });

  it('计划模式流式长文直接进卡片，并清掉重复正文', () => {
    const m: any = {
      content: 'Phase C: 文档生成与交付\n步骤 4 — 生成并交付 Word 文档',
      agentMode: 'plan',
      taskPlan: [{ key: 's1', title: '调研', status: 'pending' }],
    };
    const report = [
      'Phase C: 文档生成与交付',
      '',
      '步骤 4 — 生成并交付 Word 文档',
      '',
      '使用 python-docx 生成带标题、段落和表格的 .docx，保存到「我的文件」。',
      '',
      '验收标准',
      '- 文件可打开',
      '- 覆盖 6 个维度',
      '',
      '待你拍板',
      '无 —— 需求明确，可直接开始执行',
    ].join('\n');
    expect(looksLikePlanReport(report)).toBe(true);
    expect(ingestPlanReportText(m, report)).toBe(true);
    expect(m.planReport).toContain('待你拍板');
    expect(m.content).toBe('');
  });

  it('规划中 update_plan 已开始，报告仍收进卡片', () => {
    const m: any = {
      content: '',
      agentMode: 'plan',
      taskPlan: [{ key: 's1', title: '调研', status: 'running' }],
    };
    expect(shouldHoldPlanStreamOffBody(m)).toBe(true);
    expect(ingestPlanReportText(m, PLAN)).toBe(true);
    expect(m.planReport).toContain('Phase A');
  });

  it('自然 Markdown 标题也算计划报告', () => {
    const report = [
      '## Agent 开发笔记',
      '',
      '给同事一份入门笔记，最后输出 Word。',
      '',
      '1. 先定读者和篇幅',
      '2. 列出核心概念与最小示例',
      '3. 用 python-docx 生成文档并保存到我的文件',
    ].join('\n');
    expect(looksLikePlanReport(report)).toBe(true);
  });

  it("kind='plan' 的长文即使没有 Context 标签也进卡片", () => {
    const m = msg();
    const text = [
      '先把读者定成刚入门的同事，篇幅控制在三千字左右，语气保持白话，不要写成规范文档。',
      '内容覆盖智能体循环、工具调用、失败处理和交付检查，最后用 Word 文档交出去。',
    ].join('\n\n');
    expect(text.length).toBeGreaterThanOrEqual(80);
    expect(looksLikePlanReport(text)).toBe(false);
    applyCommentary(m, text, 'plan');
    expect(m.planReport).toBe(text);
  });

  it('确认卡没有报告时用步骤合成预览，不含验收', () => {
    const md = buildPlanCardMarkdown({
      agentMode: 'plan',
      interactive: { kind: 'plan_confirmation', plan_steps: [] },
      taskPlan: [
        { title: '写大纲', detail: '先定读者和篇幅', acceptance: '不得出现在卡片' } as any,
        { title: '生成 Word' },
      ],
      taskGoalContract: { goal: 'Agent 开发笔记' },
    });
    expect(md).toContain('## Agent 开发笔记');
    expect(md).toContain('写大纲');
    expect(md).toContain('先定读者和篇幅');
    expect(md).toContain('生成 Word');
    expect(md).not.toContain('不得出现在卡片');
  });

  it('标准模式的种子任务计划不得合成计划白卡', () => {
    expect(buildPlanCardMarkdown({
      taskPlan: [
        { title: '完成「你查看看kimi」' },
        { title: '整理结果并回复' },
      ],
      taskGoalContract: { goal: '你查看看kimi' },
    })).toBe('');
    expect(buildPlanCardMarkdown({
      agentMode: 'standard',
      taskPlan: [
        { title: '完成「你查看看kimi」' },
        { title: '整理结果并回复' },
      ],
      taskGoalContract: { goal: '你查看看kimi' },
    })).toBe('');
  });

  it('深度研究模式也不用种子任务计划冒充计划白卡', () => {
    expect(buildPlanCardMarkdown({
      agentMode: 'research',
      taskPlan: [
        { title: '完成「你查看看kimi」' },
        { title: '整理结果并回复' },
      ],
      taskGoalContract: { goal: '你查看看kimi' },
    })).toBe('');
  });

  it('计划模式规划中只有步骤、还没有确认卡时不合成白卡', () => {
    expect(buildPlanCardMarkdown({
      agentMode: 'plan',
      taskPlan: [
        { title: '完成「你查看看kimi」' },
        { title: '整理结果并回复' },
      ],
      taskGoalContract: { goal: '你查看看kimi' },
    })).toBe('');
  });

  it('已经同意执行后不用步骤合成计划卡', () => {
    expect(buildPlanCardMarkdown({
      planExecutionUnlocked: true,
      taskPlan: [{ title: '写 Word' }],
    })).toBe('');
  });

  it('已经开始执行后不再把交付正文吞进计划卡', () => {
    const m: any = {
      content: '',
      agentMode: 'plan',
      planExecutionUnlocked: true,
      planReport: '## 计划\n\n**Phase A**：调研',
      taskPlan: [{ key: 's1', title: '调研', status: 'running' }],
    };
    const deliverable = [
      '## 交付说明',
      '',
      '**Phase C**：文档已生成。',
      '',
      '步骤 4 已完成，Word 可在「我的文件」下载。',
      '',
      '验收标准均已满足。',
    ].join('\n');
    expect(ingestPlanReportText(m, deliverable)).toBe(false);
    expect(m.content).toBe('');
  });

  it('搜不到资料的过程独白不会变成计划卡', () => {
    const dump = [
      '当前搜索服务不可用，没法拉取最新评测。',
      '我仍可根据训练知识对比 Codex、Claude Agent、DeepSeek Coder、MarsCode/豆包、CodeGeeX、通义灵码。',
      '写正式文档前会再尝试搜索。',
      '现在拆解任务步骤：',
    ].join('\n\n');
    expect(looksLikePlanReport(dump)).toBe(false);
    const m: any = { content: '', agentMode: 'plan', agentSteps: [] };
    expect(ingestPlanReportText(m, dump)).toBe(false);
    expect(m.planReport).toBeUndefined();
  });
});

describe('计划卡整卡出场，不把字打进去', () => {
  const listSrc = require('fs').readFileSync(
    require('path').join(__dirname, '../components/MessageList.vue'), 'utf8');

  it('生成中不出骨架条，整份计划齐了才用研究报告白卡跳入', () => {
    expect(listSrc).toContain('function isPlanReportPending');
    expect(listSrc).toContain('function hasReadyPlanDocument');
    expect(listSrc).toContain("String(message.agentMode || '') !== 'plan'");
    const readyGate = listSrc.match(
      /function hasReadyPlanDocument\(message: ChatMessage\): boolean \{([\s\S]*?)\n\}/,
    )?.[1] || '';
    expect(readyGate).toContain('return Boolean(planCardMarkdown(message))');
    expect(readyGate).not.toContain("String(message.agentMode || '') !== 'plan'");
    expect(listSrc).not.toContain('v-if="isPlanReportPending(message)"');
    expect(listSrc).toContain('v-if="hasReadyPlanDocument(message)"');
    expect(listSrc).toContain('v-html="planCardBodyHtml(message)"');
    expect(listSrc).toContain('stripLeadingTitleHeadings');
    expect(listSrc).not.toContain('renderMarkdown(message.planReport)');
    expect(listSrc).toContain('class="research-report-card"');
    expect(listSrc).toContain('class="research-report-preview plan-report-preview"');
    expect(listSrc).toContain('.research-report-preview::after');
    expect(listSrc).not.toContain("polyline points='6 9 12 15 18 9'");
    expect(listSrc).not.toContain('.research-report-preview::before');
    expect(listSrc).not.toContain('.research-report-preview.plan-report-preview::before');
    expect(listSrc).toContain('.plan-report-fs-paper .plan-report-body :deep(> h1:first-child)');
    expect(listSrc).toContain('class="plan-review-card"');
    expect(listSrc).not.toContain('class="plan-review-steps"');
    expect(listSrc).toContain('开始执行');
    expect(listSrc).toContain('好，请执行此计划。');
    expect(listSrc).toContain('__PLAN_SKIP__');
    expect(listSrc).not.toContain('跳过，按当前计划执行。');
    expect(listSrc).not.toContain('开始执行计划');
    expect(listSrc).not.toContain('进行补充');
    expect(listSrc).toContain('需要调整计划，直接在这里补充');
    expect(listSrc).toContain('animation: plan-card-in 0.55s');
    expect(listSrc).toContain('@keyframes plan-card-in');
    expect(listSrc).toContain('background: #3b82f6');
    expect(listSrc).toContain('box-shadow: 0 10px 28px rgba(15, 23, 42, 0.06)');
    expect(listSrc).toContain('function copyPlanReport');
    expect(listSrc).toContain('aria-label="复制"');
  });
});


/**
 * 接线断言（2026-07-28 真机踩到）：后端打了 kind、agentApi 也解析并回调了，
 * 但 useCenterChat 里三个 onCommentary 处理器的签名只收 (text, strippedContent)，
 * 第三个参数被静默丢掉 —— 单测全绿、类型也不报错，页面上就是没有卡片。
 * 这类「发出去了但没人接」只有端到端断言才拦得住。
 */
describe('kind 必须一路传到 applyCommentary', () => {
  const src = require('fs').readFileSync(
    require('path').join(__dirname, 'useCenterChat.ts'), 'utf8');
  const processStreamsSource = require('fs').readFileSync(
    require('path').join(__dirname, 'harnessProcessStreams.ts'), 'utf8');

  it('三个 onCommentary 处理器都收下 kind', () => {
    const withKind = src.match(/onCommentary: \(text, strippedContent, kind\) =>/g) || [];
    const withoutKind = src.match(/onCommentary: \(text, strippedContent\) =>/g) || [];
    expect(withoutKind).toHaveLength(0);
    expect(withKind.length).toBeGreaterThanOrEqual(3);
  });

  it('发送、续接和回放经共享流式适配器把 kind 传到 reducer', () => {
    const bad = processStreamsSource.match(/applyCommentary\(target, (?:text|clean)\)/g) || [];
    const good = processStreamsSource.match(/applyCommentary\(target, (?:text|clean), kind\)/g) || [];
    expect(bad).toHaveLength(0);
    expect(good.length).toBeGreaterThanOrEqual(1);
    expect(src).toContain("from './harnessProcessStreams'");
    expect(src).toContain('commentaryStream.show(text, kind)');
    const streamPaint = src.match(/paintAssistantStreamText\(/g) || [];
    expect(streamPaint.length).toBeGreaterThanOrEqual(6);
    const commentaryRoute = src.match(
      /return applyCommentaryAndHidePlanBody\(\s*(?:updateAssistant|updateContinuation|updateTarget),\s*typewriter,\s*commentaryStream,\s*text,\s*strippedContent,\s*kind,/g,
    ) || [];
    expect(commentaryRoute).toHaveLength(3);
    expect(src).toContain('ingestPlanReportText');
  });
});
