import {
  applyCommentary,
  applyReasoningDelta,
  parkTransientReasoning,
  type ExecutionMessage,
} from './executionTimeline';
import { createSmoothStreamText } from './smoothStreamText';

type MessageUpdater<T extends ExecutionMessage> = (fn: (target: T) => void) => void;

export type CommentaryStream = {
  show: (text: string, kind?: string) => Promise<void>;
  stop: () => void;
};

export type ReasoningStream = {
  push: (delta: string, fullReasoning: string) => void;
  complete: (payload?: { text?: string; seconds?: number }) => Promise<void>;
  stop: () => void;
};

let commentaryStreamSequence = 0;

/** 公开叙述仍平滑显示，但视觉追赶不占用 SSE 的事件消费队列。 */
export function createCommentaryStream<T extends ExecutionMessage>(
  updater: MessageUpdater<T>,
): CommentaryStream {
  const liveStreams = new Map<string, { flush: () => void }>();
  let alive = true;

  function flush() {
    for (const stream of liveStreams.values()) stream.flush();
    liveStreams.clear();
  }

  return {
    show(text: string, kind = '') {
      const clean = String(text || '').trim();
      if (!alive || !clean) return Promise.resolve();
      // 后续 commentary 的归属和去重必须基于上一条权威全文，而非未播完的子串。
      flush();
      const streamKey = `commentary-${++commentaryStreamSequence}`;
      let destination: 'preamble' | 'note' | '' = '';

      updater((target) => {
        const previousPreamble = String(target.preamble || '');
        const previousStepCount = target.agentSteps?.length || 0;
        const previousPlanReport = String(target.planReport || '');
        applyCommentary(target, clean, kind);

        if (kind === 'initial_progress' || String(target.planReport || '') !== previousPlanReport) return;
        if (String(target.preamble || '') !== previousPreamble && String(target.preamble || '').trim() === clean) {
          target.preambleStreamKey = streamKey;
          target.preamble = '';
          destination = 'preamble';
          return;
        }
        const steps = target.agentSteps || [];
        const inserted = steps.length > previousStepCount ? steps[steps.length - 1] : undefined;
        if (inserted?.kind === 'note' && String(inserted.text || '').trim() === clean) {
          inserted.liveStreamKey = streamKey;
          inserted.text = '';
          destination = 'note';
        }
      });

      if (!destination) return Promise.resolve();
      const commit = (visibleText: string) => {
        updater((target) => {
          if (destination === 'preamble') {
            if (target.preambleStreamKey !== streamKey) return;
            target.preamble = visibleText;
            if (visibleText === clean) delete target.preambleStreamKey;
            return;
          }
          const note = target.agentSteps?.find((step) => step.liveStreamKey === streamKey);
          if (!note || note.kind !== 'note') return;
          note.text = visibleText;
          if (visibleText === clean) delete note.liveStreamKey;
        });
      };
      // commentary 与最终正文共用 smoothStreamText 默认速度。
      const stream = createSmoothStreamText({ commit });
      liveStreams.set(streamKey, {
        flush() {
          stream.stop();
          commit(clean);
        },
      });
      void stream.finish(clean).finally(() => liveStreams.delete(streamKey));
      return Promise.resolve();
    },
    stop() {
      flush();
      alive = false;
    },
  };
}

/** 思考全文直接进入权威投影；展开时的动画只由 ThinkingReasoningStep 负责。 */
export function createReasoningStream<T extends ExecutionMessage>(
  updater: MessageUpdater<T>,
): ReasoningStream {
  let authoritative = '';
  let alive = true;

  return {
    push(delta: string, fullReasoning: string) {
      if (!alive) return;
      const previous = authoritative;
      authoritative = String(fullReasoning || `${previous}${delta || ''}`);
      if (authoritative === previous) return;
      updater((target) => {
        if (authoritative.startsWith(previous)) {
          applyReasoningDelta(target, authoritative.slice(previous.length), authoritative);
        } else {
          parkTransientReasoning(target);
          applyReasoningDelta(target, authoritative, authoritative);
        }
      });
    },
    async complete(payload) {
      if (!alive) return;
      updater((target) => parkTransientReasoning(target, {
        ...payload,
        text: payload?.text || authoritative,
      }));
      authoritative = '';
    },
    stop() {
      alive = false;
    },
  };
}
