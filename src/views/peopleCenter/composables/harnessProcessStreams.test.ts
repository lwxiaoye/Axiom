/** @jest-environment jsdom */

import { createCommentaryStream, createReasoningStream } from './harnessProcessStreams';
import { type ExecutionMessage } from './executionTimeline';

const streams: Array<{ stop: () => void }> = [];

beforeEach(() => {
  jest.useFakeTimers();
  jest.spyOn(document, 'hidden', 'get').mockReturnValue(false);
  // SSE 可以继续收到消息，但浏览器完全不提供绘制帧。
  jest.spyOn(window, 'requestAnimationFrame').mockReturnValue(1);
  jest.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
});

afterEach(() => {
  streams.splice(0).forEach((stream) => stream.stop());
  jest.clearAllTimers();
  jest.useRealTimers();
  jest.restoreAllMocks();
});

function setup() {
  const message: ExecutionMessage = { content: '' };
  const update = (fn: (target: ExecutionMessage) => void) => fn(message);
  const reasoning = createReasoningStream(update);
  const commentary = createCommentaryStream(update);
  streams.push(reasoning, commentary);
  return { message, reasoning, commentary };
}

test('长思考已收到时立即消费正文和终态，不等待任何绘制帧或计时器', async () => {
  const { message, reasoning } = setup();
  const text = '核对已经收到的证据。'.repeat(220);
  const events: string[] = [];

  reasoning.push(text, text);
  expect(message.agentSteps?.[0]).toMatchObject({ kind: 'thinking', text, status: 'running' });
  await reasoning.complete({ text, seconds: 9 });
  message.content = '这是已经返回的答案。';
  events.push('message.delta', 'run.completed');

  expect(events).toEqual(['message.delta', 'run.completed']);
  expect(message.agentSteps?.[0]).toMatchObject({ text, status: 'completed', seconds: 9 });
  expect(window.requestAnimationFrame).not.toHaveBeenCalled();
  expect(jest.getTimerCount()).toBe(0);
});

test('只有完成快照也保留完整思考，重复完成不增加步骤', async () => {
  const { message, reasoning } = setup();
  const text = '完成事件带来的完整记录。'.repeat(180);
  await reasoning.complete({ text, seconds: 8 });
  await reasoning.complete({ text, seconds: 8 });
  await reasoning.complete();

  expect(message.agentSteps).toHaveLength(1);
  expect(message.agentSteps?.[0]).toMatchObject({ text, status: 'completed', seconds: 8 });
});

test('分片去重、下一轮思考与停止观察不串段', async () => {
  const { message, reasoning } = setup();
  reasoning.push('第一', '第一');
  reasoning.push('段', '第一段');
  reasoning.push('段', '第一段');
  await reasoning.complete();
  reasoning.push('第二段', '第二段');
  await reasoning.complete();
  reasoning.stop();
  reasoning.push('不应写入', '不应写入');
  await reasoning.complete({ text: '不应写入' });

  expect(message.agentSteps?.map((step) => step.kind === 'thinking' ? step.text : '')).toEqual(['第一段', '第二段']);
});

test('公开首句的长动画不阻塞答案，停止时补齐已收到的全文且不再回写', async () => {
  const { message, commentary } = setup();
  const text = '我会根据已有材料核对这个问题。'.repeat(60);
  await commentary.show(text);
  message.content = '答案已经返回。';

  expect(message.content).toBe('答案已经返回。');
  expect(message.preamble).not.toBe(text);
  commentary.stop();
  expect(message.preamble).toBe(text);
  expect(message.preambleStreamKey).toBeUndefined();
  await jest.advanceTimersByTimeAsync(30000);
  expect(message.preamble).toBe(text);
  expect(jest.getTimerCount()).toBe(0);
});

test('相同 commentary 到达时按权威全文去重，不按未播完的文字重复插入', async () => {
  const { message, commentary } = setup();
  const text = '我先查阅已有材料，然后核对具体要求。';
  await commentary.show(text);
  await commentary.show(text);
  commentary.stop();

  expect(message.preamble).toBe(text);
  expect(message.agentSteps?.filter((step) => step.kind === 'note') || []).toHaveLength(0);
});

test('计划报告和系统状态仍沿用 reducer 语义，不进入公开叙述动画', async () => {
  const { message, commentary } = setup();
  await commentary.show('正在分析需求...', 'initial_progress');
  expect(window.requestAnimationFrame).not.toHaveBeenCalled();
  message.agentMode = 'plan';
  const plan = '## 计划\n\n1. 查阅资料，逐项核对学校已发布的办理要求、时间安排、地点和必备材料。\n2. 编写报告，区分已确认信息与仍需核实的事项，并标注来源。\n\n验收标准：证据充分，学生能够按步骤办理，不虚构材料中没有的事实。';
  await commentary.show(plan, 'plan');
  expect(message.planReport).toBe(plan);
  expect(window.requestAnimationFrame).not.toHaveBeenCalled();
});
