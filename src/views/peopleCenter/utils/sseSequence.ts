/**
 * SSE v1 信封 sequence 闸（§15.4）：断线重连/回放会重复下发已消费的事件，
 * 乱序到达的旧事件也不能回退状态——只放行严格递增的 sequence。
 * 无 sequence 的事件（legacy/keep-alive）一律放行，由上层自行兜底。
 * initial：游标续传（?after=N 重连）时以 N 为起点，双保险拦掉服务端可能重发的旧事件。
 */
export function createSequenceGate(
  initial = 0,
  onGap?: (gap: { expected: number; received: number }) => void,
) {
  let lastSeq = initial > 0 ? initial : 0;
  return function accept(sequence: unknown): boolean {
    if (typeof sequence !== 'number' || Number.isNaN(sequence)) return true;
    if (sequence <= lastSeq) return false;
    if (lastSeq > 0 && sequence > lastSeq + 1) {
      onGap?.({ expected: lastSeq + 1, received: sequence });
    }
    lastSeq = sequence;
    return true;
  };
}
