/** @jest-environment jsdom */

import { createSmoothStreamText, type SmoothStreamTextHandle } from './smoothStreamText';

let frameId = 0;
const streams: SmoothStreamTextHandle[] = [];

beforeEach(() => {
  jest.useFakeTimers();
  frameId = 0;
  jest.spyOn(document, 'hidden', 'get').mockReturnValue(false);
  // Mobile WebViews can keep JS timers alive while withholding paint callbacks.
  jest.spyOn(window, 'requestAnimationFrame').mockImplementation(() => ++frameId);
  jest.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
});

afterEach(() => {
  streams.splice(0).forEach((stream) => stream.stop());
  jest.clearAllTimers();
  jest.useRealTimers();
  jest.restoreAllMocks();
});

function makeStream(commit: (content: string) => void) {
  const stream = createSmoothStreamText({ commit });
  streams.push(stream);
  return stream;
}

test('动画帧被暂停时，已收到的开场说明仍会出现首字', async () => {
  const commits: string[] = [];
  const stream = makeStream((content) => commits.push(content));
  void stream.finish('我先看一下你上传的图片。');

  await jest.advanceTimersByTimeAsync(250);

  expect(commits.length).toBeGreaterThan(0);
  expect(commits[0].length).toBeGreaterThan(0);
});

test('丢失动画帧不能一直堵住 commentary 后的回答与真实完成事件', async () => {
  const commits: string[] = [];
  const stream = makeStream((content) => commits.push(content));
  const events: string[] = [];
  const consuming = (async () => {
    await stream.finish('先读取图片，再回答你的问题。');
    events.push('commentary');
    events.push('answer');
    events.push('run.completed');
  })();

  await jest.advanceTimersByTimeAsync(4000);

  expect(events).toEqual(['commentary', 'answer', 'run.completed']);
  expect(commits.at(-1)).toBe('先读取图片，再回答你的问题。');
  await consuming;
});

test('已有等待中的帧时，切到后台的新分片也必须同步权威全文', () => {
  const commits: string[] = [];
  const stream = makeStream((content) => commits.push(content));
  stream.push('第一段');
  jest.spyOn(document, 'hidden', 'get').mockReturnValue(true);

  stream.push('第一段和后续内容');

  expect(commits.at(-1)).toBe('第一段和后续内容');
});

test('播放过程中切到后台，没有新 token 时也不能挂住 finish', async () => {
  const commits: string[] = [];
  const stream = makeStream((content) => commits.push(content));
  let complete = false;
  const finishing = stream.finish('后台应立即同步的完整说明').then(() => { complete = true; });
  jest.spyOn(document, 'hidden', 'get').mockReturnValue(true);

  document.dispatchEvent(new Event('visibilitychange'));
  await jest.advanceTimersByTimeAsync(150);

  expect(complete).toBe(true);
  expect(commits.at(-1)).toBe('后台应立即同步的完整说明');
  await finishing;
});

test('停止或切会话后兜底回调不再写回旧消息', async () => {
  const commits: string[] = [];
  const stream = makeStream((content) => commits.push(content));
  const finishing = stream.finish('不应再写回已切走消息的说明');

  stream.stop();
  await jest.advanceTimersByTimeAsync(4000);
  await finishing;

  expect(commits).toEqual([]);
  expect(jest.getTimerCount()).toBe(0);
});

test('研究报告在切回前台后全文到达，动画帧停顿也立即提交并释放收尾', async () => {
  let research = false;
  let content = '';
  const stream = createSmoothStreamText({
    commit: (value) => { content = value; },
    shouldRenderImmediately: () => research,
  });
  streams.push(stream);
  // Profile may arrive in run.started after the painter has been created.
  research = true;
  jest.spyOn(document, 'hidden', 'get').mockReturnValue(true);
  document.dispatchEvent(new Event('visibilitychange'));
  jest.spyOn(document, 'hidden', 'get').mockReturnValue(false);
  document.dispatchEvent(new Event('visibilitychange'));
  const report = '# Agent Loop 研究报告\n\n## 执行摘要\n' + '有来源的研究材料。'.repeat(1000);
  stream.push(report);
  stream.finalize(report);
  expect(content).toBe(report);
  let settled = false;
  await stream.finish(report).then(() => { settled = true; });
  expect(settled).toBe(true);
  expect(jest.getTimerCount()).toBe(0);
  await jest.advanceTimersByTimeAsync(5000);
  expect(content).toBe(report);
});
