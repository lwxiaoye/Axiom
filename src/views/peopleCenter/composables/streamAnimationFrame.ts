export const STREAM_FRAME_FALLBACK_MS = 120;

type PendingStreamFrame = {
  frame: number | null;
  timer: number | null;
  onVisibilityChange: () => void;
};

let sequence = 0;
const pendingFrames = new Map<number, PendingStreamFrame>();

function now() {
  return typeof performance !== 'undefined' ? performance.now() : Date.now();
}

export function cancelStreamAnimationFrame(id: number) {
  const pending = pendingFrames.get(id);
  if (!pending) return;
  pendingFrames.delete(id);
  if (pending.frame != null) window.cancelAnimationFrame(pending.frame);
  if (pending.timer != null) window.clearTimeout(pending.timer);
  document.removeEventListener('visibilitychange', pending.onVisibilityChange);
}

/** Paint is optional; a withheld animation frame must not block the message stream. */
export function requestStreamAnimationFrame(callback: FrameRequestCallback): number {
  const id = ++sequence;
  const deliver = (timestamp = now()) => {
    if (!pendingFrames.has(id)) return;
    cancelStreamAnimationFrame(id);
    callback(timestamp);
  };
  const pending: PendingStreamFrame = {
    frame: null,
    timer: null,
    onVisibilityChange: () => {
      if (document.hidden) deliver();
    },
  };
  pendingFrames.set(id, pending);
  pending.frame = window.requestAnimationFrame(deliver);
  pending.timer = window.setTimeout(deliver, STREAM_FRAME_FALLBACK_MS);
  document.addEventListener('visibilitychange', pending.onVisibilityChange);
  return id;
}

/** Allow two normal paints without holding SSE consumption hostage to rendering. */
export function waitForStreamPaint(): Promise<void> {
  if (
    typeof window === 'undefined'
    || typeof document === 'undefined'
    || document.hidden
    || (typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  ) {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    requestStreamAnimationFrame(() => {
      if (document.hidden) resolve();
      else requestStreamAnimationFrame(() => resolve());
    });
  });
}
