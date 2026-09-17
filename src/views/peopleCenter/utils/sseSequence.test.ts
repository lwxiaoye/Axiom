import { createSequenceGate } from './sseSequence';

describe('createSequenceGate（SSE v1 去重/防乱序闸）', () => {
  it('严格递增放行', () => {
    const accept = createSequenceGate();
    expect(accept(1)).toBe(true);
    expect(accept(2)).toBe(true);
    expect(accept(5)).toBe(true); // 允许跳号（中间事件可能被过滤）
  });

  it('重复 sequence 丢弃（断线重连回放不重复渲染）', () => {
    const accept = createSequenceGate();
    accept(3);
    expect(accept(3)).toBe(false);
  });

  it('乱序到达的旧事件丢弃，不回退状态', () => {
    const accept = createSequenceGate();
    accept(10);
    expect(accept(7)).toBe(false);
    expect(accept(11)).toBe(true);
  });

  it('无 sequence 的事件（legacy/keep-alive）一律放行', () => {
    const accept = createSequenceGate();
    accept(10);
    expect(accept(undefined)).toBe(true);
    expect(accept('x')).toBe(true);
    expect(accept(NaN)).toBe(true);
  });

  it('游标续传（after=N 重连）：以 N 为起点，拦掉服务端可能重发的 ≤N 旧事件', () => {
    const accept = createSequenceGate(42);
    expect(accept(41)).toBe(false);
    expect(accept(42)).toBe(false); // 游标本身也是已消费事件
    expect(accept(43)).toBe(true);
  });

  it('初值缺省/非法时回退全量语义（从 0 起）', () => {
    expect(createSequenceGate()(1)).toBe(true);
    expect(createSequenceGate(-5)(1)).toBe(true);
  });
});

it('reports a forward sequence gap exactly when the cursor skips', () => {
  const gaps: Array<{ expected: number; received: number }> = [];
  const accept = createSequenceGate(3, (gap) => gaps.push(gap));

  expect(accept(5)).toBe(true);
  expect(accept(4)).toBe(false);
  expect(accept(6)).toBe(true);
  expect(gaps).toEqual([{ expected: 4, received: 5 }]);
});
