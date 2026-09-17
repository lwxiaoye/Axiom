import {
  createSmoothStreamText,
  SMOOTH_STREAM_FRAME_MS,
  SMOOTH_STREAM_MAX_STEP,
  type SmoothStreamScheduler,
} from './smoothStreamText';

class FakeScheduler implements SmoothStreamScheduler {
  private clock = 0;
  private sequence = 0;
  private callbacks = new Map<number, FrameRequestCallback>();

  immediate = false;

  requestFrame = (callback: FrameRequestCallback) => {
    this.sequence += 1;
    this.callbacks.set(this.sequence, callback);
    return this.sequence;
  };

  cancelFrame = (frame: number) => {
    this.callbacks.delete(frame);
  };

  now = () => this.clock;

  shouldRenderImmediately = () => this.immediate;

  tick(ms = SMOOTH_STREAM_FRAME_MS) {
    this.clock += ms;
    const callbacks = Array.from(this.callbacks.values());
    this.callbacks.clear();
    callbacks.forEach((callback) => callback(this.clock));
  }

  get pendingFrames() {
    return this.callbacks.size;
  }
}

test('按 60fps 小步追赶累计全文，而不是按网络分片整块跳出', () => {
  const scheduler = new FakeScheduler();
  const commits: string[] = [];
  const stream = createSmoothStreamText({ scheduler, commit: (content) => commits.push(content) });
  const target = '这是一段用于验证主智能体与子智能体统一流式节奏的较长文本。'.repeat(5);

  stream.push(target);
  scheduler.tick();
  expect(commits[0].length).toBeGreaterThan(0);
  expect(commits[0].length).toBeLessThan(target.length);
  for (let i = 0; i < 200 && scheduler.pendingFrames; i += 1) scheduler.tick();

  expect(commits[commits.length - 1]).toBe(target);
  const increments = commits.map((value, index) => value.length - (commits[index - 1]?.length || 0));
  expect(Math.max(...increments)).toBeLessThanOrEqual(SMOOTH_STREAM_MAX_STEP);
});

test('终态尾段按有界视觉帧排空，finish 等到权威全文真正落屏', async () => {
  const scheduler = new FakeScheduler();
  const commits: string[] = [];
  const stream = createSmoothStreamText({ scheduler, commit: (content) => commits.push(content) });
  const target = '终态输出'.repeat(180);
  const finished = stream.finish(target);

  for (let i = 0; i < 600 && scheduler.pendingFrames; i += 1) scheduler.tick();
  await finished;

  expect(commits.length).toBeGreaterThan(2);
  expect(commits[commits.length - 1]).toBe(target);
  const increments = commits.map((value, index) => value.length - (commits[index - 1]?.length || 0));
  expect(Math.max(...increments)).toBeLessThanOrEqual(SMOOTH_STREAM_MAX_STEP);
});

test('超长终态积压超过目标排空时间后也不会整块喷出', async () => {
  const scheduler = new FakeScheduler();
  const commits: string[] = [];
  const stream = createSmoothStreamText({
    scheduler,
    finishMinMs: 700,
    finishMaxMs: 700,
    commit: (content) => commits.push(content),
  });
  const target = '这是需要保持节奏的超长终态正文。'.repeat(160);
  const finished = stream.finish(target);

  for (let i = 0; i < 2000 && scheduler.pendingFrames; i += 1) scheduler.tick();
  await finished;

  const increments = commits.map((value, index) => value.length - (commits[index - 1]?.length || 0));
  expect(commits[commits.length - 1]).toBe(target);
  expect(Math.max(...increments)).toBeLessThanOrEqual(SMOOTH_STREAM_MAX_STEP);
  expect(commits.length).toBeGreaterThan(100);
});

test('主线程停顿后不会积累字符额度连续满速追赶', () => {
  const scheduler = new FakeScheduler();
  const commits: string[] = [];
  const stream = createSmoothStreamText({ scheduler, commit: (content) => commits.push(content) });

  stream.finalize('停顿恢复后仍应保持稳定节奏。'.repeat(40));
  scheduler.tick(800);
  scheduler.tick();

  const increments = commits.map((value, index) => value.length - (commits[index - 1]?.length || 0));
  expect(increments[0]).toBeLessThanOrEqual(SMOOTH_STREAM_MAX_STEP);
  expect(increments[1]).toBeLessThanOrEqual(2);
});

test('commentary 与正文共用默认排空节奏', async () => {
  const scheduler = new FakeScheduler();
  const commits: string[] = [];
  const stream = createSmoothStreamText({
    scheduler,
    commit: (content) => commits.push(content),
  });
  const target = '这是一段需要平滑展开的阶段说明。';
  const finished = stream.finish(target);

  for (let i = 0; i < 8 && scheduler.pendingFrames; i += 1) scheduler.tick();
  expect(commits[commits.length - 1]).not.toBe(target);
  for (let i = 0; i < 80 && scheduler.pendingFrames; i += 1) scheduler.tick();
  await finished;

  expect(commits.length).toBeGreaterThan(10);
  expect(commits[commits.length - 1]).toBe(target);
});

test('语义纠正立即替换已有前缀，emoji 不会被切成半个代理项', () => {
  const scheduler = new FakeScheduler();
  const commits: string[] = [];
  const stream = createSmoothStreamText({ scheduler, commit: (content) => commits.push(content) });

  stream.push('A😀BC');
  for (let i = 0; i < 10 && scheduler.pendingFrames; i += 1) scheduler.tick();
  expect(commits.some((content) => /[\uD800-\uDBFF]$/.test(content))).toBe(false);

  stream.reset('修正后的正文');
  expect(commits[commits.length - 1]).toBe('修正后的正文');
});

test('后台页签或减弱动画直接同步权威全文', () => {
  const scheduler = new FakeScheduler();
  scheduler.immediate = true;
  const commits: string[] = [];
  const stream = createSmoothStreamText({ scheduler, commit: (content) => commits.push(content) });

  stream.push('完整输出');

  expect(commits).toEqual(['完整输出']);
  expect(scheduler.pendingFrames).toBe(0);
});
