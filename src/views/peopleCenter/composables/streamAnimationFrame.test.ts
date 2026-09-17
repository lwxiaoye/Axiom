/** @jest-environment jsdom */

import {
  cancelStreamAnimationFrame,
  requestStreamAnimationFrame,
  STREAM_FRAME_FALLBACK_MS,
  waitForStreamPaint,
} from './streamAnimationFrame';

let nextFrame = 0;
const frames = new Map<number, FrameRequestCallback>();

beforeEach(() => {
  jest.useFakeTimers();
  nextFrame = 0;
  frames.clear();
  jest.spyOn(document, 'hidden', 'get').mockReturnValue(false);
  jest.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    frames.set(++nextFrame, callback);
    return nextFrame;
  });
  jest.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => { frames.delete(id); });
});

afterEach(async () => {
  await jest.runAllTimersAsync();
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test('正常动画帧优先，计时兜底不会重复提交', async () => {
  const painted = jest.fn();
  requestStreamAnimationFrame(painted);
  const callback = [...frames.values()][0];

  callback(16);
  await jest.advanceTimersByTimeAsync(STREAM_FRAME_FALLBACK_MS * 2);

  expect(painted).toHaveBeenCalledTimes(1);
  expect(painted).toHaveBeenCalledWith(16);
  expect(frames.size).toBe(0);
  expect(jest.getTimerCount()).toBe(0);
});

test('动画帧未回调时按时兜底，迟到的原帧也不会再提交', async () => {
  const painted = jest.fn();
  requestStreamAnimationFrame(painted);
  const lateFrame = [...frames.values()][0];

  await jest.advanceTimersByTimeAsync(STREAM_FRAME_FALLBACK_MS);
  lateFrame(500);

  expect(painted).toHaveBeenCalledTimes(1);
  expect(painted).toHaveBeenCalledWith(STREAM_FRAME_FALLBACK_MS);
  expect(frames.size).toBe(0);
  expect(jest.getTimerCount()).toBe(0);
});

test('取消后同时清理动画帧、计时器与可见性回调', async () => {
  const painted = jest.fn();
  const id = requestStreamAnimationFrame(painted);
  const lateFrame = [...frames.values()][0];
  cancelStreamAnimationFrame(id);
  jest.spyOn(document, 'hidden', 'get').mockReturnValue(true);

  document.dispatchEvent(new Event('visibilitychange'));
  await jest.advanceTimersByTimeAsync(STREAM_FRAME_FALLBACK_MS * 2);
  lateFrame(500);

  expect(painted).not.toHaveBeenCalled();
  expect(frames.size).toBe(0);
  expect(jest.getTimerCount()).toBe(0);
});

test('同一帧未绘制就切后台时直接放行，不等后台计时器', () => {
  const painted = jest.fn();
  requestStreamAnimationFrame(painted);
  jest.spyOn(document, 'hidden', 'get').mockReturnValue(true);

  document.dispatchEvent(new Event('visibilitychange'));

  expect(painted).toHaveBeenCalledTimes(1);
  expect(jest.getTimerCount()).toBe(0);
});

test('思考收尾的双帧屏障在没有动画帧时仍有明确出口', async () => {
  let done = false;
  const waiting = waitForStreamPaint().then(() => { done = true; });

  await jest.advanceTimersByTimeAsync(STREAM_FRAME_FALLBACK_MS - 1);
  expect(done).toBe(false);
  await jest.advanceTimersByTimeAsync(STREAM_FRAME_FALLBACK_MS + 1);

  expect(done).toBe(true);
  expect(frames.size).toBe(0);
  expect(jest.getTimerCount()).toBe(0);
  await waiting;
});

test('屏障等待过程中切后台时立即完成，不留第二个悬空帧', async () => {
  let done = false;
  const waiting = waitForStreamPaint().then(() => { done = true; });
  jest.spyOn(document, 'hidden', 'get').mockReturnValue(true);

  document.dispatchEvent(new Event('visibilitychange'));
  await waiting;

  expect(done).toBe(true);
  expect(frames.size).toBe(0);
  expect(jest.getTimerCount()).toBe(0);
});
