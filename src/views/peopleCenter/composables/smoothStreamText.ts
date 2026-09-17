import { cancelStreamAnimationFrame, requestStreamAnimationFrame } from './streamAnimationFrame';

export const SMOOTH_STREAM_FRAME_MS = 1000 / 60;
export const SMOOTH_STREAM_MAX_STEP = 5;
// 正文与公开叙述共用同一档视觉排空节奏；避免 commentary 平滑、终答却突然加速。
export const SMOOTH_STREAM_FINISH_MIN_MS = 700;
export const SMOOTH_STREAM_FINISH_MAX_MS = 2000;

const BASE_CHARS_PER_SECOND = 32;
const BACKLOG_RATE_GAIN = 0.55;
const MAX_CHARS_PER_SECOND = 120;

export type SmoothStreamScheduler = {
  requestFrame: (callback: FrameRequestCallback) => number;
  cancelFrame: (frame: number) => void;
  now: () => number;
  shouldRenderImmediately: () => boolean;
};

export type SmoothStreamTextHandle = {
  push: (nextContent: string) => void;
  reset: (nextContent: string) => void;
  finish: (nextContent: string) => Promise<void>;
  finalize: (nextContent: string) => void;
  stop: () => void;
};

export type SmoothStreamTextOptions = {
  initialContent?: string;
  commit: (content: string) => void;
  scheduler?: SmoothStreamScheduler;
  /** 已在服务端缓冲整合的报告直接提交，不能再等待逐字播放。 */
  shouldRenderImmediately?: () => boolean;
  /** 已收到完整权威文本时的最短/最长视觉排空窗口。 */
  finishMinMs?: number;
  finishMaxMs?: number;
};

function defaultScheduler(): SmoothStreamScheduler {
  return {
    requestFrame: requestStreamAnimationFrame,
    cancelFrame: cancelStreamAnimationFrame,
    now: () => (typeof performance !== 'undefined' ? performance.now() : Date.now()),
    shouldRenderImmediately: () => (
      typeof window === 'undefined'
      || typeof document === 'undefined'
      || document.hidden
      || (
        typeof window.matchMedia === 'function'
        && window.matchMedia('(prefers-reduced-motion: reduce)').matches
      )
    ),
  };
}

function safeSliceEnd(content: string, start: number, step: number): number {
  let end = Math.min(content.length, start + Math.max(1, step));
  if (end >= content.length || end <= start) return end;
  const before = content.charCodeAt(end - 1);
  const after = content.charCodeAt(end);
  if (before >= 0xd800 && before <= 0xdbff && after >= 0xdc00 && after <= 0xdfff) {
    end += 1;
  }
  return end;
}

function finishDuration(backlog: number, minMs: number, maxMs: number): number {
  return Math.min(
    maxMs,
    Math.max(
      minMs,
      Math.ceil(backlog / SMOOTH_STREAM_MAX_STEP) * SMOOTH_STREAM_FRAME_MS,
    ),
  );
}

/**
 * 主 Agent 与子智能体共用的视觉流式节奏器。
 * push 接收网络侧累计全文；commit 只在视觉帧提交逐步追赶后的全文。
 */
export function createSmoothStreamText(options: SmoothStreamTextOptions): SmoothStreamTextHandle {
  const scheduler = options.scheduler || defaultScheduler();
  const shouldRenderImmediately = () => Boolean(options.shouldRenderImmediately?.()) || scheduler.shouldRenderImmediately();
  const finishMinMs = Math.max(0, options.finishMinMs ?? SMOOTH_STREAM_FINISH_MIN_MS);
  const finishMaxMs = Math.max(finishMinMs, options.finishMaxMs ?? SMOOTH_STREAM_FINISH_MAX_MS);
  let currentContent = String(options.initialContent || '');
  let targetContent = currentContent;
  let frame: number | null = null;
  let lastPaintAt: number | null = null;
  let characterCredit = 0;
  let alive = true;
  let finishing = false;
  let finishDeadline = 0;
  const waiters: Array<() => void> = [];

  const resolveWaiters = () => {
    while (waiters.length) waiters.shift()?.();
  };

  const cancelFrame = () => {
    if (frame == null) return;
    scheduler.cancelFrame(frame);
    frame = null;
  };

  const commit = (content: string) => {
    if (content === currentContent) return;
    currentContent = content;
    options.commit(content);
  };

  const settleImmediately = () => {
    cancelFrame();
    commit(targetContent);
    finishing = false;
    lastPaintAt = null;
    characterCredit = 0;
    resolveWaiters();
  };

  function schedule() {
    if (!alive) return;
    if (shouldRenderImmediately()) {
      settleImmediately();
      return;
    }
    if (frame != null) return;
    frame = scheduler.requestFrame(paint);
  }

  function paint(now: number) {
    frame = null;
    if (!alive) {
      resolveWaiters();
      return;
    }
    if (shouldRenderImmediately()) {
      settleImmediately();
      return;
    }

    const elapsedSincePaint = lastPaintAt == null ? SMOOTH_STREAM_FRAME_MS : now - lastPaintAt;
    if (lastPaintAt != null && elapsedSincePaint < SMOOTH_STREAM_FRAME_MS - 0.5) {
      schedule();
      return;
    }

    // commentary 归属修正、断线快照回退等会改变已有前缀；语义纠正不做倒放动画。
    if (!targetContent.startsWith(currentContent)) {
      commit(targetContent);
      characterCredit = 0;
    } else {
      const backlog = targetContent.length - currentContent.length;
      if (backlog > 0) {
        let step: number;
        if (finishing) {
          const remainingMs = Math.max(SMOOTH_STREAM_FRAME_MS, finishDeadline - now);
          const targetRate = Math.min(
            MAX_CHARS_PER_SECOND,
            Math.max(BASE_CHARS_PER_SECOND, backlog * 1000 / remainingMs),
          );
          // 权威全文到齐后也按时间积分吐字：短 commentary 和长终答
          // 共用同一速率上限。主线程停顿期间不累积无上限“欠字”：恢复时最多
          // 补一个小步，避免 Markdown 重排/切页后连续满速追赶。
          const elapsed = Math.min(50, Math.max(SMOOTH_STREAM_FRAME_MS, elapsedSincePaint));
          characterCredit = Math.min(
            SMOOTH_STREAM_MAX_STEP,
            characterCredit + targetRate * elapsed / 1000,
          );
          step = Math.min(SMOOTH_STREAM_MAX_STEP, Math.max(1, Math.floor(characterCredit)));
          characterCredit = Math.max(0, characterCredit - step);
        } else {
          const elapsed = Math.min(50, Math.max(SMOOTH_STREAM_FRAME_MS, elapsedSincePaint));
          const rate = Math.min(MAX_CHARS_PER_SECOND, BASE_CHARS_PER_SECOND + backlog * BACKLOG_RATE_GAIN);
          characterCredit += rate * elapsed / 1000;
          step = Math.min(SMOOTH_STREAM_MAX_STEP, Math.max(1, Math.floor(characterCredit)));
          characterCredit = Math.max(0, characterCredit - step);
        }
        const end = safeSliceEnd(targetContent, currentContent.length, step);
        commit(targetContent.slice(0, end));
      }
    }
    lastPaintAt = now;

    if (currentContent !== targetContent) {
      schedule();
      return;
    }
    finishing = false;
    characterCredit = 0;
    resolveWaiters();
  }

  function startFinishing(nextContent: string) {
    targetContent = String(nextContent || '');
    if (currentContent === targetContent) {
      finishing = false;
      resolveWaiters();
      return false;
    }
    if (shouldRenderImmediately() || !targetContent.startsWith(currentContent)) {
      settleImmediately();
      return false;
    }
    finishing = true;
    characterCredit = 0;
    finishDeadline = scheduler.now() + finishDuration(
      targetContent.length - currentContent.length,
      finishMinMs,
      finishMaxMs,
    );
    schedule();
    return true;
  }

  return {
    push(nextContent: string) {
      if (!alive) return;
      targetContent = String(nextContent || '');
      schedule();
    },
    reset(nextContent: string) {
      if (!alive) return;
      targetContent = String(nextContent || '');
      if (!targetContent.startsWith(currentContent)) settleImmediately();
      else schedule();
    },
    finish(nextContent: string) {
      if (!alive) return Promise.resolve();
      if (!startFinishing(nextContent)) return Promise.resolve();
      return new Promise<void>((resolve) => waiters.push(resolve));
    },
    finalize(nextContent: string) {
      if (!alive) return;
      startFinishing(nextContent);
    },
    stop() {
      alive = false;
      cancelFrame();
      resolveWaiters();
    },
  };
}
