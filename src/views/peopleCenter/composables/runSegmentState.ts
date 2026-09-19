/**
 * 主对话 Run「显示段」权威状态（P0 架构收口）。
 *
 * 原先 segmentContentOffset / streamContent / assistantTargetId / typewriter /
 * runSegmentControllers 分散在 useCenterChat 多个闭包里，插话/断流时容易双写。
 * 这里把「当前段写到哪条消息、offset 多少、全文多少、是否已封存」收成一份纯逻辑，
 * 三个入口（发送流 / resume 流 / 订阅流）共用同一套 split 规则。
 *
 * 不依赖 Vue；调用方仍持有消息数组与打字机实现。
 */

// ChatMessage 类型在 useCenterChat 内定义——避免循环依赖，这里用最小结构
export type SegmentMessage = {
  id: number;
  role: string;
  content: string;
  runId?: string;
  executionSegmentEndedAt?: number;
  executionSegmentEndSequence?: number;
  executionSegmentIndex?: number;
  executionSegmentStartSequence?: number;
  executionSegmentInputId?: string;
  runStartedAt?: number;
  generatedFiles?: Array<{ id?: string; [key: string]: unknown }>;
  agentSteps?: Array<{
    kind?: string;
    name?: string;
    callId?: string;
    status?: string;
    text?: string;
    files?: Array<{ id?: string; [key: string]: unknown }>;
    [key: string]: unknown;
  }>;
  toolSteps?: Array<{ name?: string; callId?: string; status?: string }>;
  [key: string]: unknown;
};

/** 插话切段正文切点：优先吸附 Markdown 块边界；无边界时整段定稿。 */
export function segmentSplitOffset(full: string, prevStart: number): number {
  const start = Math.max(0, prevStart);
  if (full.length <= start) return start;
  const boundary = full.lastIndexOf('\n\n');
  if (boundary > start) return boundary + 2;
  return full.length;
}

export function isSealedAssistantSegment(message: SegmentMessage | undefined | null): boolean {
  return Boolean(message && message.role === 'assistant' && message.executionSegmentEndedAt);
}

/**
 * 切段钩子：刻意不把 running 工具强改 completed。
 * 终态事件由 routeToolEventToRun 回落到封存前段。
 */
export function sealSegmentSteps(_message: SegmentMessage | undefined | null): void {
  // intentionally empty
}

/** 工具终态优先落到同 Run 已封存段上仍 running 的同名步骤。 */
export function routeToolEventToRun<T extends SegmentMessage>(
  messages: T[],
  runId: string | undefined,
  currentMessageId: number,
  ev: { name?: string; callId?: string; phase?: string },
  apply: (target: T) => void,
): boolean {
  if (!runId) return false;
  const phase = String(ev.phase || '');
  if (phase === 'started' || phase === 'progress') return false;
  const toolName = String(ev.name || '');
  if (!toolName) return false;
  const sealed = [...messages].reverse().find((m) => {
    if (m.role !== 'assistant' || m.runId !== runId || m.id === currentMessageId) return false;
    if (!m.executionSegmentEndedAt) return false;
    return (m.agentSteps || []).some(
      (s) => s.kind === 'tool'
        && s.status === 'running'
        && (ev.callId ? s.callId === ev.callId : s.name === toolName),
    );
  });
  if (!sealed) return false;
  apply(sealed);
  return true;
}

/**
 * artifact.saved 是 Run 级交付事实，不是某条 SSE 观察流的私有事件。
 * Plan 确认续接或断流交接的旧观察者即使晚到，同一 file_id 也只能归属
 * 当前 Run 最后一条助手消息。路由前先清理其他分段中的同一产物，从而同时修复
 * 「两条流双写」和「历史回放已带重复卡」。
 */
export function routeArtifactEventToRun<T extends SegmentMessage>(
  messages: T[],
  runId: string | undefined,
  _requestedMessageId: number,
  files: Array<{ id?: string }>,
  apply: (target: T) => void,
): boolean {
  if (!runId) return false;
  const fileIds = new Set(
    (files || []).map((file) => String(file?.id || '')).filter(Boolean),
  );
  if (!fileIds.size) return false;
  const sameRun = messages.filter(
    (message) => message.role === 'assistant' && message.runId === runId,
  );
  const owner = sameRun[sameRun.length - 1];
  if (!owner) return false;

  for (const message of sameRun) {
    if (message === owner) continue;
    if (message.generatedFiles?.length) {
      message.generatedFiles = message.generatedFiles.filter(
        (file) => !fileIds.has(String(file?.id || '')),
      );
    }
    if (message.agentSteps?.length) {
      message.agentSteps = message.agentSteps.flatMap((step) => {
        if (step.kind !== 'artifact' || !step.files?.length) return [step];
        const remaining = step.files.filter(
          (file) => !fileIds.has(String(file?.id || '')),
        );
        return remaining.length ? [{ ...step, files: remaining }] : [];
      });
    }
  }
  apply(owner);
  return true;
}

export type TypewriterHandle = {
  push: (next: string) => void;
  reset: (next: string) => void;
  finish: (next: string) => Promise<void>;
  stop: () => void;
};

export type RunSegmentSessionOpts = {
  runId: string;
  initialAssistantId: number;
  /** 可选：订阅续传时的正文起点 */
  initialContentOffset?: number;
  /** 可选：订阅续传时已有全文 */
  initialStreamContent?: string;
  nextLocalId: () => number;
  stripRecommendMark: (text: string) => string;
  createTypewriter: (messageId: number) => TypewriterHandle;
  findMessage: (id: number) => SegmentMessage | undefined;
  patchMessage: (id: number, patch: Partial<SegmentMessage>) => void;
  pushMessage: (message: SegmentMessage) => void;
  trackCursor?: (patch: {
    messageId: number;
    contentOffset?: number;
    content?: string;
    seq?: number;
  }) => void;
  /** 切段后是否把 typewriter 登记为「当前活跃打字机」 */
  onTypewriterReplaced?: (tw: TypewriterHandle) => void;
};

/**
 * 一份 Run 显示段会话：持有 streamContent / offset / 当前助手气泡 id / 打字机。
 * split() 与三条业务路径（发送/resume/订阅）规则一致。
 */
export function createRunSegmentSession(opts: RunSegmentSessionOpts) {
  let assistantId = opts.initialAssistantId;
  let contentOffset = Math.max(0, Number(opts.initialContentOffset) || 0);
  let streamContent = String(opts.initialStreamContent || '');
  let lastSeq = 0;
  let typewriter = opts.createTypewriter(assistantId);
  opts.onTypewriterReplaced?.(typewriter);

  const api = {
    get assistantId() {
      return assistantId;
    },
    get contentOffset() {
      return contentOffset;
    },
    get streamContent() {
      return streamContent;
    },
    get lastSeq() {
      return lastSeq;
    },
    get typewriter() {
      return typewriter;
    },
    setLastSeq(seq: number) {
      lastSeq = seq;
    },
    setStreamContent(full: string) {
      streamContent = full;
    },
    /** 增量正文（已是累计全文）→ 按 offset 切片推打字机 */
    onDelta(fullContent: string) {
      streamContent = fullContent;
      typewriter.push(opts.stripRecommendMark(fullContent.slice(contentOffset)));
      opts.trackCursor?.({
        messageId: assistantId,
        content: fullContent,
        contentOffset,
      });
    },
    /** commentary 剔除后的全文：打字机 reset + 记游标 */
    onCommentaryBody(strippedContent: string) {
      streamContent = strippedContent;
      typewriter.reset(opts.stripRecommendMark(strippedContent.slice(contentOffset)));
      opts.trackCursor?.({
        messageId: assistantId,
        content: strippedContent,
        contentOffset,
      });
    },
    /**
     * 插话切段：封存当前段、新开助手气泡、offset 推到切点。
     * 返回新段 messageId。
     */
    split(): number {
      typewriter.stop();
      const previous = opts.findMessage(assistantId);
      const endedAt = Date.now();
      if (previous) {
        opts.patchMessage(previous.id, {
          executionSegmentEndedAt: endedAt,
          executionSegmentEndSequence: lastSeq || undefined,
        });
        sealSegmentSteps(previous);
      }
      const nextId = opts.nextLocalId();
      const nextSegment: SegmentMessage = {
        id: nextId,
        role: 'assistant',
        content: '',
        runId: opts.runId,
        executionSegmentIndex: (previous?.executionSegmentIndex ?? 0) + 1,
        executionSegmentStartSequence: lastSeq ? lastSeq + 1 : undefined,
        runStartedAt: previous?.runStartedAt || endedAt,
      };
      const cut = segmentSplitOffset(streamContent, contentOffset);
      const sealedContent = opts.stripRecommendMark(
        streamContent.slice(contentOffset, cut),
      );
      if (previous) {
        opts.patchMessage(previous.id, { content: sealedContent });
      }
      opts.pushMessage(nextSegment);
      assistantId = nextId;
      contentOffset = cut;
      typewriter = opts.createTypewriter(assistantId);
      opts.onTypewriterReplaced?.(typewriter);
      opts.trackCursor?.({
        messageId: assistantId,
        contentOffset,
      });
      return assistantId;
    },
    /** 终态正文（累计全文）→ 只取本段切片 */
    segmentAnswer(fullAnswer: string): string {
      return opts.stripRecommendMark(fullAnswer.slice(contentOffset));
    },
    /**
     * 末段是否允许空正文（插话后无新增量）：不写「模型未返回内容」。
     */
    emptySegmentOk(message: SegmentMessage | undefined): boolean {
      return Boolean(
        message
        && (message.executionSegmentIndex || 0) > 0
        && !String(message.content || '').trim()
        && !message.agentSteps?.some((s) => s.kind === 'tool')
        && !message.toolSteps?.length,
      );
    },
    stopTypewriter() {
      typewriter.stop();
    },
  };
  return api;
}

export type RunSegmentSession = ReturnType<typeof createRunSegmentSession>;
