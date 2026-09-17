import {
  GENERATION_METER_LABELS as L,
  generationMeterStatus,
  interviewGenerationMeterStatus,
  type GenerationMeterContext,
  type GenerationMeterMessage,
} from './generationMeterStatus';

const t0 = 1_000_000;

function ctx(partial: Partial<GenerationMeterContext> = {}): GenerationMeterContext {
  return {
    now: t0,
    hasAssistantBody: false,
    isPlanConfirmation: false,
    hasGeneratedFiles: false,
    ...partial,
  };
}

function msg(partial: Partial<GenerationMeterMessage> = {}): GenerationMeterMessage {
  return { runStartedAt: t0, agentSteps: [], ...partial };
}

describe('generationMeterStatus', () => {
  it('已受理但未启动的任务不能按经过时间冒充正在分析', () => {
    expect(generationMeterStatus(msg({ runStatus: 'created' }), ctx({ now: t0 + 120_000 }))).toBe(L.queued);
    expect(generationMeterStatus(msg({ runStatus: 'running' }), ctx({ now: t0 + 2500 }))).toBe(L.analyze);
  });

  it('等待自动恢复时保留真实等待文案', () => {
    expect(generationMeterStatus(msg({ runStatus: 'waiting_system' }), ctx({ now: t0 + 120_000 }))).toBe(L.recovering);
  });

  it('任务开始阶段按耗时从理解走到分析', () => {
    expect(generationMeterStatus(msg(), ctx())).toBe(L.understand);
    expect(generationMeterStatus(msg(), ctx({ now: t0 + 2500 }))).toBe(L.analyze);
  });

  it('出现思考信号后进入分析需求', () => {
    expect(
      generationMeterStatus(
        msg({ reasoningSummary: '先看目标' }),
        ctx({ now: t0 + 800 }),
      ),
    ).toBe(L.analyze);
  });

  it('规划轮在分析之后切到规划执行方案', () => {
    const plan = msg({ agentMode: 'plan', reasoningSummary: '拆步骤' });
    expect(generationMeterStatus(plan, ctx({ now: t0 + 800 }))).toBe(L.analyze);
    expect(generationMeterStatus(plan, ctx({ now: t0 + 3200 }))).toBe(L.plan);
  });

  it('开始阶段思考变长后切到规划下一步', () => {
    expect(
      generationMeterStatus(
        msg({ reasoningSummary: '先拆下一步' }),
        ctx({ now: t0 + 4500 }),
      ),
    ).toBe(L.next);
  });

  it('搜索 / 深读 / 写文件 / 其它工具分别映射到执行阶段文案', () => {
    expect(
      generationMeterStatus(
        msg({
          agentSteps: [{ kind: 'tool', name: 'search_web', label: '搜索', status: 'running' }],
        }),
        ctx(),
      ),
    ).toBe(L.search);
    expect(
      generationMeterStatus(
        msg({
          agentSteps: [{ kind: 'tool', name: 'deep_read', label: '深读', status: 'running' }],
        }),
        ctx(),
      ),
    ).toBe(L.organize);
    expect(
      generationMeterStatus(
        msg({
          agentSteps: [{ kind: 'tool', name: 'write_file', label: '写入', status: 'running' }],
        }),
        ctx(),
      ),
    ).toBe(L.file);
    expect(
      generationMeterStatus(
        msg({
          agentSteps: [{ kind: 'tool', name: 'use_skill', label: '技能', status: 'running' }],
        }),
        ctx(),
      ),
    ).toBe(L.tool);
  });

  it('工具间隙和思考中显示规划下一步，只有真正在跑的工具才说调用工具', () => {
    expect(
      generationMeterStatus(
        msg({
          agentSteps: [
            { kind: 'tool', name: 'search_web', label: '搜索', status: 'completed' },
            { kind: 'thinking', text: '下一步读哪篇', status: 'running' },
          ],
        }),
        ctx(),
      ),
    ).toBe(L.next);
    expect(
      generationMeterStatus(
        msg({
          agentSteps: [{ kind: 'tool', name: 'use_skill', label: '技能', status: 'completed' }],
        }),
        ctx(),
      ),
    ).toBe(L.next);
    expect(
      generationMeterStatus(
        msg({
          agentSteps: [{ kind: 'tool', name: 'use_skill', label: '技能', status: 'running' }],
        }),
        ctx(),
      ),
    ).toBe(L.tool);
  });

  it('正文或产物到达后显示正在生成内容', () => {
    expect(generationMeterStatus(msg(), ctx({ hasAssistantBody: true }))).toBe(L.write);
    expect(generationMeterStatus(msg({ artifactPages: [{ index: 1 }] }), ctx())).toBe(L.write);
    expect(generationMeterStatus(msg(), ctx({ hasGeneratedFiles: true }))).toBe(L.write);
  });

  it('研究阶段机映射到同一组文案', () => {
    expect(
      generationMeterStatus(msg({ researchProgress: { stage: 'planning' } }), ctx()),
    ).toBe(L.plan);
    expect(
      generationMeterStatus(msg({ researchProgress: { stage: 'researching' } }), ctx()),
    ).toBe(L.next);
    expect(
      generationMeterStatus(msg({ researchProgress: { stage: 'verifying' } }), ctx()),
    ).toBe(L.organize);
    expect(
      generationMeterStatus(msg({ researchProgress: { stage: 'reviewing' } }), ctx()),
    ).toBe(L.organize);
    expect(
      generationMeterStatus(msg({ researchProgress: { stage: 'synthesizing' } }), ctx()),
    ).toBe(L.write);
  });

  it('面试助手把通用计量文案换成面试相关说法，主对话保持原句', () => {
    expect(generationMeterStatus(msg(), ctx({ now: t0 + 2500 }))).toBe(L.analyze);
    expect(interviewGenerationMeterStatus(msg(), ctx({ now: t0 + 2500 }))).toBe('正在对照岗位要求...');
    expect(interviewGenerationMeterStatus(msg(), ctx())).toBe('正在了解你的经历...');
  });
});
