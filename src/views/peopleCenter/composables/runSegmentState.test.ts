import {
  createRunSegmentSession,
  routeArtifactEventToRun,
  routeToolEventToRun,
  segmentSplitOffset,
  isSealedAssistantSegment,
} from './runSegmentState';

describe('runSegmentState', () => {
  it('segmentSplitOffset：有段落边界吸附到边界后', () => {
    const full = '先给结论。\n\n第二段。\n\n| 表 |';
    expect(segmentSplitOffset(full, 0)).toBe(full.lastIndexOf('\n\n') + 2);
  });

  it('segmentSplitOffset：无段落边界整段定稿', () => {
    const full = '明白，只评价不动它。';
    expect(segmentSplitOffset(full, 0)).toBe(full.length);
  });

  it('isSealedAssistantSegment', () => {
    expect(isSealedAssistantSegment({ id: 1, role: 'assistant', content: '', executionSegmentEndedAt: 1 })).toBe(true);
    expect(isSealedAssistantSegment({ id: 1, role: 'assistant', content: '' })).toBe(false);
  });

  it('routeToolEventToRun：终态回落封存前段', () => {
    const sealed: any = {
      id: 1, role: 'assistant', runId: 'r1', content: '',
      executionSegmentEndedAt: 100,
      agentSteps: [{ kind: 'tool', name: 'bash', status: 'running' }],
    };
    const current: any = { id: 2, role: 'assistant', runId: 'r1', content: '', agentSteps: [] };
    let applied: any = null;
    const hit = routeToolEventToRun(
      [sealed, current], 'r1', 2,
      { name: 'bash', phase: 'completed' },
      (t) => { applied = t; t.agentSteps[0].status = 'completed'; },
    );
    expect(hit).toBe(true);
    expect(applied).toBe(sealed);
    expect(sealed.agentSteps[0].status).toBe('completed');
  });

  it('routeToolEventToRun：同名工具用 callId 选中正确封存段', () => {
    const first: any = {
      id: 1, role: 'assistant', runId: 'r1', content: '', executionSegmentEndedAt: 100,
      agentSteps: [{ kind: 'tool', name: 'read_file', callId: 'call-a', status: 'running' }],
    };
    const second: any = {
      id: 2, role: 'assistant', runId: 'r1', content: '', executionSegmentEndedAt: 200,
      agentSteps: [{ kind: 'tool', name: 'read_file', callId: 'call-b', status: 'running' }],
    };
    const current: any = { id: 3, role: 'assistant', runId: 'r1', content: '', agentSteps: [] };
    let applied: any = null;

    expect(routeToolEventToRun(
      [first, second, current], 'r1', 3,
      { name: 'read_file', callId: 'call-a', phase: 'completed' },
      (target) => { applied = target; },
    )).toBe(true);
    expect(applied).toBe(first);
  });

  it('routeArtifactEventToRun：旧观察者晚到也只保留续接后的一张产物卡', () => {
    const planMessage: any = {
      id: 1,
      role: 'assistant',
      runId: 'r1',
      content: '',
      generatedFiles: [{ id: 'file-1', filename: 'report.docx' }, { id: 'old', filename: 'old.docx' }],
      agentSteps: [
        { kind: 'artifact', files: [{ id: 'file-1' }] },
        { kind: 'artifact', files: [{ id: 'old' }] },
      ],
    };
    const executionMessage: any = {
      id: 2,
      role: 'assistant',
      runId: 'r1',
      content: '',
      generatedFiles: [],
      agentSteps: [],
    };
    let applied: any = null;

    expect(routeArtifactEventToRun(
      [planMessage, executionMessage],
      'r1',
      1, // 旧观察流仍然指向上方计划消息
      [{ id: 'file-1' }],
      (target) => {
        applied = target;
        target.generatedFiles = [{ id: 'file-1', filename: 'report.docx' }];
        target.agentSteps.push({ kind: 'artifact', files: [{ id: 'file-1' }] });
      },
    )).toBe(true);

    expect(applied).toBe(executionMessage);
    expect(planMessage.generatedFiles.map((file: any) => file.id)).toEqual(['old']);
    expect(planMessage.agentSteps).toHaveLength(1);
    expect(planMessage.agentSteps[0].files[0].id).toBe('old');
    expect(executionMessage.generatedFiles.map((file: any) => file.id)).toEqual(['file-1']);
    expect(executionMessage.agentSteps).toHaveLength(1);
  });

  it('createRunSegmentSession.split 无边界：前段定稿、offset 到全文末尾', () => {
    const messages: any[] = [{ id: 1, role: 'assistant', content: '', runId: 'r1' }];
    const session = createRunSegmentSession({
      runId: 'r1',
      initialAssistantId: 1,
      nextLocalId: () => 2,
      stripRecommendMark: (s) => s,
      createTypewriter: () => ({ push() {}, reset() {}, finish: async () => {}, stop() {} }),
      findMessage: (id) => messages.find((m) => m.id === id),
      patchMessage: (id, patch) => {
        const i = messages.findIndex((m) => m.id === id);
        if (i >= 0) messages[i] = { ...messages[i], ...patch };
      },
      pushMessage: (m) => { messages.push(m); },
    });
    session.onDelta('明白，只评价不动它。');
    const nextId = session.split();
    expect(nextId).toBe(2);
    expect(messages[0].content).toBe('明白，只评价不动它。');
    expect(messages[0].executionSegmentEndedAt).toBeTruthy();
    expect(session.contentOffset).toBe('明白，只评价不动它。'.length);
    expect(session.assistantId).toBe(2);
    expect(session.emptySegmentOk(messages[1])).toBe(true);
  });
});
