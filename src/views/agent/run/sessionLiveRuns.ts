import { computed, ref, type Ref } from 'vue';

/** 同一智能体下可并行的会话运行：切到新对话不 abort 旧流。 */
export type SessionLiveRun<TMessage, TInteractive = unknown> = {
  abort: AbortController;
  messages: TMessage[];
  nodeLabel: string;
  view: number;
  interactive: TInteractive | null;
};

export function createSessionLiveRuns<TMessage, TInteractive = unknown>(
  activeSessionId: Ref<string>,
) {
  const view = ref(0);
  const liveIds = ref<string[]>([]);
  const runs = new Map<string, SessionLiveRun<TMessage, TInteractive>>();
  const startingViews = ref<number[]>([]);

  const running = computed(() => {
    const id = activeSessionId.value;
    if (id) return liveIds.value.includes(id) && !runs.get(id)?.interactive;
    return startingViews.value.includes(view.value);
  });
  const runningIds = computed(() => liveIds.value.filter((id) => !runs.get(id)?.interactive));

  function nextView() {
    view.value += 1;
    return view.value;
  }

  function isBusy(sessionId: string, viewAtStart = view.value) {
    if (sessionId) {
      const rec = runs.get(sessionId);
      return Boolean(rec && !rec.interactive);
    }
    return startingViews.value.includes(viewAtStart);
  }

  function markStarting(viewId: number) {
    if (!startingViews.value.includes(viewId)) startingViews.value = [...startingViews.value, viewId];
  }

  function clearStarting(viewId: number) {
    startingViews.value = startingViews.value.filter((item) => item !== viewId);
  }

  function setInteractive(sessionId: string, value: TInteractive | null) {
    const rec = runs.get(sessionId);
    if (!rec) return;
    rec.interactive = value;
    liveIds.value = [...runs.keys()];
  }

  function start(sessionId: string, rec: SessionLiveRun<TMessage, TInteractive>) {
    runs.set(sessionId, rec);
    liveIds.value = [...runs.keys()];
  }

  function get(sessionId: string) {
    return runs.get(sessionId);
  }

  function finish(sessionId: string) {
    const rec = runs.get(sessionId);
    runs.delete(sessionId);
    liveIds.value = [...runs.keys()];
    return rec;
  }

  function abortSession(sessionId: string) {
    runs.get(sessionId)?.abort.abort();
  }

  function abortAll() {
    for (const rec of runs.values()) rec.abort.abort();
  }

  return {
    view,
    liveIds,
    runningIds,
    running,
    nextView,
    isBusy,
    markStarting,
    clearStarting,
    start,
    get,
    setInteractive,
    finish,
    abortSession,
    abortAll,
  };
}
